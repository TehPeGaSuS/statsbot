"""
irc/pm_commands.py
Private message command handler.
All admin/management commands live here, dispatched via /msg statsbot.

Commands:
  identify <master_nick> <password>
  logout
  whoami
  status

  ignore add [#channel] <pattern>
  ignore del [#channel] <pattern>
  ignore list [#channel]

  master add <nick>         (bot will ask for password interactively via PM)
  master del <nick>
  master list

  set page [#channel] <url>
  rehash
"""

import logging
import time

log = logging.getLogger("pm_commands")


class PMCommandHandler:
    def __init__(self, network: str, auth_manager, send_fn, config: dict,
                  connectors: list = None):
        self.network = network
        self.auth = auth_manager
        self.send = send_fn          # send(nick, text) — sends a NOTICE or PRIVMSG to nick
        self.cfg = config
        self.connectors = connectors or []
        # Pending master add flows: {nick_lower: {"step": 1, "target": master_nick}}
        self._pending_master_add: dict = {}

    def dispatch(self, nick: str, host: str, text: str):
        """Entry point for all PM messages."""
        text = text.strip()
        if not text:
            return

        # Check if we're mid-flow (e.g. waiting for a password for master add)
        if nick.lower() in self._pending_master_add:
            self._handle_pending(nick, host, text)
            return

        parts = text.split(None, 1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        handlers = {
            "identify": lambda: self._cmd_identify(nick, host, args),
            "logout":   lambda: self._cmd_logout(nick),
            "whoami":   lambda: self._cmd_whoami(nick),
            "status":   lambda: self._cmd_status(nick),
            "ignore":   lambda: self._cmd_ignore(nick, args),
            "master":   lambda: self._cmd_master(nick, args),
            "set":      lambda: self._cmd_set(nick, args),
            "rehash":   lambda: self._cmd_rehash(nick),
            "help":     lambda: self._cmd_help(nick),
            "addchan":  lambda: self._cmd_addchan(nick, args),
            "delchan":  lambda: self._cmd_delchan(nick, args),
            "addnet":   lambda: self._cmd_addnet(nick, args),
            "delnet":   lambda: self._cmd_delnet(nick, args),
            "reload":   lambda: self._cmd_reload(nick),
            "nets":     lambda: self._cmd_nets(nick),
            "chans":    lambda: self._cmd_chans(nick),
            "setlang":  lambda: self._cmd_setlang(nick, args),
        }

        handler = handlers.get(cmd)
        if handler:
            try:
                handler()
            except Exception as e:
                log.error(f"PM command error from {nick}: {e}", exc_info=True)
                self.send(nick, "Internal error. Check bot logs.")
        else:
            self.send(nick, f"Unknown command: {cmd}. Try: help")

    # ─── Auth commands ────────────────────────────────────────────────────────

    def _cmd_identify(self, nick: str, host: str, args: str):
        parts = args.split(None, 1)
        if len(parts) < 2:
            self.send(nick, "Usage: identify <master_nick> <password>")
            return
        master_nick, password = parts[0], parts[1]
        ok, msg = self.auth.identify(self.network, nick, host, master_nick, password)
        self.send(nick, msg)

    def _cmd_logout(self, nick: str):
        if self.auth.is_authed(self.network, nick):
            self.auth.destroy_session(self.network, nick)
            self.send(nick, "Logged out.")
        else:
            self.send(nick, "You are not identified.")

    def _cmd_whoami(self, nick: str):
        session = self.auth.get_session(self.network, nick)
        if session:
            self.send(nick, f"You are identified as {session['master']} on {self.network}.")
        else:
            self.send(nick, "You are not identified. Use: identify <master_nick> <password>")

    # ─── Status ───────────────────────────────────────────────────────────────

    def _cmd_status(self, nick: str):
        if not self.auth.is_authed(self.network, nick):
            self.send(nick, "Not identified.")
            return
        from database.models import get_channels, count_users
        lines = [f"ircstats — network: {self.network}"]
        for conn in self.connectors:
            chans = conn._channel_members
            for chan, members in chans.items():
                users_db = count_users(conn.network, chan)
                lines.append(f"  {chan}: {len(members)} online, {users_db} tracked")
        for line in lines:
            self.send(nick, line)

    # ─── Ignore commands ──────────────────────────────────────────────────────

    def _cmd_ignore(self, nick: str, args: str):
        if not self.auth.is_authed(self.network, nick):
            self.send(nick, "Not identified. Use: identify <master_nick> <password>")
            return

        parts = args.split()
        if not parts:
            self.send(nick, "Usage: ignore add|del|list [#channel] <pattern>")
            return

        subcmd = parts[0].lower()

        if subcmd == "list":
            channel = parts[1] if len(parts) > 1 and parts[1].startswith("#") else None
            self._ignore_list(nick, channel)
            return

        if subcmd in ("add", "del"):
            rest = parts[1:]
            if not rest:
                self.send(nick, f"Usage: ignore {subcmd} [#channel] <pattern>")
                return
            # If first arg starts with #, it's a channel
            if rest[0].startswith("#"):
                if len(rest) < 2:
                    self.send(nick, f"Usage: ignore {subcmd} #channel <pattern>")
                    return
                channel = rest[0]
                pattern = rest[1]
            else:
                channel = "*"   # network-wide
                pattern = rest[0]

            if subcmd == "add":
                self._ignore_add(nick, channel, pattern)
            else:
                self._ignore_del(nick, channel, pattern)
            return

        self.send(nick, "Usage: ignore add|del|list [#channel] <pattern>")

    def _ignore_add(self, nick: str, channel: str, pattern: str):
        from database.models import add_ignore
        add_ignore(pattern, self.network, channel=channel, added_by=nick)
        scope = channel if channel != "*" else "network-wide"
        self.send(nick, f"Ignored {pattern} ({scope}).")

    def _ignore_del(self, nick: str, channel: str, pattern: str):
        from database.models import del_ignore
        del_ignore(pattern, self.network, channel=channel)
        self.send(nick, f"Removed ignore: {pattern}.")

    def _ignore_list(self, nick: str, channel: str = None):
        from database.models import list_ignores
        ignores = list_ignores(self.network, channel)
        if not ignores:
            self.send(nick, "Ignore list is empty.")
            return
        self.send(nick, f"Ignores for {self.network}" + (f"/{channel}" if channel else "") + ":")
        for ig in ignores:
            scope = ig["channel"] if ig["channel"] != "*" else "network-wide"
            self.send(nick, f"  [{scope}] {ig['pattern']}  (added by {ig['added_by'] or '?'})")

    # ─── Master commands ──────────────────────────────────────────────────────

    def _cmd_master(self, nick: str, args: str):
        if not self.auth.is_authed(self.network, nick):
            self.send(nick, "Not identified.")
            return

        parts = args.split()
        if not parts:
            self.send(nick, "Usage: master add|del|list [nick]")
            return

        subcmd = parts[0].lower()
        target = parts[1] if len(parts) > 1 else ""

        if subcmd == "list":
            from database.models import list_masters_global
            masters = list_masters_global()
            if not masters:
                self.send(nick, "No masters configured.")
            else:
                for m in masters:
                    masks = m.get("masks") or "(no masks)"
                    self.send(nick, f"  {m['nick']}  masks: {masks}")

        elif subcmd == "add":
            if not target:
                self.send(nick, "Usage: master add <nick>")
                return
            # Start interactive flow — ask for password via PM
            self._pending_master_add[nick.lower()] = {"target": target, "step": 1}
            self.send(nick, f"Adding master {target}. Enter password (will not be echoed):")

        elif subcmd == "del":
            if not target:
                self.send(nick, "Usage: master del <nick>")
                return
            from database.models import del_master_by_nick
            del_master_by_nick(target)
            self.send(nick, f"Removed master {target}.")

        else:
            self.send(nick, "Usage: master add|del|list [nick]")

    def _handle_pending(self, nick: str, host: str, text: str):
        """Handle multi-step flows (e.g. password input for master add)."""
        state = self._pending_master_add.get(nick.lower())
        if not state:
            return

        if state["step"] == 1:
            # First message after "master add" = password
            password = text.strip()
            if len(password) < 6:
                self.send(nick, "Password too short (min 6 chars). Try again or send 'cancel'.")
                return
            if password.lower() == "cancel":
                del self._pending_master_add[nick.lower()]
                self.send(nick, "Cancelled.")
                return
            state["password"] = password
            state["step"] = 2
            self.send(nick, "Confirm password:")

        elif state["step"] == 2:
            # Second message = confirmation
            confirm = text.strip()
            if confirm.lower() == "cancel":
                del self._pending_master_add[nick.lower()]
                self.send(nick, "Cancelled.")
                return
            if confirm != state["password"]:
                self.send(nick, "Passwords don't match. Start over with: master add <nick>")
                del self._pending_master_add[nick.lower()]
                return

            from database.models import add_master_with_password
            from bot.auth import hash_password
            hashed = hash_password(state["password"])
            add_master_with_password(state["target"], hashed, added_by=nick)
            del self._pending_master_add[nick.lower()]
            self.send(nick, f"Master {state['target']} added successfully.")
            log.info(f"Master {state['target']} added by {nick}")

    # ─── Set commands ─────────────────────────────────────────────────────────

    def _cmd_set(self, nick: str, args: str):
        if not self.auth.is_authed(self.network, nick):
            self.send(nick, "Not identified.")
            return

        parts = args.split(None, 2)
        if len(parts) < 2:
            self.send(nick, "Usage: set page [#channel] <url>")
            return

        key = parts[0].lower()
        if key == "page":
            rest = parts[1:]
            if rest[0].startswith("#"):
                if len(rest) < 2:
                    self.send(nick, "Usage: set page #channel <url>")
                    return
                channel, url = rest[0], rest[1]
            else:
                channel = "*"
                url = rest[0]
            from database.models import set_channel_config
            set_channel_config(self.network, channel, "stats_url", url)
            self.send(nick, f"Stats URL for {channel} set to {url}")
        else:
            self.send(nick, f"Unknown setting: {key}. Available: page")

    # ─── Rehash ───────────────────────────────────────────────────────────────

    def _cmd_rehash(self, nick: str):
        """Alias for reload."""
        self._cmd_reload(nick)

    # ─── Network / Channel management ────────────────────────────────────────

    def _require_auth(self, nick: str) -> bool:
        if not self.auth.is_authed(self.network, nick):
            self.send(nick, "Not identified. Use: identify <master_nick> <password>")
            return False
        return True

    def _post_event(self, event: dict) -> bool:
        """Post an event to the asyncio reload queue via any connector."""
        conn = next((c for c in self.connectors if c.network == self.network), None)
        q = getattr(conn, "reload_queue", None) if conn else None
        if q is None:
            return False
        try:
            q.put_nowait(event)
            return True
        except Exception:
            return False

    @staticmethod
    def _parse_flags(args: str) -> dict:
        """Parse -flag value style arguments.
        Boolean flags (-ssl, -plaintext) get value True.
        Returns {"flags": {name: value}, "positional": [list]}.
        """
        tokens = args.split()
        flags = {}
        positional = []
        i = 0
        while i < len(tokens):
            if tokens[i].startswith("-"):
                key = tokens[i][1:].lower()
                # Next token is value only if it doesn't start with -
                if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                    flags[key] = tokens[i + 1]
                    i += 2
                else:
                    flags[key] = True   # boolean flag
                    i += 1
            else:
                positional.append(tokens[i])
                i += 1
        return {"flags": flags, "positional": positional}

    def _cmd_addchan(self, nick: str, args: str):
        """addchan [-network <net>] #channel"""
        if not self._require_auth(nick): return
        parsed = self._parse_flags(args)
        network = parsed["flags"].get("network", self.network)
        chan = (parsed["positional"][0]
                if parsed["positional"]
                else parsed["flags"].get("channel", ""))
        if not chan or not chan.startswith("#"):
            self.send(nick, "Usage: addchan [-network <net>] #channel")
            return
        from database.models import add_channel
        add_channel(network, chan)
        self._post_event({"action": "add_channel", "network": network, "channel": chan})
        self.send(nick, f"Added and joining {chan} on {network}.")

    def _cmd_delchan(self, nick: str, args: str):
        """delchan [-network <net>] #channel"""
        if not self._require_auth(nick): return
        parsed = self._parse_flags(args)
        network = parsed["flags"].get("network", self.network)
        chan = (parsed["positional"][0]
                if parsed["positional"]
                else parsed["flags"].get("channel", ""))
        if not chan or not chan.startswith("#"):
            self.send(nick, "Usage: delchan [-network <net>] #channel")
            return
        from database.models import delete_channel
        delete_channel(network, chan)
        self._post_event({"action": "remove_channel", "network": network, "channel": chan})
        self.send(nick, f"Parted {chan} on {network} and deleted all its stats.")

    def _cmd_addnet(self, nick: str, args: str):
        """addnet -name <n> -host <host> -port <port> [-ssl|-plaintext]
        -ssl is the default. Use -plaintext to disable TLS."""
        if not self._require_auth(nick): return
        parsed = self._parse_flags(args)
        flags  = parsed["flags"]
        name     = flags.get("name", "")
        host     = flags.get("host", "")
        port_val = str(flags.get("port", ""))
        missing  = [f"-{f}" for f, v in [("name", name), ("host", host), ("port", port_val)]
                    if not v]
        if missing:
            self.send(nick, f"Missing required flag(s): {', '.join(missing)}")
            self.send(nick, "Usage: addnet -name <n> -host <host> -port <port> [-ssl|-plaintext]")
            return
        if not port_val.isdigit():
            self.send(nick, f"Invalid port: {port_val!r} — must be a number.")
            return
        port = int(port_val)
        if not (1 <= port <= 65535):
            self.send(nick, f"Port {port} out of range (1-65535).")
            return
        # Default to SSL unless -plaintext is explicitly given
        ssl = "plaintext" not in flags
        from database.models import add_network
        ok = add_network(name, host, port, ssl)
        if not ok:
            self.send(nick, f"Network {name!r} already exists.")
            return
        bot_cfg = self.cfg.get("bot", {})
        net_cfg = {
            "name": name, "host": host, "port": port, "ssl": ssl,
            "nick":     bot_cfg.get("nick",     "statsbot"),
            "altnick":  bot_cfg.get("altnick",  "statsbot_"),
            "ident":    bot_cfg.get("ident",    "statsbot"),
            "realname": bot_cfg.get("realname", "IRC Stats Bot"),
            "channels": [], "cmd_prefix": "!",
        }
        self._post_event({"action": "add_network", "net_cfg": net_cfg})
        ssl_tag = "SSL" if ssl else "plaintext"
        self.send(nick, f"Network {name} ({host}:{port} {ssl_tag}) added and connecting.")
        self.send(nick, f"Use: addchan -network {name} #channel  to start tracking.")

    def _cmd_delnet(self, nick: str, args: str):
        """delnet -name <n>"""
        if not self._require_auth(nick): return
        parsed = self._parse_flags(args)
        name = (parsed["flags"].get("name", "")
                or (parsed["positional"][0] if parsed["positional"] else ""))
        if not name:
            self.send(nick, "Usage: delnet -name <n>")
            return
        if name == self.network:
            self.send(nick, "Cannot delete the network you are currently connected on.")
            return
        from database.models import delete_network
        delete_network(name)
        self._post_event({"action": "remove_network", "name": name})
        self.send(nick, f"Network {name} removed and all its stats deleted.")

    def _cmd_setlang(self, nick: str, args: str):
        """setlang [-network <net>] #channel en_US|pt_PT|fr_FR|it_IT"""
        if not self._require_auth(nick): return
        from i18n import set_lang, SUPPORTED
        parsed = self._parse_flags(args)
        network = parsed["flags"].get("network", self.network)
        positional = parsed["positional"]
        # Expect: #channel lang  OR  lang #channel
        chan = next((p for p in positional if p.startswith("#")), "")
        lang = next((p for p in positional if not p.startswith("#")), "")
        if not chan or not lang:
            self.send(nick, f"Usage: setlang [-network <net>] #channel <lang>")
            self.send(nick, f"Supported: {", ".join(SUPPORTED)}")
            return
        if not set_lang(network, chan, lang):
            self.send(nick, f"Unsupported language {lang!r}. Supported: {", ".join(SUPPORTED)}")
            return
        self.send(nick, f"Language for {chan} on {network} set to {lang}.")

    def _cmd_reload(self, nick: str):
        if not self._require_auth(nick): return
        self.send(nick, "Changes apply immediately — no reload needed.")

    def _cmd_nets(self, nick: str):
        """List all networks in the DB."""
        if not self._require_auth(nick): return
        from database.models import get_all_networks
        nets = get_all_networks()
        if not nets:
            self.send(nick, "No networks in database.")
            return
        self.send(nick, f"Networks ({len(nets)}):")
        for n in nets:
            ssl_tag = " [SSL]" if n["ssl"] else ""
            status  = "" if n["enabled"] else " [DISABLED]"
            self.send(nick, f"  {n['name']} — {n['host']}:{n['port']}{ssl_tag}{status}")

    def _cmd_chans(self, nick: str):
        """List channels tracked on this network."""
        if not self._require_auth(nick): return
        from database.models import get_channels_for_network
        chans = get_channels_for_network(self.network, enabled_only=False)
        if not chans:
            self.send(nick, f"No channels tracked on {self.network}.")
            return
        self.send(nick, f"Channels on {self.network}: {' '.join(chans)}")

    # ─── Help ─────────────────────────────────────────────────────────────────

    def _cmd_help(self, nick: str):
        lines = [
            "ircstats PM commands:",
            "  identify <master_nick> <password>  — authenticate",
            "  logout  |  whoami  |  status",
            "  ignore add [#chan] <pattern>",
            "  ignore del [#chan] <pattern>",
            "  ignore list [#chan]",
            "  master add <nick>  |  master del <nick>  |  master list",
            "  set page [#chan] <url>",
            "  nets                                       — list all networks",
            "  setlang [-network <net>] #channel <lang>     — set channel language (en_US/pt_PT/fr_FR/it_IT)",
            "  chans                                      — list channels on this network",
            "  addchan [-network <net>] #channel          — join and track a channel",
            "  delchan [-network <net>] #channel          — part and delete channel stats",
            "  addnet -name <n> -host <host> -port <port> [-ssl|-plaintext]  (TLS by default)",
            "  delnet -name <n>                           — remove network and all stats",
        ]
        for line in lines:
            self.send(nick, line)
