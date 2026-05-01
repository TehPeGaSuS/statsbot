"""
i18n.py — lightweight PO file loader and translator.

Follows the Anope/gettext convention: msgid is the English string itself.
en_US.po has empty msgstr for every entry — t() returns the msgid as the
English fallback when msgstr is empty or the key is missing.

Usage:
    from i18n import t, get_lang

    lang = get_lang(network, channel)
    t("Big numbers", lang)
    t("{nick} is a very aggressive person. They attacked others {count} times.",
      lang, nick="PeGaSuS", count=3)

Falls back to msgid (the English string) for any missing translation.
PO files live in locale/<lang>.po
"""

import os
import re
import logging
from typing import Dict

log = logging.getLogger("i18n")

SUPPORTED = ("en_US", "pt_PT", "fr_FR", "it_IT")
DEFAULT   = "en_US"

_catalogues: Dict[str, Dict[str, str]] = {}
_LOCALE_DIR = os.path.join(os.path.dirname(__file__), "locale")


def _load(lang: str) -> Dict[str, str]:
    path = os.path.join(_LOCALE_DIR, f"{lang}.po")
    if not os.path.exists(path):
        log.warning(f"i18n: locale file not found: {path}")
        return {}
    catalogue: Dict[str, str] = {}
    msgid = msgstr = ""
    in_msgid = in_msgstr = False
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith("msgid "):
                m = re.match(r'^msgid "(.*)"$', line)
                msgid = m.group(1) if m else ""
                in_msgid = True; in_msgstr = False
            elif line.startswith("msgstr "):
                m = re.match(r'^msgstr "(.*)"$', line)
                msgstr = m.group(1) if m else ""
                in_msgstr = True; in_msgid = False
            elif line.startswith('"') and in_msgid:
                m = re.match(r'^"(.*)"$', line)
                if m: msgid += m.group(1)
            elif line.startswith('"') and in_msgstr:
                m = re.match(r'^"(.*)"$', line)
                if m: msgstr += m.group(1)
            elif line.strip() == "":
                if msgid:
                    catalogue[msgid] = msgstr
                msgid = msgstr = ""
                in_msgid = in_msgstr = False
    if msgid:
        catalogue[msgid] = msgstr
    return catalogue


def _get_catalogue(lang: str) -> Dict[str, str]:
    if lang not in _catalogues:
        _catalogues[lang] = _load(lang)
    return _catalogues[lang]


def reload_catalogues():
    _catalogues.clear()


def get_lang(network: str, channel: str) -> str:
    try:
        from database.models import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM channel_config WHERE network=? AND channel=? AND key='lang'",
                (network, channel)
            ).fetchone()
        if row and row["value"] in SUPPORTED:
            return row["value"]
    except Exception:
        pass
    return DEFAULT


def set_lang(network: str, channel: str, lang: str) -> bool:
    if lang not in SUPPORTED:
        return False
    try:
        from database.models import get_conn
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO channel_config(network, channel, key, value)
                   VALUES(?,?,?,?)
                   ON CONFLICT(network,channel,key) DO UPDATE SET value=excluded.value""",
                (network, channel, "lang", lang)
            )
        return True
    except Exception:
        return False


def t(msgid: str, lang: str = DEFAULT, **kwargs) -> str:
    """
    Translate msgid (the English string) to lang.
    Falls back to msgid when msgstr is empty or missing.
    """
    cat = _get_catalogue(lang)
    raw = cat.get(msgid, "")
    if not raw:
        raw = msgid
    raw = raw.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
    if kwargs:
        try:
            return raw.format(**kwargs)
        except KeyError:
            return raw
    return raw


# ── Date/time helpers ─────────────────────────────────────────────────────────

_EN_WEEKDAYS = "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday"
_EN_MONTHS   = "January,February,March,April,May,June,July,August,September,October,November,December"


def _get_list(msgid: str, lang: str) -> list:
    cat = _get_catalogue(lang)
    raw = cat.get(msgid, "")
    if not raw:
        raw = msgid  # msgid is itself the English comma-separated list
    return [s.strip() for s in raw.split(",")]


def format_date_long(dt, lang: str = DEFAULT) -> str:
    weekdays = _get_list(_EN_WEEKDAYS, lang)
    months   = _get_list(_EN_MONTHS, lang)
    wd  = weekdays[dt.weekday()]
    mon = months[dt.month - 1]
    return f"{wd} {dt.day:02d} {mon} {dt.year} - {dt.strftime('%H:%M:%S')}"
