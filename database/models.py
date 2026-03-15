"""
database/models.py
SQLite schema and all DB operations for ircstats.
Tracks per-nick (not per-mask) stats, mirroring stats.mod's stat types.
"""

import sqlite3
import os
import time
from contextlib import contextmanager
from typing import Optional, List, Tuple, Dict


DB_PATH = "data/stats.db"


def set_db_path(path: str):
    global DB_PATH
    DB_PATH = path


@contextmanager
def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create all tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript("""
        -- Core nick registry
        CREATE TABLE IF NOT EXISTS nicks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nick        TEXT NOT NULL COLLATE NOCASE,
            network     TEXT NOT NULL DEFAULT 'unknown',
            channel     TEXT NOT NULL COLLATE NOCASE,
            last_host   TEXT,
            last_seen   INTEGER DEFAULT 0,
            first_seen  INTEGER DEFAULT 0,
            is_bot      INTEGER DEFAULT 0,   -- 1 if +B umode seen via WHOX
            UNIQUE(nick, network, channel)
        );

        -- Main stats table — one row per (nick, network, channel, period)
        -- period: 0=total, 1=today, 2=week, 3=month
        CREATE TABLE IF NOT EXISTS stats (
            nick_id     INTEGER NOT NULL REFERENCES nicks(id) ON DELETE CASCADE,
            period      INTEGER NOT NULL DEFAULT 0,
            words       INTEGER DEFAULT 0,
            letters     INTEGER DEFAULT 0,
            lines       INTEGER DEFAULT 0,
            actions     INTEGER DEFAULT 0,
            modes       INTEGER DEFAULT 0,
            bans        INTEGER DEFAULT 0,
            kicks       INTEGER DEFAULT 0,
            kick_given  INTEGER DEFAULT 0,
            nicks       INTEGER DEFAULT 0,
            joins       INTEGER DEFAULT 0,
            smileys     INTEGER DEFAULT 0,
            sad         INTEGER DEFAULT 0,
            caps        INTEGER DEFAULT 0,   -- lines written in ALL CAPS
            violent     INTEGER DEFAULT 0,   -- /me slaps etc
            foul        INTEGER DEFAULT 0,   -- foul word count
            monologues  INTEGER DEFAULT 0,   -- 5+ lines in a row alone
            questions   INTEGER DEFAULT 0,
            minutes     INTEGER DEFAULT 0,
            topics      INTEGER DEFAULT 0,
            op_given    INTEGER DEFAULT 0,   -- times this nick gave +o
            op_taken    INTEGER DEFAULT 0,   -- times this nick removed -o
            op_got      INTEGER DEFAULT 0,   -- times this nick received +o
            deop_got    INTEGER DEFAULT 0,   -- times this nick received -o
            voice_given INTEGER DEFAULT 0,   -- times this nick gave +v
            voice_taken INTEGER DEFAULT 0,   -- times this nick removed -v
            voice_got   INTEGER DEFAULT 0,   -- times this nick received +v
            devoice_got INTEGER DEFAULT 0,   -- times this nick received -v
            halfop_given INTEGER DEFAULT 0,  -- times this nick gave +h
            halfop_taken INTEGER DEFAULT 0,  -- times this nick removed -h
            halfop_got  INTEGER DEFAULT 0,   -- times this nick received +h
            dehalfop_got INTEGER DEFAULT 0,  -- times this nick received -h
            attacked    INTEGER DEFAULT 0,   -- times this nick was the victim of violence
            violent_ex  TEXT,               -- example violent line (attacker)
            attacked_ex TEXT,               -- example attacked line (victim)
            caps_ex     TEXT,               -- example ALL CAPS line
            foul_ex     TEXT,               -- example foul line
            action_ex   TEXT,               -- example /me line
            PRIMARY KEY (nick_id, period)
        );

        -- Per-nick word frequency (reset daily)
        CREATE TABLE IF NOT EXISTS wordstats (
            nick_id     INTEGER NOT NULL REFERENCES nicks(id) ON DELETE CASCADE,
            network     TEXT NOT NULL DEFAULT 'unknown',
            channel     TEXT NOT NULL COLLATE NOCASE,
            word        TEXT NOT NULL COLLATE NOCASE,
            count       INTEGER DEFAULT 1,
            PRIMARY KEY (nick_id, word)
        );

        -- Global channel word frequency
        CREATE TABLE IF NOT EXISTS channel_words (
            network      TEXT NOT NULL DEFAULT 'unknown',
            channel      TEXT NOT NULL COLLATE NOCASE,
            word         TEXT NOT NULL COLLATE NOCASE,
            count        INTEGER DEFAULT 1,
            last_used_by TEXT,
            PRIMARY KEY (network, channel, word)
        );

        -- Random quote log
        CREATE TABLE IF NOT EXISTS quotes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nick_id     INTEGER REFERENCES nicks(id) ON DELETE SET NULL,
            network     TEXT NOT NULL DEFAULT 'unknown',
            channel     TEXT NOT NULL COLLATE NOCASE,
            quote       TEXT NOT NULL,
            ts          INTEGER DEFAULT 0
        );

        -- URL log — deduplicated, tracks count and last poster
        CREATE TABLE IF NOT EXISTS urls (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nick_id     INTEGER REFERENCES nicks(id) ON DELETE SET NULL,
            network     TEXT NOT NULL DEFAULT 'unknown',
            channel     TEXT NOT NULL COLLATE NOCASE,
            url         TEXT NOT NULL,
            count       INTEGER DEFAULT 1,
            ts          INTEGER DEFAULT 0,
            UNIQUE(network, channel, url)
        );

        -- Kick log with context
        CREATE TABLE IF NOT EXISTS kick_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            network     TEXT NOT NULL DEFAULT 'unknown',
            channel     TEXT NOT NULL COLLATE NOCASE,
            kicker      TEXT,
            victim      TEXT,
            reason      TEXT,
            context     TEXT,   -- JSON array of recent lines
            ts          INTEGER DEFAULT 0
        );

        -- Topic history
        CREATE TABLE IF NOT EXISTS topics (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            network     TEXT NOT NULL DEFAULT 'unknown',
            channel     TEXT NOT NULL COLLATE NOCASE,
            topic       TEXT NOT NULL,
            set_by      TEXT,
            ts          INTEGER DEFAULT 0
        );

        -- Hourly user count (for activity graph)
        CREATE TABLE IF NOT EXISTS hourly_users (
            network     TEXT NOT NULL DEFAULT 'unknown',
            channel     TEXT NOT NULL COLLATE NOCASE,
            hour        INTEGER NOT NULL,
            user_sum    INTEGER DEFAULT 0,
            user_count  INTEGER DEFAULT 0,
            PRIMARY KEY (network, channel, hour)
        );

        -- Per-nick hourly activity (heatmap data)
        CREATE TABLE IF NOT EXISTS hourly_activity (
            nick_id     INTEGER NOT NULL REFERENCES nicks(id) ON DELETE CASCADE,
            hour        INTEGER NOT NULL,
            lines       INTEGER DEFAULT 0,
            PRIMARY KEY (nick_id, hour)
        );

        -- Channel peak users
        CREATE TABLE IF NOT EXISTS peaks (
            network     TEXT NOT NULL DEFAULT 'unknown',
            channel     TEXT NOT NULL COLLATE NOCASE,
            period      INTEGER NOT NULL DEFAULT 0,
            peak        INTEGER DEFAULT 0,
            PRIMARY KEY (network, channel, period)
        );

        -- Recent channel activity log (for kick context etc.)
        CREATE TABLE IF NOT EXISTS chanlog (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            network     TEXT NOT NULL DEFAULT 'unknown',
            channel     TEXT NOT NULL COLLATE NOCASE,
            nick        TEXT,
            line        TEXT,
            type        INTEGER DEFAULT 0,
            ts          INTEGER DEFAULT 0
        );

        -- Ignored nicks/masks — scoped by (network, channel)
        -- channel='*' means network-wide
        CREATE TABLE IF NOT EXISTS ignored (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            network     TEXT NOT NULL DEFAULT '*',
            channel     TEXT NOT NULL DEFAULT '*' COLLATE NOCASE,
            pattern     TEXT NOT NULL COLLATE NOCASE,
            added_by    TEXT,
            added_at    INTEGER DEFAULT 0,
            UNIQUE(network, channel, pattern)
        );

        CREATE INDEX IF NOT EXISTS idx_ignored ON ignored(network, pattern);

        -- Bot masters — allowed to use admin commands
        CREATE TABLE IF NOT EXISTS masters (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            network         TEXT NOT NULL DEFAULT '*',
            pattern         TEXT NOT NULL COLLATE NOCASE,
            added_by        TEXT,
            added_at        INTEGER DEFAULT 0,
            password_hash   TEXT,
            masks           TEXT,   -- space-separated host masks for auto-auth
            UNIQUE(network, pattern)
        );

        -- Per-channel configuration (stats_url, etc.)
        CREATE TABLE IF NOT EXISTS channel_config (
            network TEXT NOT NULL,
            channel TEXT NOT NULL COLLATE NOCASE,
            key     TEXT NOT NULL,
            value   TEXT,
            PRIMARY KEY (network, channel, key)
        );
        CREATE INDEX IF NOT EXISTS idx_masters ON masters(network, pattern);
        -- Ignored nicks/masks (excluded from stats display and tracking)
        CREATE TABLE IF NOT EXISTS ignored (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            network     TEXT NOT NULL DEFAULT '*',
            pattern     TEXT NOT NULL COLLATE NOCASE,
            added_by    TEXT,
            added_at    INTEGER DEFAULT 0,
            UNIQUE(network, pattern)
        );

        CREATE INDEX IF NOT EXISTS idx_ignored ON ignored(network, pattern);
        -- Per-smiley frequency (which specific smiley was used most)
        CREATE TABLE IF NOT EXISTS smiley_freq (
            nick_id     INTEGER NOT NULL REFERENCES nicks(id) ON DELETE CASCADE,
            network     TEXT NOT NULL,
            channel     TEXT NOT NULL COLLATE NOCASE,
            smiley      TEXT NOT NULL,
            count       INTEGER DEFAULT 1,
            PRIMARY KEY (nick_id, smiley)
        );
        CREATE INDEX IF NOT EXISTS idx_smiley_freq ON smiley_freq(network, channel);

        -- Nick references (which nicks are mentioned most in messages)
        CREATE TABLE IF NOT EXISTS nick_refs (
            network     TEXT NOT NULL,
            channel     TEXT NOT NULL COLLATE NOCASE,
            mentioned   TEXT NOT NULL COLLATE NOCASE,
            by_nick     TEXT,
            count       INTEGER DEFAULT 1,
            PRIMARY KEY (network, channel, mentioned)
        );

        CREATE TABLE IF NOT EXISTS karma (
            network     TEXT NOT NULL,
            channel     TEXT NOT NULL COLLATE NOCASE,
            nick        TEXT NOT NULL COLLATE NOCASE,
            score       INTEGER DEFAULT 0,
            PRIMARY KEY (network, channel, nick)
        );

        CREATE INDEX IF NOT EXISTS idx_nicks_lookup ON nicks(nick, network, channel);
        CREATE INDEX IF NOT EXISTS idx_stats_nick   ON stats(nick_id);
        CREATE INDEX IF NOT EXISTS idx_quotes_chan  ON quotes(network, channel);
        CREATE INDEX IF NOT EXISTS idx_urls_chan    ON urls(network, channel);
        CREATE INDEX IF NOT EXISTS idx_chanlog_chan ON chanlog(network, channel, ts);
        """)
    _migrate()
    import logging as _log
    _log.getLogger("database").debug(f"Database initialized at {DB_PATH}")


def _migrate():
    """Add any missing columns to existing DBs (idempotent)."""
    migrations = [
        ("nicks",    "is_bot",        "INTEGER DEFAULT 0"),
        ("peaks",    "peak_at",       "INTEGER DEFAULT 0"),
        ("ignored",  "channel",       "TEXT NOT NULL DEFAULT '*'"),
        ("masters",  "password_hash", "TEXT"),
        ("masters",  "masks",         "TEXT"),
        ("stats",    "sad",           "INTEGER DEFAULT 0"),
        ("stats",    "caps",          "INTEGER DEFAULT 0"),
        ("stats",    "violent",       "INTEGER DEFAULT 0"),
        ("stats",    "foul",          "INTEGER DEFAULT 0"),
        ("stats",    "monologues",    "INTEGER DEFAULT 0"),
        ("stats",    "attacked",      "INTEGER DEFAULT 0"),
        ("stats",    "violent_ex",    "TEXT"),
        ("stats",    "attacked_ex",   "TEXT"),
        ("stats",    "caps_ex",       "TEXT"),
        ("stats",    "foul_ex",       "TEXT"),
        ("stats",    "action_ex",     "TEXT"),
        ("channel_words", "last_used_by", "TEXT"),
        ("channel_words", "display_word", "TEXT"),
        ("stats", "op_given",     "INTEGER DEFAULT 0"),
        ("stats", "op_taken",     "INTEGER DEFAULT 0"),
        ("stats", "op_got",       "INTEGER DEFAULT 0"),
        ("stats", "deop_got",     "INTEGER DEFAULT 0"),
        ("stats", "voice_given",  "INTEGER DEFAULT 0"),
        ("stats", "voice_taken",  "INTEGER DEFAULT 0"),
        ("stats", "voice_got",    "INTEGER DEFAULT 0"),
        ("stats", "devoice_got",  "INTEGER DEFAULT 0"),
        ("stats", "halfop_given", "INTEGER DEFAULT 0"),
        ("stats", "halfop_taken", "INTEGER DEFAULT 0"),
        ("stats", "halfop_got",   "INTEGER DEFAULT 0"),
        ("stats", "dehalfop_got", "INTEGER DEFAULT 0"),
        ("urls",     "count",         "INTEGER DEFAULT 1"),
    ]
    with get_conn() as conn:
        for table, column, col_def in migrations:
            existing = [row[1] for row in
                        conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
                print(f"Migration: added {table}.{column}")


# ─── Nick Management ────────────────────────────────────────────────────────

def get_or_create_nick(nick: str, network: str, channel: str,
                        host: str = None) -> int:
    """Return nick_id, creating the nick record if needed."""
    now = int(time.time())
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM nicks WHERE nick=? AND network=? AND channel=?",
            (nick, network, channel)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE nicks SET last_seen=?, last_host=COALESCE(?,last_host) WHERE id=?",
                (now, host, row["id"])
            )
            return row["id"]
        conn.execute(
            "INSERT INTO nicks(nick,network,channel,last_host,last_seen,first_seen) VALUES(?,?,?,?,?,?)",
            (nick, network, channel, host, now, now)
        )
        nick_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Initialize stat rows for all periods
        for period in range(4):
            conn.execute(
                "INSERT OR IGNORE INTO stats(nick_id, period) VALUES(?,?)",
                (nick_id, period)
            )
        return nick_id


def touch_nick(nick: str, network: str, channel: str, host: str = None):
    now = int(time.time())
    with get_conn() as conn:
        conn.execute(
            "UPDATE nicks SET last_seen=?, last_host=COALESCE(?,last_host) WHERE nick=? AND network=? AND channel=?",
            (now, host, nick, network, channel)
        )


def expire_nicks(days: int, network: str = None):
    cutoff = int(time.time()) - (days * 86400)
    with get_conn() as conn:
        if network:
            conn.execute("DELETE FROM nicks WHERE last_seen < ? AND network=?", (cutoff, network))
        else:
            conn.execute("DELETE FROM nicks WHERE last_seen < ?", (cutoff,))


# ─── Stats Increment ─────────────────────────────────────────────────────────

STAT_COLS = {
    "words", "letters", "lines", "actions", "modes", "bans",
    "kicks", "kick_given", "nicks", "joins", "smileys", "sad", "caps", "violent", "attacked", "foul", "monologues", "questions", "minutes", "topics",
    "op_given", "op_taken", "op_got", "deop_got",
    "voice_given", "voice_taken", "voice_got", "devoice_got",
    "halfop_given", "halfop_taken", "halfop_got", "dehalfop_got",
}

def incr(nick_id: int, stat: str, value: int = 1):
    """Increment a stat for all periods (total + today + week + month)."""
    if stat not in STAT_COLS:
        raise ValueError(f"Unknown stat: {stat}")
    with get_conn() as conn:
        for period in range(4):
            conn.execute(
                f"UPDATE stats SET {stat}={stat}+? WHERE nick_id=? AND period=?",
                (value, nick_id, period)
            )
        # Track hourly activity for lines
        if stat == "lines":
            hour = int(time.strftime("%H"))
            conn.execute(
                "INSERT INTO hourly_activity(nick_id,hour,lines) VALUES(?,?,?) "
                "ON CONFLICT(nick_id,hour) DO UPDATE SET lines=lines+?",
                (nick_id, hour, value, value)
            )


def get_stats(nick_id: int, period: int = 0) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM stats WHERE nick_id=? AND period=?",
            (nick_id, period)
        ).fetchone()


# ─── Top Lists ───────────────────────────────────────────────────────────────

def get_top(network: str, channel: str, stat: str,
             period: int = 0, limit: int = 10) -> List[Dict]:
    """Return top N nicks for a given stat."""
    if stat not in STAT_COLS and stat != "wpl":
        raise ValueError(f"Unknown stat: {stat}")
    with get_conn() as conn:
        if stat == "wpl":
            rows = conn.execute("""
                SELECT n.nick,
                       CASE WHEN s.lines > 0 THEN CAST(s.words AS REAL)/s.lines ELSE 0 END AS value
                FROM nicks n
                JOIN stats s ON s.nick_id=n.id
                WHERE n.network=? AND n.channel=? AND s.period=? AND s.lines>5
                ORDER BY value DESC LIMIT ?
            """, (network, channel, period, limit)).fetchall()
        else:
            rows = conn.execute(f"""
                SELECT n.nick, s.{stat} AS value
                FROM nicks n
                JOIN stats s ON s.nick_id=n.id
                WHERE n.network=? AND n.channel=? AND s.period=?
                ORDER BY s.{stat} DESC LIMIT ?
            """, (network, channel, period, limit)).fetchall()
        return [dict(r) for r in rows]


def get_rank(nick: str, network: str, channel: str,
              stat: str, period: int = 0) -> Tuple[int, int]:
    """Return (rank, total_users) for a nick."""
    with get_conn() as conn:
        if stat == "wpl":
            value_row = conn.execute("""
                SELECT CASE WHEN s.lines>0 THEN CAST(s.words AS REAL)/s.lines ELSE 0 END as val
                FROM nicks n JOIN stats s ON s.nick_id=n.id
                WHERE n.nick=? AND n.network=? AND n.channel=? AND s.period=?
            """, (nick, network, channel, period)).fetchone()
            if not value_row:
                return 0, 0
            val = value_row["val"]
            rank = conn.execute("""
                SELECT COUNT(*)+1 FROM nicks n JOIN stats s ON s.nick_id=n.id
                WHERE n.network=? AND n.channel=? AND s.period=?
                AND CASE WHEN s.lines>0 THEN CAST(s.words AS REAL)/s.lines ELSE 0 END > ?
            """, (network, channel, period, val)).fetchone()[0]
        else:
            value_row = conn.execute(f"""
                SELECT s.{stat} as val FROM nicks n JOIN stats s ON s.nick_id=n.id
                WHERE n.nick=? AND n.network=? AND n.channel=? AND s.period=?
            """, (nick, network, channel, period)).fetchone()
            if not value_row:
                return 0, 0
            val = value_row["val"]
            rank = conn.execute(f"""
                SELECT COUNT(*)+1 FROM nicks n JOIN stats s ON s.nick_id=n.id
                WHERE n.network=? AND n.channel=? AND s.period=? AND s.{stat}>?
            """, (network, channel, period, val)).fetchone()[0]
        total = conn.execute(
            "SELECT COUNT(*) FROM nicks WHERE network=? AND channel=?",
            (network, channel)
        ).fetchone()[0]
        return rank, total


def get_nick_all_stats(nick: str, network: str, channel: str,
                        period: int = 0) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT n.nick, n.last_seen, n.first_seen, n.last_host, s.*
            FROM nicks n JOIN stats s ON s.nick_id=n.id
            WHERE n.nick=? AND n.network=? AND n.channel=? AND s.period=?
        """, (nick, network, channel, period)).fetchone()
        return dict(row) if row else None


# ─── Word Stats ───────────────────────────────────────────────────────────────

def incr_word(nick_id: int, network: str, channel: str, word: str,
              nick: str = "", count: int = 1):
    word_key = word.lower()          # lowercase key for counting/dedup
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO wordstats(nick_id,network,channel,word,count) VALUES(?,?,?,?,?) "
            "ON CONFLICT(nick_id,word) DO UPDATE SET count=count+?",
            (nick_id, network, channel, word_key, count, count)
        )
        conn.execute(
            """INSERT INTO channel_words(network,channel,word,count,last_used_by,display_word) VALUES(?,?,?,?,?,?)
               ON CONFLICT(network,channel,word) DO UPDATE SET
                 count=count+?,
                 last_used_by=excluded.last_used_by,
                 display_word=excluded.display_word""",
            (network, channel, word_key, count, nick, word, count)
        )


def get_top_words_nick(nick_id: int, limit: int = 10) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT word, count FROM wordstats WHERE nick_id=? ORDER BY count DESC LIMIT ?",
            (nick_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def get_top_words_channel(network: str, channel: str, limit: int = 10) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT COALESCE(display_word, word) as word, count, last_used_by FROM channel_words WHERE network=? AND channel=? ORDER BY count DESC LIMIT ?",
            (network, channel, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def get_vocables(nick_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM wordstats WHERE nick_id=?", (nick_id,)
        ).fetchone()
        return row["c"] if row else 0


def reset_daily_words():
    """Reset word stats (called daily)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM wordstats")
        conn.execute("DELETE FROM channel_words")


# ─── Quotes ──────────────────────────────────────────────────────────────────

def add_quote(nick_id: int, network: str, channel: str, text: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO quotes(nick_id,network,channel,quote,ts) VALUES(?,?,?,?,?)",
            (nick_id, network, channel, text, int(time.time()))
        )


def get_random_quote(network: str, channel: str) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT n.nick, q.quote, q.ts FROM quotes q
            LEFT JOIN nicks n ON n.id=q.nick_id
            WHERE q.network=? AND q.channel=?
            ORDER BY RANDOM() LIMIT 1
        """, (network, channel)).fetchone()
        return dict(row) if row else None


def get_quote_for_nick(nick: str, network: str, channel: str) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT n.nick, q.quote, q.ts FROM quotes q
            JOIN nicks n ON n.id=q.nick_id
            WHERE n.nick=? AND q.network=? AND q.channel=?
            ORDER BY RANDOM() LIMIT 1
        """, (nick, network, channel)).fetchone()
        return dict(row) if row else None


# ─── URLs ────────────────────────────────────────────────────────────────────

def add_url(nick_id: int, network: str, channel: str, url: str):
    now = int(time.time())
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO urls(nick_id,network,channel,url,count,ts) VALUES(?,?,?,?,1,?)
               ON CONFLICT(network,channel,url) DO UPDATE SET
                 count=count+1, nick_id=excluded.nick_id, ts=excluded.ts""",
            (nick_id, network, channel, url, now)
        )


def get_recent_urls(network: str, channel: str, limit: int = 10) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT n.nick, u.url, u.count, u.ts FROM urls u
            LEFT JOIN nicks n ON n.id=u.nick_id
            WHERE u.network=? AND u.channel=?
            ORDER BY u.count DESC, u.ts DESC LIMIT ?
        """, (network, channel, limit)).fetchall()
        return [dict(r) for r in rows]


# ─── Kick Log ────────────────────────────────────────────────────────────────

def add_kick(network: str, channel: str, kicker: str, victim: str,
              reason: str, context: str = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO kick_log(network,channel,kicker,victim,reason,context,ts) VALUES(?,?,?,?,?,?,?)",
            (network, channel, kicker, victim, reason, context, int(time.time()))
        )


def get_recent_kicks(network: str, channel: str, limit: int = 5) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM kick_log WHERE network=? AND channel=? ORDER BY ts DESC LIMIT ?",
            (network, channel, limit)
        ).fetchall()
        return [dict(r) for r in rows]


# ─── Topics ──────────────────────────────────────────────────────────────────

def add_topic(network: str, channel: str, topic: str, set_by: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO topics(network,channel,topic,set_by,ts) VALUES(?,?,?,?,?)",
            (network, channel, topic, set_by, int(time.time()))
        )


def get_recent_topics(network: str, channel: str, limit: int = 5) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM topics WHERE network=? AND channel=? ORDER BY ts DESC LIMIT ?",
            (network, channel, limit)
        ).fetchall()
        return [dict(r) for r in rows]


# ─── Hourly / Activity ───────────────────────────────────────────────────────

def get_hourly_activity(nick_id: int) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT hour, lines FROM hourly_activity WHERE nick_id=? ORDER BY hour",
            (nick_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_channel_hourly(network: str, channel: str) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT ha.hour, SUM(ha.lines) as lines
            FROM hourly_activity ha
            JOIN nicks n ON n.id=ha.nick_id
            WHERE n.network=? AND n.channel=?
            GROUP BY ha.hour ORDER BY ha.hour
        """, (network, channel)).fetchall()
        return [dict(r) for r in rows]


def update_peak(network: str, channel: str, period: int, user_count: int):
    now = int(time.time())
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT peak FROM peaks WHERE network=? AND channel=? AND period=?",
            (network, channel, period)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO peaks(network,channel,period,peak,peak_at) VALUES(?,?,?,?,?)",
                (network, channel, period, user_count, now)
            )
        elif user_count > existing["peak"]:
            conn.execute(
                "UPDATE peaks SET peak=?, peak_at=? WHERE network=? AND channel=? AND period=?",
                (user_count, now, network, channel, period)
            )


def get_peak(network: str, channel: str, period: int = 0) -> dict:
    """Returns dict with 'peak' (int) and 'peak_at' (int timestamp)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT peak, peak_at FROM peaks WHERE network=? AND channel=? AND period=?",
            (network, channel, period)
        ).fetchone()
        return {"peak": row["peak"], "peak_at": row["peak_at"]} if row else {"peak": 0, "peak_at": 0}


# ─── Channel Log (for kick context) ─────────────────────────────────────────

def add_chanlog(network: str, channel: str, nick: str, line: str, type_: int = 0):
    now = int(time.time())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chanlog(network,channel,nick,line,type,ts) VALUES(?,?,?,?,?,?)",
            (network, channel, nick, line, type_, now)
        )
        # Keep only last 100 lines per channel
        conn.execute("""
            DELETE FROM chanlog WHERE id NOT IN (
                SELECT id FROM chanlog WHERE network=? AND channel=?
                ORDER BY ts DESC LIMIT 100
            ) AND network=? AND channel=?
        """, (network, channel, network, channel))


def get_chanlog(network: str, channel: str, limit: int = 10) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM chanlog WHERE network=? AND channel=? ORDER BY ts DESC LIMIT ?",
            (network, channel, limit)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


# ─── Period Reset ─────────────────────────────────────────────────────────────

def reset_period(period: int):
    """Reset stats for a given period (1=daily, 2=weekly, 3=monthly).
    Peak is intentionally never reset — it's an all-time record."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE stats SET
                words=0, letters=0, lines=0, actions=0, modes=0, bans=0,
                kicks=0, kick_given=0, nicks=0, joins=0, smileys=0, sad=0,
                caps=0, violent=0, attacked=0, foul=0, monologues=0,
                questions=0, minutes=0, topics=0
            WHERE period=?
        """, (period,))


# ─── Channel / Nick Listing ───────────────────────────────────────────────────

def get_channels(network: str = None) -> List[Dict]:
    with get_conn() as conn:
        if network:
            rows = conn.execute(
                "SELECT DISTINCT network, channel FROM nicks WHERE network=?", (network,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT network, channel FROM nicks"
            ).fetchall()
        return [dict(r) for r in rows]


def get_nick_list(network: str, channel: str) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT n.nick, n.last_seen, n.first_seen, n.is_bot, s.lines, s.words
            FROM nicks n JOIN stats s ON s.nick_id=n.id
            WHERE n.network=? AND n.channel=? AND s.period=0
            ORDER BY n.nick COLLATE NOCASE
        """, (network, channel)).fetchall()
        return [dict(r) for r in rows]


def count_users(network: str, channel: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM nicks WHERE network=? AND channel=?",
            (network, channel)
        ).fetchone()
        return row["c"] if row else 0


# ─── Ignore List ─────────────────────────────────────────────────────────────

def add_ignore(pattern: str, network: str = '*', added_by: str = None):
    """Add a nick or mask to the ignore list."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO ignored(network,pattern,added_by,added_at) VALUES(?,?,?,?)",
            (network, pattern.lower(), added_by, int(time.time()))
        )


def del_ignore(pattern: str, network: str = '*'):
    """Remove a nick or mask from the ignore list."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM ignored WHERE network=? AND pattern=?",
            (network, pattern.lower())
        )


def list_ignores(network: str = None) -> List[Dict]:
    with get_conn() as conn:
        if network:
            rows = conn.execute(
                "SELECT * FROM ignored WHERE network=? OR network='*' ORDER BY pattern",
                (network,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM ignored ORDER BY network, pattern").fetchall()
        return [dict(r) for r in rows]


def is_ignored(nick: str, network: str, host: str = None) -> bool:
    """
    Return True if nick (or nick!user@host) matches any ignore pattern.

    Patterns are matched against:
      - the nick alone              e.g. ChanServ, *Bot*
      - the full hostmask           e.g. *!*@services.ptirc.org
    If host is provided, both forms are checked.
    """
    import fnmatch
    nick_l = nick.lower()
    full_mask = f"{nick_l}!{host.lower()}" if host else None

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT pattern FROM ignored WHERE network=? OR network='*'",
            (network,)
        ).fetchall()

    for row in rows:
        pat = row["pattern"]
        # Hostmask pattern (contains ! or @) — match against full mask
        if '!' in pat or '@' in pat:
            if full_mask and fnmatch.fnmatch(full_mask, pat):
                return True
        else:
            # Nick-only pattern
            if fnmatch.fnmatch(nick_l, pat) or nick_l == pat:
                return True
    return False

# ─── Masters ─────────────────────────────────────────────────────────────────

def add_master(pattern: str, network: str = '*', added_by: str = None):
    """Add a nick or hostmask as a bot master."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO masters(network,pattern,added_by,added_at) VALUES(?,?,?,?)",
            (network, pattern.lower(), added_by, int(time.time()))
        )


def del_master(pattern: str, network: str = '*'):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM masters WHERE network=? AND pattern=?",
            (network, pattern.lower())
        )


def list_masters(network: str = None) -> List[Dict]:
    with get_conn() as conn:
        if network:
            rows = conn.execute(
                "SELECT * FROM masters WHERE network=? OR network='*' ORDER BY pattern",
                (network,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM masters ORDER BY network, pattern").fetchall()
        return [dict(r) for r in rows]


def is_master(nick: str, network: str, host: str = None) -> bool:
    """
    Return True if nick (or nick!user@host) matches any master pattern.
    Same matching logic as is_ignored: patterns with ! or @ match the full
    hostmask, others match the nick only.
    """
    import fnmatch
    nick_l = nick.lower()
    full_mask = f"{nick_l}!{host.lower()}" if host else None

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT pattern FROM masters WHERE network=? OR network='*'",
            (network,)
        ).fetchall()

    for row in rows:
        pat = row["pattern"]
        if '!' in pat or '@' in pat:
            if full_mask and fnmatch.fnmatch(full_mask, pat):
                return True
        else:
            if fnmatch.fnmatch(nick_l, pat) or nick_l == pat:
                return True
    return False


# ─── Channel Config (stats_url etc.) ─────────────────────────────────────────

def set_channel_config(network: str, channel: str, key: str, value: str):
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_config (
                network TEXT NOT NULL,
                channel TEXT NOT NULL COLLATE NOCASE,
                key     TEXT NOT NULL,
                value   TEXT,
                PRIMARY KEY (network, channel, key)
            )
        """)
        conn.execute(
            "INSERT OR REPLACE INTO channel_config(network,channel,key,value) VALUES(?,?,?,?)",
            (network, channel, key, value)
        )


def get_channel_config(network: str, channel: str, key: str) -> Optional[str]:
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM channel_config WHERE network=? AND channel=? AND key=?",
                (network, channel, key)
            ).fetchone()
            if row:
                return row["value"]
            # Fall back to network-wide setting
            row = conn.execute(
                "SELECT value FROM channel_config WHERE network=? AND channel='*' AND key=?",
                (network, key)
            ).fetchone()
            return row["value"] if row else None
    except Exception:
        return None


# ─── Updated ignore functions (channel-scoped) ────────────────────────────────

def add_ignore(pattern: str, network: str = '*', channel: str = '*', added_by: str = None):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO ignored(network,channel,pattern,added_by,added_at)
               VALUES(?,?,?,?,?)""",
            (network, channel, pattern.lower(), added_by, int(time.time()))
        )


def del_ignore(pattern: str, network: str = '*', channel: str = '*'):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM ignored WHERE network=? AND channel=? AND pattern=?",
            (network, channel, pattern.lower())
        )


def list_ignores(network: str = None, channel: str = None) -> List[Dict]:
    with get_conn() as conn:
        if network and channel:
            rows = conn.execute(
                """SELECT * FROM ignored
                   WHERE network=? AND (channel=? OR channel='*')
                   ORDER BY channel, pattern""",
                (network, channel)
            ).fetchall()
        elif network:
            rows = conn.execute(
                "SELECT * FROM ignored WHERE network=? ORDER BY channel, pattern",
                (network,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ignored ORDER BY network, channel, pattern"
            ).fetchall()
        return [dict(r) for r in rows]


def is_ignored(nick: str, network: str, host: str = None, channel: str = None) -> bool:
    import fnmatch
    nick_l = nick.lower()
    full_mask = f"{nick_l}!{host.lower()}" if host else None

    with get_conn() as conn:
        if channel:
            rows = conn.execute(
                """SELECT pattern FROM ignored
                   WHERE (network=? OR network='*')
                   AND (channel=? OR channel='*')""",
                (network, channel)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT pattern FROM ignored
                   WHERE (network=? OR network='*') AND channel='*'""",
                (network,)
            ).fetchall()

    for row in rows:
        pat = row["pattern"]
        if '!' in pat or '@' in pat:
            if full_mask and fnmatch.fnmatch(full_mask, pat):
                return True
        else:
            if fnmatch.fnmatch(nick_l, pat) or nick_l == pat:
                return True
    return False


# ─── Master with password (for auth system) ───────────────────────────────────

def get_master_by_nick(nick: str) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM masters WHERE lower(pattern)=lower(?)",
            (nick,)
        ).fetchone()
        return dict(row) if row else None


def list_masters_global() -> List[Dict]:
    """Return all masters with their mask lists."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM masters ORDER BY pattern"
        ).fetchall()
        return [dict(r) for r in rows]


def add_master_with_password(nick: str, password_hash: str, added_by: str = None):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO masters(network,pattern,added_by,added_at,password_hash)
               VALUES('*',?,?,?,?)""",
            (nick.lower(), added_by, int(time.time()), password_hash)
        )


def del_master_by_nick(nick: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM masters WHERE lower(pattern)=lower(?)", (nick,))


# ─── Smiley frequency ─────────────────────────────────────────────────────────

def incr_smiley(nick_id: int, network: str, channel: str, smiley: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO smiley_freq(nick_id,network,channel,smiley,count) VALUES(?,?,?,?,1)
               ON CONFLICT(nick_id,smiley) DO UPDATE SET count=count+1""",
            (nick_id, network, channel, smiley)
        )

def get_top_smileys(network: str, channel: str, limit: int = 10) -> List[Dict]:
    """Return most-used smileys channel-wide with who used them most."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT sf.smiley,
                   SUM(sf.count) as total,
                   n.nick as top_user
            FROM smiley_freq sf
            JOIN nicks n ON n.id = sf.nick_id
            WHERE sf.network=? AND sf.channel=?
            GROUP BY sf.smiley
            ORDER BY total DESC LIMIT ?
        """, (network, channel, limit)).fetchall()
        return [dict(r) for r in rows]


# ─── Nick references ──────────────────────────────────────────────────────────

def incr_nick_ref(network: str, channel: str, mentioned: str, by_nick: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO nick_refs(network,channel,mentioned,by_nick,count) VALUES(?,?,?,?,1)
               ON CONFLICT(network,channel,mentioned) DO UPDATE SET count=count+1, by_nick=excluded.by_nick, mentioned=excluded.mentioned""",
            (network, channel, mentioned, by_nick)
        )

def get_top_nick_refs(network: str, channel: str, limit: int = 10) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT nr.mentioned, nr.count, nr.by_nick
            FROM nick_refs nr
            WHERE nr.network=? AND nr.channel=?
            ORDER BY nr.count DESC LIMIT ?
        """, (network, channel, limit)).fetchall()
        return [dict(r) for r in rows]

# ─── Example lines ────────────────────────────────────────────────────────────

def set_example(nick_id: int, kind: str, text: str):
    """Store an example line for a stat (caps_ex, violent_ex, foul_ex, action_ex).
    Only stores if not already set — keeps the first good example."""
    col = kind  # e.g. 'caps_ex', 'violent_ex'
    with get_conn() as conn:
        # Only update if currently NULL
        conn.execute(
            f"UPDATE stats SET {col}=? WHERE nick_id=? AND period=0 AND {col} IS NULL",
            (text[:200], nick_id)
        )


def get_example(nick_id: int, kind: str) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT {kind} FROM stats WHERE nick_id=? AND period=0",
            (nick_id,)
        ).fetchone()
        return row[0] if row else None

# ─── Karma ────────────────────────────────────────────────────────────────────

def change_karma(network: str, channel: str, nick: str, delta: int):
    """Increment or decrement karma for a nick (+1 or -1)."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO karma(network,channel,nick,score) VALUES(?,?,?,?)
               ON CONFLICT(network,channel,nick) DO UPDATE SET score=score+?""",
            (network, channel, nick, delta, delta)
        )


def get_karma_top(network: str, channel: str, limit: int = 10) -> List[Dict]:
    """Return top karma nicks (highest score first)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT nick, score FROM karma
               WHERE network=? AND channel=? AND score != 0
               ORDER BY score DESC LIMIT ?""",
            (network, channel, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def get_karma_bottom(network: str, channel: str, limit: int = 5) -> List[Dict]:
    """Return bottom karma nicks (lowest score first)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT nick, score FROM karma
               WHERE network=? AND channel=? AND score != 0
               ORDER BY score ASC LIMIT ?""",
            (network, channel, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def get_karma_nick(network: str, channel: str, nick: str) -> int:
    """Return karma score for a single nick, 0 if unknown."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT score FROM karma WHERE network=? AND channel=? AND nick=? COLLATE NOCASE",
            (network, channel, nick)
        ).fetchone()
        return row["score"] if row else 0

