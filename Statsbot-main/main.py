#!/usr/bin/env python3
"""
main.py — ircstats entry point.

Usage:
    python main.py [--config config/config.yml] [--web-only] [--init-db] [--setup]
"""

import asyncio
import argparse
import logging
import os
import sys
import threading

import yaml

# Global asyncio queue for runtime reload signals
# Web thread and PM commands post events here; the async loop consumes them
reload_queue: asyncio.Queue = None


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def setup_logging(config: dict):
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("file", "data/ircstats.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(log_file))
    except Exception:
        pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=handlers,
    )


def run_setup(db_path: str):
    """Interactive setup wizard — configure master nicks and passwords."""
    from database.models import add_master_with_password, list_masters_global
    from bot.auth import hash_password

    print("\nircstats setup wizard")
    print("=" * 40)
    existing = list_masters_global()
    if existing:
        print(f"Existing masters: {', '.join(m['pattern'] for m in existing)}")
        print()

    while True:
        nick = input("Add master nick (Enter to finish): ").strip()
        if not nick:
            break
        while True:
            import getpass
            pw = getpass.getpass(f"Password for {nick}: ")
            pw2 = getpass.getpass("Confirm password: ")
            if pw != pw2:
                print("Passwords don't match, try again.")
                continue
            if len(pw) < 6:
                print("Password too short (min 6 chars).")
                continue
            break
        masks = input(f"Host masks for {nick} (space-separated, or Enter for none): ").strip()
        hashed = hash_password(pw)
        add_master_with_password(nick, hashed, added_by="setup")
        # Store masks separately
        if masks:
            from database.models import get_conn
            with get_conn() as conn:
                conn.execute(
                    "UPDATE masters SET masks=? WHERE lower(pattern)=lower(?)",
                    (masks, nick)
                )
        print(f"Master {nick} configured.\n")

    print("Setup complete.")


def main():
    parser = argparse.ArgumentParser(description="IRC Stats Bot")
    parser.add_argument("--config", default="config/config.yml")
    parser.add_argument("--web-only", action="store_true")
    parser.add_argument("--init-db", action="store_true")
    parser.add_argument("--setup", action="store_true",
                        help="Configure master nicks and passwords")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config)
    log = logging.getLogger("main")

    db_path = config.get("database", {}).get("path", "data/stats.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    from database.models import init_db, set_db_path, seed_from_config
    set_db_path(db_path)
    init_db()
    seed_from_config(config)  # config.yml seeds DB on first run; DB wins on conflict

    if args.setup:
        run_setup(db_path)
        return

    if args.init_db:
        print("Database initialized.")
        return

    if args.web_only:
        from web.dashboard import run_dashboard
        run_dashboard(config, db_path)
        return

    from bot.sensors import Sensors
    from bot.auth import AuthManager
    from irc.commands import CommandHandler
    from irc.pm_commands import PMCommandHandler
    from bot.connector import IRCConnector
    from bot.scheduler import Scheduler
    from web.dashboard import run_dashboard, set_config, register_connector
    from database.models import get_enabled_networks, get_channels_for_network

    global reload_queue
    reload_queue = asyncio.Queue()

    db_networks = get_enabled_networks()
    if not db_networks:
        log.error("No enabled networks in database!")
        sys.exit(1)

    # Convert DB rows to the dict shape connectors expect
    def db_net_to_cfg(n: dict) -> dict:
        cfg = dict(n)
        cfg["channels"] = get_channels_for_network(n["name"])
        cfg["ssl"] = bool(n["ssl"])
        if n.get("sasl_user") and n.get("sasl_pass"):
            cfg["sasl"] = {"username": n["sasl_user"], "password": n["sasl_pass"]}
        if n.get("nickserv_pass"):
            cfg["nickserv_password"] = n["nickserv_pass"]
        return cfg

    networks = [db_net_to_cfg(n) for n in db_networks]

    # Shared auth manager — one instance handles all networks
    auth = AuthManager()

    connectors = []
    sensors_list = []
    scheduler = Scheduler(sensors_list, connectors, config)

    if config.get("web", {}).get("enabled", True):
        set_config(config, db_path)
        web_thread = threading.Thread(
            target=run_dashboard,
            args=(config, db_path),
            daemon=True,
            name="web-dashboard"
        )
        web_thread.start()
        log.info("Web dashboard thread started.")

    def make_connector(net_cfg: dict):
        """Instantiate a connector + all its handlers for a network dict."""
        network_name = net_cfg["name"]
        sensors = Sensors(config, network_name)
        sensors_list.append(sensors)
        _send_ref = [None]
        _pm_ref   = [None]
        cmd_h = CommandHandler(
            config, network_name,
            lambda ch, tx: _send_ref[0] and _send_ref[0](ch, tx),
            auth_manager=auth
        )
        pm_h = PMCommandHandler(
            network_name, auth,
            lambda nick, tx: _pm_ref[0] and _pm_ref[0](nick, tx),
            config
        )
        conn = IRCConnector(config, net_cfg, sensors, cmd_h, pm_handler=pm_h)
        conn.reload_queue = reload_queue
        _send_ref[0] = conn.send_msg
        _pm_ref[0]   = conn.send_notice
        pm_h.connectors = [conn]
        return conn

    async def run_all():
        active: dict = {}  # name -> (connector, task)

        async def start_connector(net_cfg: dict):
            conn = make_connector(net_cfg)
            connectors.append(conn)
            if config.get("web", {}).get("enabled", True):
                register_connector(conn)
            task = asyncio.create_task(
                auto_reconnect(conn), name=f"irc-{conn.network}"
            )
            active[net_cfg["name"]] = (conn, task)

        tasks = [asyncio.create_task(scheduler.run(), name="scheduler")]
        for net_cfg in networks:
            await start_connector(net_cfg)
            tasks.append(active[net_cfg["name"]][1])

        async def reload_consumer():
            """Watch reload_queue for add/remove/reload events from web or PM."""
            while True:
                event = await reload_queue.get()
                try:
                    action = event.get("action")
                    if action == "add_network":
                        net_cfg = event["net_cfg"]
                        if net_cfg["name"] not in active:
                            await start_connector(net_cfg)
                            log.info(f"Reload: connected new network {net_cfg['name']}")
                    elif action == "remove_network":
                        name = event["name"]
                        if name in active:
                            conn, task = active.pop(name)
                            task.cancel()
                            await conn.disconnect()
                            log.info(f"Reload: disconnected network {name}")
                    elif action == "add_channel":
                        name = event["network"]
                        chan = event["channel"]
                        if name in active:
                            await active[name][0].join_channel(chan)
                    elif action == "remove_channel":
                        name = event["network"]
                        chan = event["channel"]
                        if name in active:
                            await active[name][0].part_channel(chan)
                except Exception as e:
                    log.error(f"Reload consumer error: {e}", exc_info=True)

        tasks.append(asyncio.create_task(reload_consumer(), name="reload-consumer"))

        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            log.info("Shutting down...")
        finally:
            scheduler.stop()
            for c in connectors:
                await c.disconnect()

    async def auto_reconnect(connector: IRCConnector, delay: int = 30):
        while True:
            try:
                await connector.connect()
            except KeyboardInterrupt:
                break
            except Exception as e:
                log.error(f"Connection error for {connector.host}: {e}. Reconnecting in {delay}s...")
            await asyncio.sleep(delay)

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        log.info("Bye!")


if __name__ == "__main__":
    main()
