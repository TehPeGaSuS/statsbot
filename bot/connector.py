"""
bot/connector.py
Raw IRC connection handler. Parses RFC 1459 messages and fires sensors.
Uses asyncio for non-blocking I/O, supports TLS.
"""

import asyncio
import ssl
import logging
import re
import time
from typing import Callable, Optional, List

log = logging.getLogger("connector")

# IRC message parser
MSG_RE = re.compile(
    r'^(?::(?P<prefix>[^\s]+)\s+)?'
    r'(?P<command>[A-Z0-9]+)'
    r'(?P<params>(?:\s+[^:][^\s]*)*)?'
    r'(?:\s+:(?P<trailing>.*))?$'
)


def parse_irc(raw: str):
    m = MSG_RE.match(raw)
    if not m:
        return None
    prefix = m.group("prefix") or ""
    command = m.group("command")
    params_str = (m.group("params") or "").split()
    trailing = m.group("trailing")
    params = params_str + ([trailing] if trailing is not None else [])
    return prefix, command, params


def nick_from_prefix(prefix: str) -> str:
    return prefix.split("!")[0] if "!" in prefix else prefix


def host_from_prefix(prefix: str) -> str:
    return prefix.split("!")[1] if "!" in prefix else ""


class IRCConnector:
    """
    Async IRC connection for one network.
    """

    def __init__(self, config: dict, network_cfg: dict,
                  sensors, commands, scheduler=None, pm_handler=None):
        self.cfg = config
        self.net = network_cfg
        self.sensors = sensors
        self.commands = commands
        self.pm_handler = pm_handler
        self.scheduler = scheduler

        # Identity: per-network overrides global bot: defaults.
        # DB rows may have NULL for optional fields — use `or` to skip None/empty.
        bot = config.get("bot", {})
        self.nick     = (network_cfg.get("nick")     or bot.get("nick")     or "statsbot")
        self.altnick  = (network_cfg.get("altnick")  or bot.get("altnick")  or self.nick + "_")
        self.realname = (network_cfg.get("realname") or bot.get("realname") or "IRC Stats Bot")
        self.ident    = (network_cfg.get("ident")    or bot.get("ident")    or "statsbot")

        self.host = network_cfg["host"]
        self.port = network_cfg.get("port", 6667)
        self.use_ssl = network_cfg.get("ssl", False)
        self.channels: List[str] = network_cfg.get("channels", [])
        self.network: str = network_cfg["name"]

        self._writer: Optional[asyncio.StreamWriter] = None
        self._channel_members: dict = {}   # channel -> set of nicks
        self._nick_hosts: dict = {}        # nick -> user@host (populated by WHO)
        self._whox_pending: set = set()    # channels awaiting WHO response
        self._connected = False
        self._current_nick = self.nick
        self._sasl_authed  = False       # SASL completed
        self._auth_done    = False       # any auth completed, safe to run on_connect
        self._ghost_sent   = False       # already sent GHOST this session
        self.reload_queue  = None        # set by main.py after construction
        self._reclaim_nick = False       # waiting to reclaim primary nick after ghost

    # ─── Send ─────────────────────────────────────────────────────────────

    def send_raw(self, line: str):
        if self._writer:
            data = (line.rstrip("\r\n") + "\r\n").encode("utf-8", errors="replace")
            self._writer.write(data)
            log.debug(f">> {line}")

    def send_msg(self, target: str, text: str):
        # Split long messages
        for chunk in _split_message(text, 400):
            self.send_raw(f"PRIVMSG {target} :{chunk}")

    def send_notice(self, target: str, text: str):
        self.send_raw(f"NOTICE {target} :{text}")

    # ─── Connect / Run ────────────────────────────────────────────────────

    async def connect(self):
        log.info(f"Connecting to {self.host}:{self.port} (ssl={self.use_ssl})")
        ssl_ctx = ssl.create_default_context() if self.use_ssl else None
        try:
            reader, writer = await asyncio.open_connection(
                self.host, self.port, ssl=ssl_ctx)
        except Exception as e:
            log.error(f"Connection failed: {e}")
            raise

        self._writer = writer
        self._connected = True
        log.info(f"Connected to {self.host}")

        # Server password (for BNCs / private servers)
        server_pass = self.net.get("server_password")
        if server_pass:
            self.send_raw(f"PASS {server_pass}")
            log.info("Sent server password.")

        # SASL — request capabilities before NICK/USER
        sasl_cfg = self.net.get("sasl")
        if sasl_cfg:
            self.send_raw("CAP REQ :sasl")
            log.info("Requested SASL capability.")

        # Register
        self.send_raw(f"NICK {self.nick}")
        self.send_raw(f"USER {self.ident} 0 * :{self.realname}")

        await self._read_loop(reader)

    async def _read_loop(self, reader: asyncio.StreamReader):
        while self._connected:
            try:
                raw = await reader.readline()
                if not raw:
                    log.warning("Server closed connection.")
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                log.debug(f"<< {line}")
                self._handle_line(line)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Read error: {e}", exc_info=True)

    def _handle_line(self, line: str):
        parsed = parse_irc(line)
        if not parsed:
            return
        prefix, command, params = parsed

        if command == "PING":
            self.send_raw(f"PONG :{params[0] if params else ''}")
            return

        if command == "CAP":
            # CAP * ACK :sasl  — server agreed, start SASL PLAIN
            subcmd = params[1] if len(params) > 1 else ""
            cap_val = params[2] if len(params) > 2 else ""
            if subcmd == "ACK" and "sasl" in cap_val:
                self.send_raw("AUTHENTICATE PLAIN")
            elif subcmd == "NAK":
                log.warning("Server rejected SASL CAP — falling back to NickServ.")
                self.send_raw("CAP END")
                self._do_nickserv_auth()

        elif command == "AUTHENTICATE":
            # Server sent AUTHENTICATE + — send base64(user\0user\0pass)
            if params and params[0] == "+":
                import base64
                sasl_cfg = self.net.get("sasl", {})
                user = sasl_cfg.get("username", self.nick)
                pw   = sasl_cfg.get("password", "")
                blob = f"{user}\0{user}\0{pw}".encode()
                self.send_raw(f"AUTHENTICATE {base64.b64encode(blob).decode()}")

        elif command == "900":  # RPL_LOGGEDIN
            log.info(f"SASL: logged in as {params[2] if len(params) > 2 else '?'}")

        elif command == "903":  # RPL_SASLSUCCESS
            log.info("SASL authentication successful.")
            self._sasl_authed = True
            self._auth_done   = True
            self.send_raw("CAP END")

        elif command in ("904", "905"):  # ERR_SASLFAIL / ERR_SASLTOOLONG
            log.error("SASL authentication failed.")
            self.send_raw("CAP END")

        elif command == "001":   # RPL_WELCOME
            log.info("Registered on server.")
            self._current_nick = self.nick
            # NickServ auth (if not using SASL)
            if not self._sasl_authed:
                self._do_nickserv_auth()
            # Join channels (after a brief delay to let auth settle)
            for chan in self.channels:
                self.send_raw(f"JOIN {chan}")
            # on_connect commands (if no auth pending)
            if self._auth_done or (not self.net.get("sasl") and not self.net.get("nickserv_password")):
                self._run_on_connect()

        elif command == "433":  # ERR_NICKNAMEINUSE
            if not self._ghost_sent and self.net.get("ghost") and (
                    self.net.get("nickserv_password") or self.net.get("sasl", {}).get("password")):
                # Try to ghost the primary nick
                ns_pass = self.net.get("nickserv_password") or self.net.get("sasl", {}).get("password", "")
                self.send_raw(f"NICK {self.altnick}")
                self._current_nick = self.altnick
                self.send_raw(f"PRIVMSG NickServ :GHOST {self.nick} {ns_pass}")
                self._ghost_sent   = True
                self._reclaim_nick = True
                log.info(f"Nick in use — sent GHOST for {self.nick}, using {self.altnick}")
            else:
                self._current_nick = self.altnick
                self.send_raw(f"NICK {self.altnick}")
                log.warning(f"Nick {self.nick} in use, switched to {self.altnick}")

        elif command == "353":  # RPL_NAMREPLY — initial member list
            # params: [me, =/@/*, channel, "nick1 nick2 ..."]
            if len(params) >= 4:
                channel = params[2]
                nicks = params[3].split()
                nicks = [n.lstrip("@+%&~!") for n in nicks]
                if channel not in self._channel_members:
                    self._channel_members[channel] = set()
                self._channel_members[channel].update(nicks)

        elif command == "366":  # RPL_ENDOFNAMES — fire WHO now that NAMES is complete
            channel = params[1] if len(params) > 1 else ""
            if channel and channel not in self._whox_pending:
                self._whox_pending.add(channel)
                # Plain WHO — 352 gives us channel, nick, user, host, flags directly
                self.send_raw(f"WHO {channel}")
                log.debug(f"Sent WHO for {channel}")

        elif command == "352":  # RPL_WHOREPLY
            # :server 352 botnick channel user host server nick flags :hops realname
            # params: [botnick, channel, user, host, server, nick, flags, ...]
            if len(params) >= 7:
                who_channel = params[1]
                who_user    = params[2]
                who_host    = params[3]
                # params[4] = server name (skip)
                who_nick    = params[5]
                who_flags   = params[6]   # e.g. 'H', 'HrsB', 'G*~@', etc.
                full_host   = f"{who_user}@{who_host}"

                self._nick_hosts[who_nick.lower()] = full_host

                # Skip ourselves
                if who_nick.lower() == self._current_nick.lower():
                    log.debug(f"WHO: skipping self ({who_nick})")
                # +B flag = bot umode
                elif "B" in who_flags:
                    if self.cfg.get("stats", {}).get("auto_ignore_bots", False):
                        from database.models import add_ignore
                        add_ignore(f"{who_nick}!{full_host}", self.network,
                                   added_by="who-autobot")
                        log.info(f"WHO: auto-ignored +B nick {who_nick} ({full_host})")
                    else:
                        log.info(
                            f"WHO: {who_nick} ({full_host}) has +B — "
                            f"!ignore add {who_nick}!{full_host}"
                        )
                        if who_channel:
                            self.sensors.on_join(who_nick, full_host, who_channel, is_bot=True)
                elif who_channel:
                    self.sensors.on_join(who_nick, full_host, who_channel, is_bot=False)
                    # Try auto-auth via host masks
                    if self.pm_handler and hasattr(self.pm_handler, 'auth'):
                        self.pm_handler.auth.try_auto_auth(self.network, who_nick, full_host)
                else:
                    from database.models import get_conn
                    with get_conn() as conn:
                        conn.execute(
                            "UPDATE nicks SET last_host=?, last_seen=? WHERE nick=? AND network=?",
                            (full_host, int(time.time()), who_nick, self.network)
                        )
                log.debug(f"WHO: {who_nick}!{full_host} flags={who_flags} chan={who_channel}")

        elif command == "315":  # RPL_ENDOFWHO
            channel = params[1] if len(params) > 1 else ""
            self._whox_pending.discard(channel)
            log.debug(f"WHOX complete for {channel}")

        elif command == "JOIN":
            nick = nick_from_prefix(prefix)
            host = host_from_prefix(prefix)
            channel = params[0] if params else ""
            if nick == self._current_nick:
                if channel not in self._channel_members:
                    self._channel_members[channel] = set()
            else:
                self._channel_members.setdefault(channel, set()).add(nick)
                if host:
                    self._nick_hosts[nick.lower()] = host
                self.sensors.on_join(nick, host, channel)

        elif command == "PART":
            nick = nick_from_prefix(prefix)
            host = host_from_prefix(prefix)
            channel = params[0] if params else ""
            reason = params[1] if len(params) > 1 else ""
            self._channel_members.get(channel, set()).discard(nick)
            self.sensors.on_part(nick, host, channel, reason)

        elif command == "NOTICE":
            # NickServ sends NOTICE, not PRIVMSG, on most networks
            target = params[0] if params else ""
            text   = params[1] if len(params) > 1 else ""
            sender = nick_from_prefix(prefix).lower()
            if sender in ("nickserv", "enforcer", "q", "x") or sender.endswith("serv"):
                self._handle_nickserv_notice(text)

        elif command == "QUIT":
            nick = nick_from_prefix(prefix)
            host = host_from_prefix(prefix)
            reason = params[0] if params else ""
            chans = [ch for ch, m in self._channel_members.items() if nick in m]
            for ch in chans:
                self._channel_members[ch].discard(nick)
            self.sensors.on_quit(nick, host, chans, reason)
            # Destroy auth session on quit
            if self.pm_handler and hasattr(self.pm_handler, 'auth'):
                self.pm_handler.auth.on_quit(self.network, nick)

        elif command == "NICK":
            nick = nick_from_prefix(prefix)
            host = host_from_prefix(prefix)
            new_nick = params[0] if params else ""
            chans = [ch for ch, m in self._channel_members.items() if nick in m]
            for ch in chans:
                self._channel_members[ch].discard(nick)
                self._channel_members[ch].add(new_nick)
            self.sensors.on_nick(nick, host, new_nick, chans)
            # Transfer auth session on nick change
            if self.pm_handler and hasattr(self.pm_handler, 'auth'):
                self.pm_handler.auth.on_nick_change(self.network, nick, new_nick)

        elif command == "KICK":
            kicker = nick_from_prefix(prefix)
            host = host_from_prefix(prefix)
            channel = params[0] if params else ""
            victim = params[1] if len(params) > 1 else ""
            reason = params[2] if len(params) > 2 else ""
            self._channel_members.get(channel, set()).discard(victim)
            self.sensors.on_kick(kicker, host, channel, victim, reason)

        elif command == "MODE":
            nick = nick_from_prefix(prefix)
            host = host_from_prefix(prefix)
            target = params[0] if params else ""
            mode_str = params[1] if len(params) > 1 else ""
            # Pass all mode targets (params[2:]) to support multi-target modes
            # like +oov Nick1 Nick2 Nick3
            mode_targets = params[2:] if len(params) > 2 else []
            if target.startswith(('#', '&')):
                self.sensors.on_mode(nick, host, target, mode_str, mode_targets)

        elif command == "TOPIC":
            nick = nick_from_prefix(prefix)
            host = host_from_prefix(prefix)
            channel = params[0] if params else ""
            topic = params[1] if len(params) > 1 else ""
            self.sensors.on_topic(nick, host, channel, topic)

        elif command == "PRIVMSG":
            nick = nick_from_prefix(prefix)
            host = host_from_prefix(prefix)
            # Prefer WHO-resolved host (more reliable on cloaked networks)
            host = self._nick_hosts.get(nick.lower(), host)
            target = params[0] if params else ""
            text = params[1] if len(params) > 1 else ""

            # PM to the bot — route to PM command handler
            if target.lower() == self._current_nick.lower():
                if self.pm_handler:
                    self.pm_handler.dispatch(nick, host, text)
                # Also try auto-auth on first PM
                if hasattr(self, '_auth') and self._auth:
                    self._auth.try_auto_auth(self.network, nick, host)
                # Watch for NickServ responses
                nick_l = nick.lower()
                if nick_l in ("nickserv", "enforcer"):
                    self._handle_nickserv_notice(text)
            elif text.startswith("\x01ACTION") and text.endswith("\x01"):
                action_text = text[8:-1]
                self.sensors.on_action(nick, host, target, action_text)
            else:
                self.sensors.on_privmsg(nick, host, target, text)
                self.commands.dispatch(nick, target, text, host=host)

    def get_channel_members(self) -> dict:
        return {c: list(m) for c, m in self._channel_members.items()}

    def _do_nickserv_auth(self):
        """Send NickServ IDENTIFY if configured."""
        ns_pass = self.net.get("nickserv_password")
        if not ns_pass:
            self._auth_done = True
            return
        self.send_raw(f"PRIVMSG NickServ :IDENTIFY {self.nick} {ns_pass}")
        log.info("Sent NickServ IDENTIFY.")
        # Mark done — some networks confirm, some don't. We run on_connect
        # after a notice or after a short window via _handle_nickserv_notice.

    def _handle_nickserv_notice(self, text: str):
        """React to NickServ/services messages."""
        tl = text.lower()
        # Ghost succeeded — reclaim primary nick
        if self._reclaim_nick and any(w in tl for w in ("ghost", "killed", "disconnected", "is not online")):
            log.info(f"Ghost confirmed. Reclaiming nick {self.nick}.")
            self.send_raw(f"NICK {self.nick}")
            self._reclaim_nick = False
            self._current_nick = self.nick

        # Auth success signals
        if any(w in tl for w in ("you are now identified", "you are already identified",
                                  "password accepted", "now recognized", "logged in")):
            if not self._auth_done:
                log.info("NickServ: authentication confirmed.")
                self._auth_done = True
                self._run_on_connect()

    def _run_on_connect(self):
        """Send configured on_connect commands."""
        cmds = self.net.get("on_connect") or []
        if not cmds:
            return
        for cmd in cmds:
            raw = cmd.replace("{nick}", self._current_nick)
            self.send_raw(raw)
            log.info(f"on_connect: {raw}")

    async def join_channel(self, channel: str):
        """JOIN a channel live and add it to the tracked list."""
        if channel not in self.channels:
            self.channels.append(channel)
        self.send_raw(f"JOIN {channel}")

    async def part_channel(self, channel: str):
        """PART a channel live and remove it from the tracked list."""
        if channel in self.channels:
            self.channels.remove(channel)
        self.send_raw(f"PART {channel} :Removed by admin")

    async def disconnect(self):
        self._connected = False
        if self._writer:
            self.send_raw("QUIT :statsbot shutting down")
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass


def _split_message(text: str, max_len: int = 400) -> List[str]:
    """Split long messages into chunks."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks
