"""
i18n.py — lightweight PO file loader and translator.

Supports the standard gettext convention used by Anope and others:

    msgid "%d nickname dropped."
    msgid_plural "%d nicknames dropped."
    msgstr[0] "%d nickname removido."
    msgstr[1] "%d nicknames removidos."

Header `Plural-Forms: nplurals=N; plural=EXPR;` is honoured per language.

API:
    t(msgid, lang, **kwargs)                  # singular / non-plural strings
    tn(singular, plural, count, lang, **kw)   # plural-aware

Falls back to the English msgid / msgid_plural when no translation is set.
PO files live in locale/<lang>.po
"""

import os
import re
import logging
from typing import Dict, List, Tuple, Callable

log = logging.getLogger("i18n")

SUPPORTED = ("en_US", "pt_PT", "fr_FR", "it_IT")
DEFAULT   = "en_US"

# catalogue: msgid -> (msgstr, [plural_forms_or_empty])
# When entry has no plural: value is (msgstr, []) and lookup key is msgid.
# When entry has plural:    value is (msgstr_singular_unused, [msgstr[0], msgstr[1], ...])
#                           and we ALSO index it under msgid_plural for safety.
_catalogues: Dict[str, Dict[str, Tuple[str, List[str]]]] = {}
_plural_funcs: Dict[str, Callable[[int], int]] = {}
_nplurals: Dict[str, int] = {}

_LOCALE_DIR = os.path.join(os.path.dirname(__file__), "locale")

# Default Germanic plural rule: 0 if n==1 else 1
def _default_plural(n: int) -> int:
    return 0 if n == 1 else 1


def _compile_plural(expr: str) -> Callable[[int], int]:
    """Compile a Plural-Forms `plural=` expression into a callable.
    The expression uses C ternary syntax; we translate `a ? b : c` to Python
    `(b) if (a) else (c)` using a tiny recursive parser."""
    expr = expr.strip().rstrip(";").strip()

    def parse_ternary(s: str) -> str:
        # Find top-level '?' (ignoring those inside parens)
        depth = 0
        for i, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "?" and depth == 0:
                # find matching ':' at same depth
                d2 = 0
                for j in range(i + 1, len(s)):
                    c2 = s[j]
                    if c2 == "(":
                        d2 += 1
                    elif c2 == ")":
                        d2 -= 1
                    elif c2 == ":" and d2 == 0:
                        cond = s[:i].strip()
                        a = parse_ternary(s[i + 1 : j])
                        b = parse_ternary(s[j + 1 :])
                        return f"(({a}) if ({cond}) else ({b}))"
                break
        return s.strip()

    py = parse_ternary(expr)
    # gettext spec: result may be bool — coerce to int
    code = compile(f"int({py})", "<plural>", "eval")

    def f(n: int) -> int:
        try:
            return eval(code, {"__builtins__": {}}, {"n": int(n)})
        except Exception:
            return _default_plural(n)

    return f


def _unescape(s: str) -> str:
    return s.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")


def _load(lang: str) -> Dict[str, Tuple[str, List[str]]]:
    path = os.path.join(_LOCALE_DIR, f"{lang}.po")
    if not os.path.exists(path):
        log.warning(f"i18n: locale file not found: {path}")
        return {}

    catalogue: Dict[str, Tuple[str, List[str]]] = {}

    msgid = msgid_plural = msgstr = ""
    msgstr_n: Dict[int, str] = {}
    state = None  # "msgid", "msgid_plural", "msgstr", ("msgstr_n", N)

    def flush():
        nonlocal msgid, msgid_plural, msgstr, msgstr_n
        if msgid or msgid_plural:
            if msgid_plural or msgstr_n:
                # plural entry
                forms = [msgstr_n.get(i, "") for i in range(max(msgstr_n) + 1)] if msgstr_n else []
                catalogue[msgid] = (msgstr, forms)
                if msgid_plural:
                    catalogue[msgid_plural] = (msgstr, forms)
            else:
                catalogue[msgid] = (msgstr, [])
        msgid = msgid_plural = msgstr = ""
        msgstr_n = {}

    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if stripped == "" or stripped.startswith("#"):
                if stripped == "":
                    flush()
                    state = None
                continue

            m = re.match(r'^msgid\s+"(.*)"$', line)
            if m:
                flush()
                msgid = m.group(1)
                state = "msgid"
                continue
            m = re.match(r'^msgid_plural\s+"(.*)"$', line)
            if m:
                msgid_plural = m.group(1)
                state = "msgid_plural"
                continue
            m = re.match(r'^msgstr\s+"(.*)"$', line)
            if m:
                msgstr = m.group(1)
                state = "msgstr"
                continue
            m = re.match(r'^msgstr\[(\d+)\]\s+"(.*)"$', line)
            if m:
                idx = int(m.group(1))
                msgstr_n[idx] = m.group(2)
                state = ("msgstr_n", idx)
                continue
            m = re.match(r'^"(.*)"$', line)
            if m and state is not None:
                cont = m.group(1)
                if state == "msgid":
                    msgid += cont
                elif state == "msgid_plural":
                    msgid_plural += cont
                elif state == "msgstr":
                    msgstr += cont
                elif isinstance(state, tuple) and state[0] == "msgstr_n":
                    msgstr_n[state[1]] += cont
        flush()

    # Parse Plural-Forms header (msgid "" entry → msgstr "" with header lines)
    header = catalogue.get("", ("", []))[0]
    nplurals = 2
    plural_fn = _default_plural
    if header:
        m = re.search(r'Plural-Forms:\s*nplurals=(\d+);\s*plural=([^"\n]+);?', header)
        if m:
            try:
                nplurals = int(m.group(1))
                plural_fn = _compile_plural(m.group(2))
            except Exception as e:
                log.warning(f"i18n[{lang}]: bad Plural-Forms header: {e}")

    _nplurals[lang] = nplurals
    _plural_funcs[lang] = plural_fn
    return catalogue


def _get_catalogue(lang: str) -> Dict[str, Tuple[str, List[str]]]:
    if lang not in _catalogues:
        _catalogues[lang] = _load(lang)
        _plural_funcs.setdefault(lang, _default_plural)
        _nplurals.setdefault(lang, 2)
    return _catalogues[lang]


def reload_catalogues():
    _catalogues.clear()
    _plural_funcs.clear()
    _nplurals.clear()


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
    """Translate a non-plural msgid. Falls back to msgid when missing/empty."""
    cat = _get_catalogue(lang)
    entry = cat.get(msgid)
    raw = entry[0] if entry and entry[0] else msgid
    raw = _unescape(raw)
    if kwargs:
        try:
            return raw.format(**kwargs)
        except (KeyError, IndexError):
            return raw
    return raw


def tn(singular: str, plural: str, n: int, lang: str = DEFAULT, **kwargs) -> str:
    """Translate a plural msgid. Picks the correct form for `n` according
    to the target language's Plural-Forms rule. Falls back to English
    singular/plural when no translation is present.

    `n` is the numeric value used *only* for plural-form selection.
    Pass ``count=`` as a keyword argument to override what ``{count}``
    renders as in the format string (e.g. a bold-wrapped string).
    When ``count=`` is not supplied, ``{count}`` defaults to ``n``.
    """
    cat = _get_catalogue(lang)
    entry = cat.get(singular) or cat.get(plural)
    plural_fn = _plural_funcs.get(lang, _default_plural)
    nplurals = _nplurals.get(lang, 2)

    raw = ""
    if entry:
        _, forms = entry
        if forms:
            try:
                idx = plural_fn(n)
                if idx < 0 or idx >= nplurals:
                    idx = _default_plural(n)
                if 0 <= idx < len(forms) and forms[idx]:
                    raw = forms[idx]
            except Exception:
                raw = ""

    if not raw:
        # English fallback: simple Germanic rule
        raw = singular if n == 1 else plural

    raw = _unescape(raw)
    # `count` in the format string defaults to the numeric selector `n`,
    # but callers may pass count= explicitly (e.g. a bold-wrapped value).
    fmt_kwargs = dict(kwargs)
    fmt_kwargs.setdefault("count", n)
    try:
        return raw.format(**fmt_kwargs)
    except (KeyError, IndexError):
        return raw


# ── Date/time helpers ─────────────────────────────────────────────────────────

_EN_WEEKDAYS = "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday"
_EN_MONTHS   = "January,February,March,April,May,June,July,August,September,October,November,December"


def _get_list(msgid: str, lang: str) -> list:
    cat = _get_catalogue(lang)
    entry = cat.get(msgid)
    raw = entry[0] if entry and entry[0] else msgid
    return [s.strip() for s in raw.split(",")]


def format_date_long(dt, lang: str = DEFAULT) -> str:
    weekdays = _get_list(_EN_WEEKDAYS, lang)
    months   = _get_list(_EN_MONTHS, lang)
    wd  = weekdays[dt.weekday()]
    mon = months[dt.month - 1]
    return f"{wd} {dt.day:02d} {mon} {dt.year} - {dt.strftime('%H:%M:%S')}"
