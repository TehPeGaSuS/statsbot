"""
i18n.py — lightweight PO file loader and translator.

Usage:
    from i18n import t, get_lang

    lang = get_lang(network, channel)   # looks up channel_config DB
    t("section_big_numbers", lang)      # -> "Big numbers"
    t("bignums_questions", lang,        # -> "Is PeGaSuS a little bit..."
      nick="PeGaSuS", pct="27.3")

Falls back to en_US for any missing translation.
PO files live in locale/<lang>.po  (e.g. locale/pt_PT.po)
"""

import os
import re
import logging
from typing import Dict, Optional

log = logging.getLogger("i18n")

SUPPORTED = ("en_US", "pt_PT", "fr_FR", "it_IT")
DEFAULT   = "en_US"

# Cache: {lang: {msgid: msgstr}}
_catalogues: Dict[str, Dict[str, str]] = {}

_LOCALE_DIR = os.path.join(os.path.dirname(__file__), "locale")


def _load(lang: str) -> Dict[str, str]:
    """Parse a .po file and return {msgid: msgstr}."""
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
                msgid = re.match(r'^msgid "(.*)"\s*$', line)
                msgid = msgid.group(1) if msgid else ""
                in_msgid = True; in_msgstr = False
            elif line.startswith("msgstr "):
                msgstr = re.match(r'^msgstr "(.*)"\s*$', line)
                msgstr = msgstr.group(1) if msgstr else ""
                in_msgstr = True; in_msgid = False
            elif line.startswith('"') and in_msgid:
                cont = re.match(r'^"(.*)"\s*$', line)
                if cont: msgid += cont.group(1)
            elif line.startswith('"') and in_msgstr:
                cont = re.match(r'^"(.*)"\s*$', line)
                if cont: msgstr += cont.group(1)
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
    """Force-reload all cached catalogues (e.g. after editing PO files)."""
    _catalogues.clear()


def get_lang(network: str, channel: str) -> str:
    """Look up the language configured for this channel. Falls back to en_US."""
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
    """Persist language choice for a channel. Returns False if lang unsupported."""
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
    Translate msgid to lang, interpolating any kwargs.
    Falls back to en_US if translation missing.
    kwargs values that are bold-wrapped get <b>value</b> automatically
    if the key ends with '_b' (e.g. nick_b="PeGaSuS" -> <b>PeGaSuS</b>).
    """
    cat = _get_catalogue(lang)
    en  = _get_catalogue(DEFAULT)
    raw = cat.get(msgid) or en.get(msgid) or msgid
    if not raw:
        return msgid
    # Unescape PO escape sequences
    raw = raw.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
    if kwargs:
        # Bold-wrap _b kwargs
        fmt = {}
        for k, v in kwargs.items():
            if k.endswith("_b"):
                fmt[k[:-2]] = f"<b>{v}</b>"
            else:
                fmt[k] = v
        try:
            return raw.format(**fmt)
        except KeyError:
            return raw
    return raw

# Weekday and month name tables per language
_WEEKDAYS = {
    "en_US": ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"],
    "pt_PT": ["segunda-feira","terça-feira","quarta-feira","quinta-feira","sexta-feira","sábado","domingo"],
    "fr_FR": ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"],
    "it_IT": ["lunedì","martedì","mercoledì","giovedì","venerdì","sabato","domenica"],
}
_MONTHS = {
    "en_US": ["January","February","March","April","May","June",
              "July","August","September","October","November","December"],
    "pt_PT": ["janeiro","fevereiro","março","abril","maio","junho",
              "julho","agosto","setembro","outubro","novembro","dezembro"],
    "fr_FR": ["janvier","février","mars","avril","mai","juin",
              "juillet","août","septembre","octobre","novembre","décembre"],
    "it_IT": ["gennaio","febbraio","marzo","aprile","maggio","giugno",
              "luglio","agosto","settembre","ottobre","novembre","dicembre"],
}


def format_date_long(dt, lang: str = DEFAULT) -> str:
    """Format a datetime as 'Weekday DD Month YYYY - HH:MM:SS' in the given language."""
    weekdays = _WEEKDAYS.get(lang, _WEEKDAYS[DEFAULT])
    months   = _MONTHS.get(lang, _MONTHS[DEFAULT])
    wd  = weekdays[dt.weekday()]
    mon = months[dt.month - 1]
    return f"{wd} {dt.day:02d} {mon} {dt.year} - {dt.strftime('%H:%M:%S')}"
