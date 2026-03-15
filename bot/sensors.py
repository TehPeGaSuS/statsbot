"""
bot/sensors.py
Event handlers that mirror sensors.c from stats.mod.
Each sensor receives an event and updates the database accordingly.
All tracking is per-nick, not per-mask.
"""

import json
import time
import logging
from typing import Optional

from database.models import (
    get_or_create_nick, incr, incr_word,
    add_quote, add_url, add_kick, add_topic, add_chanlog,
    get_chanlog, update_peak, reset_period, touch_nick, is_ignored, add_master,
    set_example
)
from bot.parser import parse_message, count_words, count_letters, count_smileys, count_questions

log = logging.getLogger("sensors")


class Sensors:
    """
    Stateful sensor collection. Holds config references and quote counter.
    Instantiate once per network connection.
    """

    def __init__(self, config: dict, network: str):
        self.network = network
        self.cfg = config
        self.smileys = config.get("stats", {}).get("happy_smileys", [])
        self.sad_smileys = config.get("stats", {}).get("sad_smileys", [])
        self.min_word = config.get("stats", {}).get("min_word_length", 3)
        pisg = config.get("pisg", {})
        self.violent_words = pisg.get("ViolentWords", [])
        self.foul_words    = pisg.get("FoulWords", [])
        # Monologue tracking: {(network, channel): (last_nick, consecutive_count)}
        self._last_speaker: dict = {}
        # Nick list cache for reference detection: {(network,channel): (ts, [nicks])}
        self._nick_cache: dict = {}
        self._nick_cache_ttl = 300  # seconds
        self.log_wordstats = config.get("stats", {}).get("log_wordstats", True)
        self.quote_freq = config.get("stats", {}).get("quote_frequency", 5)
        self.kick_context = config.get("stats", {}).get("kick_context", 5)
        self.log_urls = config.get("stats", {}).get("display_urls", 5) > 0
        self._quote_counters = {}   # (network, chan) -> int
        self._minute_tracker = {}   # (network, chan) -> set of nick_ids active this minute
        self.cmd_prefix = config.get("commands", {}).get("prefix", "!")
        # Load static ignores from config into DB
        self._load_statics(config)

    def _load_statics(self, config: dict):
        from database.models import add_ignore, add_master
        # Always ignore the bot's own nicks — we don't want ourselves in the stats
        bot_nick    = config.get("bot", {}).get("nick", "")
        bot_altnick = config.get("bot", {}).get("altnick", "")
        if bot_nick:
            add_ignore(bot_nick, self.network, added_by="bot-self")
        if bot_altnick and bot_altnick != bot_nick:
            add_ignore(bot_altnick, self.network, added_by="bot-self")
        for mask in config.get("stats", {}).get("ignore", []):
            add_ignore(mask, self.network, added_by="config")
        for mask in config.get("bot", {}).get("masters", []):
            add_master(mask, self.network, added_by="config")
        log.debug("Static ignores and masters loaded from config.")

    # ─── PRIVMSG ────────────────────────────────────────────────────────────

    def on_privmsg(self, nick: str, host: str, channel: str, text: str):
        """Fires on every channel message. Core stat tracker."""
        if not channel.startswith(('#', '&', '!')):
            return  # ignore private messages

        # Skip ignored nicks
        if is_ignored(nick, self.network, host, channel):
            return

        # Skip lines that start with the command prefix — !top10 etc.
        # Counting those as "words" would be cheating :)
        if self.cmd_prefix and text.lstrip().startswith(self.cmd_prefix):
            return

        nick_id = get_or_create_nick(nick, self.network, channel, host)

        parsed = parse_message(text, self.smileys, self.sad_smileys, self.min_word,
                                self.violent_words, self.foul_words)

        incr(nick_id, "lines", 1)
        incr(nick_id, "words", parsed["words"])
        incr(nick_id, "letters", parsed["letters"])
        if parsed["smileys"]:
            incr(nick_id, "smileys", parsed["smileys"])
        if parsed["sad"]:
            incr(nick_id, "sad", parsed["sad"])
        if parsed["caps"]:
            incr(nick_id, "caps", 1)
            set_example(nick_id, "caps_ex", text)
        if parsed["violent"]:
            incr(nick_id, "violent", parsed["violent"])
        if parsed["foul"]:
            incr(nick_id, "foul", parsed["foul"])
            set_example(nick_id, "foul_ex", text)
        incr(nick_id, "letters", parsed["letters"])

        # Per-smiley frequency
        if parsed["smiley_freq"] and self.log_wordstats:
            from database.models import incr_smiley
            for smiley, cnt in parsed["smiley_freq"].items():
                for _ in range(cnt):
                    incr_smiley(nick_id, self.network, channel, smiley)

        # Monologue detection: 5+ consecutive lines by same nick
        key = (self.network, channel)
        last_nick, streak = self._last_speaker.get(key, (None, 0))
        if last_nick == nick:
            streak += 1
            if streak == 5:
                incr(nick_id, "monologues", 1)
        else:
            streak = 1
        self._last_speaker[key] = (nick, streak)
        if parsed["questions"]:
            incr(nick_id, "questions", 1)

        # Word frequency tracking
        if self.log_wordstats:
            for word in parsed["word_list"]:
                incr_word(nick_id, self.network, channel, word, nick=nick)

        # Nick reference tracking — uses a cached nick list (refreshed every 5 min)
        from database.models import get_conn as _gc, incr_nick_ref
        from bot.parser import find_nick_refs
        import time as _time
        _cache_key = (self.network, channel)
        _cached_ts, _known = self._nick_cache.get(_cache_key, (0, []))
        if _time.time() - _cached_ts > self._nick_cache_ttl:
            with _gc() as _c:
                _known = [r["nick"] for r in _c.execute(
                    "SELECT n.nick FROM nicks n JOIN stats st ON st.nick_id=n.id"
                    " WHERE n.network=? AND n.channel=? AND st.words>0 AND st.period=0",
                    (self.network, channel)
                ).fetchall()]
            self._nick_cache[_cache_key] = (_time.time(), _known)
        for mentioned in find_nick_refs(text, _known):
            if mentioned.lower() != nick.lower():
                incr_nick_ref(self.network, channel, mentioned, nick)

        # Karma tracking — detect nick++ / nick-- (suffix only).
        # Rule: strip exactly the last two chars (++ or --) from the token;
        # the remainder is the nick. This handles nicks that themselves contain
        # -- or ++ (e.g. "Mike----" → nick "Mike--", delta -1).
        # Only award karma if the target nick is actually in the channel
        # (_known is the nick cache built above for nick-refs).
        from database.models import change_karma as _change_karma
        _known_lower = {n.lower(): n for n in _known}
        for _token in text.split():
            _token = _token.strip(".,!?;:\"'<>@")
            if len(_token) > 2 and _token.endswith('++'):
                _kn = _token[:-2]
                if _kn and _kn.lower() != nick.lower() and _kn.lower() in _known_lower:
                    _change_karma(self.network, channel, _known_lower[_kn.lower()], +1)
            elif len(_token) > 2 and _token.endswith('--'):
                _kn = _token[:-2]
                if _kn and _kn.lower() != nick.lower() and _kn.lower() in _known_lower:
                    _change_karma(self.network, channel, _known_lower[_kn.lower()], -1)

        # URL logging
        if self.log_urls and parsed["urls"]:
            for url in parsed["urls"]:
                add_url(nick_id, self.network, channel, url)

        # Quote logging — log every Nth message, but always log first quote for new nicks
        key = (self.network, channel)
        nick_key = (self.network, channel, nick_id)
        self._quote_counters[key] = self._quote_counters.get(key, 0) + 1
        is_first = nick_key not in self._quote_counters
        self._quote_counters[nick_key] = True  # mark nick as having spoken
        if is_first or self._quote_counters[key] >= self.quote_freq:
            add_quote(nick_id, self.network, channel, text)
            if self._quote_counters[key] >= self.quote_freq:
                self._quote_counters[key] = 0

        # Channel log
        add_chanlog(self.network, channel, nick, text, type_=0)

        log.debug(f"PRIVMSG {nick} in {channel}: {parsed['words']}w {parsed['letters']}l")

    # ─── ACTION (/me) ────────────────────────────────────────────────────────

    def on_action(self, nick: str, host: str, channel: str, text: str):
        if not channel.startswith(('#', '&', '!')):
            return
        if is_ignored(nick, self.network, host, channel):
            return
        nick_id = get_or_create_nick(nick, self.network, channel, host)
        incr(nick_id, "actions", 1)
        full_text = f"* {nick} {text}"
        set_example(nick_id, "action_ex", full_text)

        # Check for violent actions — also detect victim nick
        from bot.parser import count_violent, find_nick_refs, parse_message
        if self.violent_words and count_violent(text, self.violent_words):
            incr(nick_id, "violent", 1)
            set_example(nick_id, "violent_ex", full_text)
            # Try to find the victim — first nick mentioned after the violent word
            # Get current channel nicks from DB
            from database.models import get_conn
            with get_conn() as conn:
                known = [r["nick"] for r in conn.execute(
                    "SELECT n.nick FROM nicks n JOIN stats st ON st.nick_id=n.id WHERE n.network=? AND n.channel=? AND st.words>0 AND st.period=0",
                    (self.network, channel)
                ).fetchall()]
            refs = find_nick_refs(text, known)
            for victim_nick in refs:
                if victim_nick.lower() != nick.lower():
                    victim_id = get_or_create_nick(victim_nick, self.network, channel)
                    incr(victim_id, "attacked", 1)
                    set_example(victim_id, "attacked_ex", full_text)
                    break  # only first victim

        # Count lines/words/letters for the action directly — do NOT call
        # on_privmsg to avoid double-counting lines, smileys, quotes, etc.
        parsed = parse_message(text, self.smileys, self.sad_smileys, self.min_word,
                               self.violent_words, self.foul_words)
        incr(nick_id, "lines",   1)
        incr(nick_id, "words",   parsed["words"])
        incr(nick_id, "letters", parsed["letters"])
        if parsed["smileys"]:
            incr(nick_id, "smileys", parsed["smileys"])
        if parsed["sad"]:
            incr(nick_id, "sad", parsed["sad"])
        if parsed["questions"]:
            incr(nick_id, "questions", 1)

        # Word frequency
        if self.log_wordstats:
            from database.models import incr_word
            for word in parsed["word_list"]:
                incr_word(nick_id, self.network, channel, word, nick=nick)

        # Quote logging
        key = (self.network, channel)
        nick_key = (self.network, channel, nick_id)
        self._quote_counters[key] = self._quote_counters.get(key, 0) + 1
        is_first = nick_key not in self._quote_counters
        self._quote_counters[nick_key] = True
        if is_first or self._quote_counters[key] >= self.quote_freq:
            add_quote(nick_id, self.network, channel, full_text)
            if self._quote_counters[key] >= self.quote_freq:
                self._quote_counters[key] = 0

        # Channel log
        add_chanlog(self.network, channel, nick, full_text, type_=1)

    # ─── JOIN ────────────────────────────────────────────────────────────────

    def on_join(self, nick: str, host: str, channel: str, is_bot: bool = False):
        # Create entry for ALL joining nicks so minutes tracking is accurate.
        # Silent nicks (0 words/lines) are filtered from display by get_top.
        if is_ignored(nick, self.network, host, channel):
            add_chanlog(self.network, channel, nick, None, type_=6)
            return
        nick_id = get_or_create_nick(nick, self.network, channel, host)
        incr(nick_id, "joins", 1)
        # Store +B flag so dashboard can show it
        if is_bot:
            from database.models import get_conn
            with get_conn() as conn:
                conn.execute("UPDATE nicks SET is_bot=1 WHERE id=?", (nick_id,))
        add_chanlog(self.network, channel, nick, None, type_=6)
        log.debug(f"JOIN {nick} -> {channel} (bot={is_bot})")

    # ─── PART ────────────────────────────────────────────────────────────────

    def on_part(self, nick: str, host: str, channel: str, reason: str = ""):
        touch_nick(nick, self.network, channel, host)
        add_chanlog(self.network, channel, nick, reason, type_=4)

    # ─── QUIT ────────────────────────────────────────────────────────────────

    def on_quit(self, nick: str, host: str, channels: list, reason: str = ""):
        for channel in channels:
            touch_nick(nick, self.network, channel, host)
            add_chanlog(self.network, channel, nick, reason, type_=5)

    # ─── NICK CHANGE ─────────────────────────────────────────────────────────

    def on_nick(self, old_nick: str, host: str, new_nick: str, channels: list):
        """
        Nick change. We track the new nick independently (per-nick design).
        We still increment T_NICKS on the OLD nick to show how many times
        they changed their nick. Only tracked if the old nick is already in the DB.
        """
        from database.models import get_conn
        for channel in channels:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT id FROM nicks WHERE nick=? AND network=? AND channel=?",
                    (old_nick, self.network, channel)
                ).fetchone()
            if row:
                incr(row["id"], "nicks", 1)
            add_chanlog(self.network, channel, old_nick, new_nick, type_=3)
        log.debug(f"NICK {old_nick} -> {new_nick}")

    # ─── KICK ─────────────────────────────────────────────────────────────────

    def on_kick(self, kicker: str, host: str, channel: str,
                 victim: str, reason: str):
        kicker_id = get_or_create_nick(kicker, self.network, channel, host)
        incr(kicker_id, "kick_given", 1)

        # Victim gets a "kicks received" counter
        victim_id = get_or_create_nick(victim, self.network, channel)
        incr(victim_id, "kicks", 1)

        # Grab context lines
        ctx_lines = get_chanlog(self.network, channel, self.kick_context)
        context_json = json.dumps([
            f"<{l['nick']}> {l['line']}" for l in ctx_lines if l.get("line")
        ])

        add_kick(self.network, channel, kicker, victim, reason, context_json)
        log.debug(f"KICK {kicker} kicked {victim} from {channel}: {reason}")

    # ─── MODE ─────────────────────────────────────────────────────────────────

    def on_mode(self, nick: str, host: str, channel: str,
                 mode_str: str, mode_targets: list = None):
        if not channel.startswith(('#', '&', '!')):
            return
        if mode_targets is None:
            mode_targets = []

        setter_id = get_or_create_nick(nick, self.network, channel, host)
        incr(setter_id, "modes", 1)

        # Parse mode string into (flag, adding) pairs matched with targets.
        # e.g. "+oov" with ["Nick1", "Nick2", "Nick3"] → [(o,+,Nick1), (o,+,Nick2), (v,+,Nick3)]
        # Mode chars that consume a target parameter:
        _TARGETED = set("oOvVhHbBeEIkqlLjf")
        adding = True
        target_idx = 0
        for ch in mode_str:
            if ch == '+':
                adding = True
            elif ch == '-':
                adding = False
            elif ch in _TARGETED:
                target_nick = mode_targets[target_idx] if target_idx < len(mode_targets) else None
                target_idx += 1

                if ch == 'b':
                    incr(setter_id, "bans", 1)
                elif ch in ('o', 'O') and target_nick:
                    target_id = get_or_create_nick(target_nick, self.network, channel)
                    if adding:
                        incr(setter_id,  "op_given", 1)
                        incr(target_id,  "op_got",   1)
                    else:
                        incr(setter_id,  "op_taken", 1)
                        incr(target_id,  "deop_got", 1)
                elif ch in ('h', 'H') and target_nick:
                    target_id = get_or_create_nick(target_nick, self.network, channel)
                    if adding:
                        incr(setter_id,  "halfop_given", 1)
                        incr(target_id,  "halfop_got",   1)
                    else:
                        incr(setter_id,  "halfop_taken", 1)
                        incr(target_id,  "dehalfop_got", 1)
                elif ch == 'v' and target_nick:
                    target_id = get_or_create_nick(target_nick, self.network, channel)
                    if adding:
                        incr(setter_id,  "voice_given", 1)
                        incr(target_id,  "voice_got",   1)
                    else:
                        incr(setter_id,  "voice_taken", 1)
                        incr(target_id,  "devoice_got", 1)

        targets_str = " ".join(mode_targets)
        add_chanlog(self.network, channel, nick,
                    f"sets mode {mode_str} {targets_str}".rstrip(), type_=2)

    # ─── TOPIC ────────────────────────────────────────────────────────────────

    def on_topic(self, nick: str, host: str, channel: str, topic: str):
        nick_id = get_or_create_nick(nick, self.network, channel, host)
        incr(nick_id, "topics", 1)
        add_topic(self.network, channel, topic, nick)

    # ─── MINUTELY ─────────────────────────────────────────────────────────────

    def on_minute(self, channel_members: dict):
        """
        Called every minute (by scheduler).
        channel_members: dict of {channel: [nick, ...]}
        Increments T_MINUTES for each nick currently in channel.
        Also updates peak user count.
        """
        for channel, members in channel_members.items():
            stat_seen = set()   # non-ignored nicks (get minutes incremented)
            total_count = 0     # all nicks including ignored (for peak)
            for nick in members:
                total_count += 1
                if is_ignored(nick, self.network, channel=channel):
                    continue
                nick_id = get_or_create_nick(nick, self.network, channel)
                if nick_id not in stat_seen:
                    incr(nick_id, "minutes", 1)
                    stat_seen.add(nick_id)
            # Peak is an all-time record — never period-specific
            update_peak(self.network, channel, 0, total_count)

    # ─── PERIOD RESETS ────────────────────────────────────────────────────────

    def on_daily_reset(self):
        """Called at midnight."""
        reset_period(1)
        log.info("Daily stats reset.")

    def on_weekly_reset(self):
        """Called on Monday."""
        reset_period(2)
        log.info("Weekly stats reset.")

    def on_monthly_reset(self):
        """Called on 1st of month."""
        reset_period(3)
        log.info("Monthly stats reset.")
