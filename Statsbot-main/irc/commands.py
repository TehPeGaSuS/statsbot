"""
irc/commands.py
Public channel commands — lightweight, point to the web page for detail.

  !stats          — link to stats page
  !top [n]        — top N by words, single line
  !quote [nick]   — random quote
"""

import logging
import time
from i18n import t, get_lang

log = logging.getLogger("commands")


class CommandHandler:
    def __init__(self, config: dict, network: str, send_fn, auth_manager=None):
        self.cfg = config
        self.network = network
        self.send = send_fn
        self.auth = auth_manager
        # Per-network prefix overrides the global commands.prefix.
        # Find this network's entry in config to check for a local override.
        _global_cmd = config.get("commands", {})
        _net_cfg = next((n for n in config.get("networks", []) if n.get("name") == network), {})
        self.prefix   = _net_cfg.get("cmd_prefix") or _global_cmd.get("prefix", "!")
        self.max_cmds  = _global_cmd.get("max_cmds", 5)
        self.max_window = _global_cmd.get("max_cmds_window", 60)
        self._flood_buckets: dict = {}

    def _flood_check(self, channel: str) -> bool:
        now = time.time()
        bucket = [t for t in self._flood_buckets.get(channel, []) if now - t < self.max_window]
        if len(bucket) >= self.max_cmds:
            return True
        bucket.append(now)
        self._flood_buckets[channel] = bucket
        return False

    def dispatch(self, nick: str, channel: str, text: str, host: str = ""):
        if not text.startswith(self.prefix):
            return
        parts = text[len(self.prefix):].split(None, 1)
        if not parts:
            return
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if self._flood_check(channel):
            return

        handlers = {
            "stats":  lambda: self._cmd_stats(channel),
            "top":    lambda: self._cmd_top(channel, args),
            "quote":  lambda: self._cmd_quote(channel, args),
        }

        handler = handlers.get(cmd)
        if handler:
            log.info(f"CMD {self.prefix}{cmd} from {nick} in {channel}")
            try:
                handler()
            except Exception as e:
                log.error(f"Command error: {e}", exc_info=True)

    def _cmd_stats(self, channel: str):
        from database.models import get_channel_config
        url = get_channel_config(self.network, channel, "stats_url")
        if not url:
            web = self.cfg.get("web", {})
            public_url = web.get("public_url", "")
            if public_url:
                chan_slug = channel.lstrip("#")
                url = f"{public_url.rstrip('/')}/{self.network}/{chan_slug}/"
            else:
                port = web.get("port", 8033)
                chan_slug = channel.lstrip("#")
                url = f"http://localhost:{port}/{self.network}/{chan_slug}/"
        lang = get_lang(self.network, channel)
        self.send(channel, t("cmd_stats_url", lang, channel=channel, url=url))

    def _cmd_top(self, channel: str, args: str):
        try:
            n = max(1, min(int(args.strip()), 10)) if args.strip().isdigit() else 3
        except ValueError:
            n = 3
        from database.models import get_top
        rows = [r for r in get_top(self.network, channel, "words", 0, n) if r["value"] > 0]
        if not rows:
            lang = get_lang(self.network, channel)
            self.send(channel, t("cmd_stats_no_stats", lang))
            return
        parts = [f"#{i+1} {r['nick']}: {r['value']}" for i, r in enumerate(rows)]
        lang = get_lang(self.network, channel)
        self.send(channel, t("cmd_top_result", lang, channel=channel,
                             n=len(rows), list=", ".join(parts)))

    def _cmd_quote(self, channel: str, args: str):
        target = args.strip() or None
        from database.models import get_random_quote, get_quote_for_nick
        if target:
            q = get_quote_for_nick(target, self.network, channel)
            if not q:
                lang = get_lang(self.network, channel)
                self.send(channel, t("cmd_quote_none_for_nick", lang, nick=target))
                return
            nick_str = target
        else:
            q = get_random_quote(self.network, channel)
            if not q:
                lang = get_lang(self.network, channel)
                self.send(channel, t("cmd_quote_none", lang))
                return
            nick_str = q.get("nick") or "unknown"
        lang = get_lang(self.network, channel)
        self.send(channel, t("cmd_quote_result", lang,
                            nick=nick_str, quote=q["quote"]))
