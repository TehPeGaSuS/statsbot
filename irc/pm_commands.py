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
import urllib.request
import urllib.parse

log = logging.getLogger("pm_commands")


class PMCommandHandler:
    def __init__(self, network: str, auth_manager, send_fn, config: dict,
                  connectors: list = None, config_path: str = None):
        self.network = network
        self.auth = auth_manager
        self.send = send_fn          # send(nick, text) — sends a NOTICE or PRIVMSG to nick
        self.cfg = config
        self.config_path = config_path   # path to config.yml, used by rehash
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
            rest = [p for p in parts[1:] if p != "--purge"]
            purge = "--purge" in parts
            if not rest:
                self.send(nick, f"Usage: ignore {subcmd} [#channel] <pattern> [--purge]")
                return
            # If first arg starts with #, it's a channel
            if rest[0].startswith("#"):
                if len(rest) < 2:
                    self.send(nick, f"Usage: ignore {subcmd} #channel <pattern> [--purge]")
                    return
                channel = rest[0]
                pattern = rest[1]
            else:
                channel = "*"   # network-wide
                pattern = rest[0]

            if subcmd == "add":
                self._ignore_add(nick, channel, pattern, purge=purge)
            else:
                self._ignore_del(nick, channel, pattern)
            return

        if subcmd == "purge":
            rest = parts[1:]
            if not rest:
                self.send(nick, "Usage: ignore purge [#channel] <pattern>")
                return
            if rest[0].startswith("#"):
                if len(rest) < 2:
                    self.send(nick, "Usage: ignore purge #channel <pattern>")
                    return
                channel = rest[0]
                pattern = rest[1]
            else:
                channel = None
                pattern = rest[0]
            self._ignore_purge(nick, channel, pattern)
            return

        self.send(nick, "Usage: ignore add|del|list [#channel] <pattern>")

    def _ignore_purge(self, nick: str, channel: str, pattern: str):
        """Delete stats for nicks matching pattern without touching the ignore list."""
        from database.models import delete_nick_stats
        count = delete_nick_stats(self.network, pattern, channel=channel)
        scope = channel if channel else "network-wide"
        if count:
            self.send(nick, f"Purged stats for {count} nick(s) matching {pattern!r} ({scope}).")
        else:
            self.send(nick, f"No stats found for {pattern!r} ({scope}).")

    def _ignore_add(self, nick: str, channel: str, pattern: str, purge: bool = False):
        from database.models import add_ignore, delete_nick_stats
        add_ignore(pattern, self.network, channel=channel, added_by=nick)
        scope = channel if channel != "*" else "network-wide"
        self.send(nick, f"Ignored {pattern} ({scope}).")
        if purge:
            chan = channel if channel != "*" else None
            count = delete_nick_stats(self.network, pattern, channel=chan)
            if count:
                self.send(nick, f"Purged stats for {count} nick(s) matching {pattern!r}.")
            else:
                self.send(nick, f"No existing stats found for {pattern!r}.")

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

    def _cmd_reload(self, nick: str):
        """Reload config.yml: upsert networks, connect new ones, disconnect removed ones."""
        if not self._require_auth(nick): return

        path = self.config_path
        if not path:
            self.send(nick, "Config path not set — cannot rehash.")
            return

        import yaml
        try:
            with open(path) as f:
                new_cfg = yaml.safe_load(f)
        except Exception as e:
            self.send(nick, f"Failed to read config: {e}")
            return

        from database.models import seed_from_config, get_enabled_networks
        from bot.connector import IRCConnector

        # Remember which networks were active before the reload
        old_names = {c.network for c in self.connectors}

        # Sync DB with new config (upserts + disables removed networks)
        seed_from_config(new_cfg)

        # Work out what changed
        new_names = {n["name"] for n in new_cfg.get("networks", [])}
        to_connect = new_names - old_names
        to_disconnect = old_names - new_names

        # Update the live config dict in-place so all handlers see the new values
        self.cfg.clear()
        self.cfg.update(new_cfg)

        added, removed = [], []

        # Connect new networks
        for net in new_cfg.get("networks", []):
            if net["name"] not in to_connect:
                continue
            import json as _json
            bot = new_cfg.get("bot", {})
            net_cfg = {
                "name":     net["name"],
                "host":     net["host"],
                "port":     net.get("port", 6667),
                "ssl":      net.get("ssl", False),
                "nick":     net.get("nick")     or bot.get("nick",     "statsbot"),
                "altnick":  net.get("altnick")  or bot.get("altnick",  "statsbot_"),
                "ident":    net.get("ident")     or bot.get("ident",    "statsbot"),
                "realname": net.get("realname") or bot.get("realname", "IRC Stats Bot"),
                "channels": net.get("channels", []),
                "cmd_prefix": net.get("cmd_prefix", new_cfg.get("commands", {}).get("prefix", "!")),
                "nickserv_password": net.get("nickserv_password"),
                "server_password":   net.get("server_password"),
                "ghost":      net.get("ghost", False),
                "on_connect": net.get("on_connect", []),
                "sasl":       net.get("sasl"),
            }
            self._post_event({"action": "add_network", "net_cfg": net_cfg})
            added.append(net["name"])

        # Disconnect removed networks
        for name in to_disconnect:
            self._post_event({"action": "remove_network", "name": name})
            removed.append(name)

        parts = []
        if added:
            parts.append(f"connecting: {', '.join(added)}")
        if removed:
            parts.append(f"disconnecting: {', '.join(removed)}")
        if not parts:
            parts.append("no network changes")
        self.send(nick, f"Rehash done — {'; '.join(parts)}.")

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
        """addnet is no longer used — networks are managed via config.yml + rehash."""
        if not self._require_auth(nick): return
        self.send(nick, "Networks are now managed via config.yml.")
        self.send(nick, "Add the network there, then run: rehash")

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
            self.send(nick, "Supported: " + ", ".join(SUPPORTED))
            return
        if not set_lang(network, chan, lang):
            self.send(nick, f"Unsupported language {lang!r}. Supported: " + ", ".join(SUPPORTED))
            return
        self.send(nick, f"Language for {chan} on {network} set to {lang}.")



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

    @staticmethod
    def _paste(text: str, timeout: int = 6) -> str | None:
        """Upload text to a paste service and return the URL.

        Tries multiple services in order, returns the first that works.
        """
        encoded = text.encode()

        def try_ixio() -> str | None:
            # ix.io accepts a plain POST with form field "f:1"
            data = urllib.parse.urlencode({"f:1": text}).encode()
            req = urllib.request.Request(
                "http://ix.io",
                data=data,
                headers={"User-Agent": "Statsbot/2.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                url = r.read().decode().strip()
                return url if url.startswith("http") else None

        def try_pastrs() -> str | None:
            # paste.rs: raw POST body, returns bare URL
            req = urllib.request.Request(
                "https://paste.rs",
                data=encoded,
                headers={
                    "Content-Type": "text/plain",
                    "User-Agent": "Statsbot/2.0",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                url = r.read().decode().strip()
                return url if url.startswith("http") else None

        def try_dpaste() -> str | None:
            # dpaste.org: form POST
            data = urllib.parse.urlencode({
                "content": text,
                "syntax": "text",
                "expiry_days": 7,
            }).encode()
            req = urllib.request.Request(
                "https://dpaste.org/api/",
                data=data,
                headers={"User-Agent": "Statsbot/2.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                url = r.read().decode().strip().strip('"')
                return url if url.startswith("http") else None

        for backend in (try_ixio, try_pastrs, try_dpaste):
            try:
                url = backend()
                if url:
                    return url
            except Exception as exc:
                log.debug("paste backend %s failed: %s", backend.__name__, exc)

        log.warning("all paste backends failed")
        return None
    def _cmd_help(self, nick: str):
        lines = [
            "ircstats PM command reference",
            "=" * 36,
            "",
            "Auth:",
            "  identify <master_nick> <password>     — authenticate",
            "  logout                                — end your session",
            "  whoami                                — show current identity",
            "  status                                — connected channels and user counts",
            "",
            "Ignore management (requires auth):",
            "  ignore add [#chan] <pattern>           — add ignore (network-wide if no #chan)",
            "  ignore add [#chan] <pattern> --purge   — add ignore AND delete existing stats",
            "  ignore del [#chan] <pattern>           — remove ignore",
            "  ignore list [#chan]                    — list ignores",
            "  ignore purge [#chan] <pattern>         — delete stats only (ignore list unchanged)",
            "",
            "Master management (requires auth):",
            "  master add <nick>                      — add master (bot asks for password)",
            "  master del <nick>                      — remove master",
            "  master list                            — list all masters",
            "",
            "Configuration (requires auth):",
            "  set page [#chan] <url>                 — override the URL returned by !stats",
            "  setlang [-network <net>] #chan <lang>  — set channel language",
            "    Supported: en_US  pt_PT  fr_FR  it_IT",
            "",
            "Network & channel management (requires auth):",
            "  nets                                   — list all networks",
            "  chans                                  — list channels on this network",
            "  addnet -name <n> -host <h> -port <p> [-ssl|-plaintext]  (TLS is default)",
            "  delnet -name <n>                       — remove network + ALL its stats (no undo)",
            "  addchan [-network <net>] #channel      — join and start tracking",
            "  delchan [-network <net>] #channel      — part and delete ALL channel stats (no undo)",
        ]
        text = "\n".join(lines)

        # _paste() does a blocking HTTP request. We're called from inside the
        # asyncio read loop, so we must offload it to a thread to avoid stalling
        # the bot. Fire-and-forget: the callback sends the reply when done.
        import asyncio
        send = self.send

        def _do_paste():
            url = PMCommandHandler._paste(text)
            if url:
                send(nick, f"Command reference: {url}")
            else:
                send(nick, "Could not reach pastebin — try again in a moment.")

        try:
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, _do_paste)
        except RuntimeError:
            # No running loop (e.g. during tests) — call directly
            _do_paste()
