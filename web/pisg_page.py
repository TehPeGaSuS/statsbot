"""
web/pisg_page.py
Pisg-style stats page generator — all sections, full layout.
Called from dashboard.py channel_stats route.
"""

import json
import time
from datetime import datetime
from typing import List, Dict, Optional


def _ts_date(ts: int) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y")


def _ago(ts: int) -> str:
    if not ts:
        return "never"
    diff = int(time.time()) - ts
    if diff < 86400:
        return "today"
    days = diff // 86400
    return "yesterday" if days == 1 else f"{days} days ago"


def _pct(num: int, denom: int) -> str:
    if not denom:
        return "0.0"
    return f"{num / denom * 100:.1f}"


def build_page(network: str, channel: str, period: int, config: dict) -> str:
    """Build the full pisg-style HTML page and return it as a string."""
    from database.models import (
        get_top, get_nick_list, get_top_words_channel,
        get_channel_hourly, get_recent_urls, get_recent_kicks,
        get_recent_topics, get_peak, count_users, get_conn,
        get_top_smileys, get_top_nick_refs, get_quote_for_nick,
        get_random_quote, get_example,
        get_karma_top, get_karma_bottom
    )

    pisg = config.get("pisg", {})
    web  = config.get("web", {})

    # ── Fetch all data ────────────────────────────────────────────────────────
    peak_data    = get_peak(network, channel, 0)
    total_users  = count_users(network, channel)
    hourly       = get_channel_hourly(network, channel)
    hourly_data  = {h["hour"]: h["lines"] for h in hourly}
    # Fetch more words than needed so we can filter by word_length and ignore_words
    _word_limit   = pisg.get("WordHistory", 10)
    _word_length  = pisg.get("WordLength", 5)
    _ignore_words = set(w.lower() for w in pisg.get("IgnoreWords", []))
    _raw_words    = get_top_words_channel(network, channel, _word_limit * 5)
    top_words_ch  = [w for w in _raw_words
                     if len(w["word"]) >= _word_length
                     and w["word"].lower() not in _ignore_words][:_word_limit]
    recent_urls  = get_recent_urls(network, channel, pisg.get("UrlHistory", 10))
    recent_kicks = get_recent_kicks(network, channel, 5)
    recent_topics = get_recent_topics(network, channel, pisg.get("TopicHistory", 5))
    top_smileys  = get_top_smileys(network, channel, pisg.get("SmileyHistory", 10)) if pisg.get("ShowSmileys", True) else []
    nick_refs    = get_top_nick_refs(network, channel, pisg.get("NickHistory", 5)) if pisg.get("ShowMrn", True) else []
    karma_top    = get_karma_top(network, channel, pisg.get("KarmaHistory", 10)) if pisg.get("ShowKarma", True) else []
    karma_bottom = get_karma_bottom(network, channel, 5) if pisg.get("ShowKarma", True) else []

    # Per-nick hourly band totals (used by ShowTime and ShowMostActiveByHour)
    _bands_def = [(0,5), (6,11), (12,17), (18,23)]
    nick_band_lines = {}
    with get_conn() as _hconn:
        _hr_rows = _hconn.execute("""
            SELECT n.nick, ha.hour, ha.lines
            FROM hourly_activity ha
            JOIN nicks n ON n.id=ha.nick_id
            WHERE n.network=? AND n.channel=?
        """, (network, channel)).fetchall()
    for _hr in _hr_rows:
        _hn = _hr["nick"]; _hh = _hr["hour"]; _hl = _hr["lines"]
        if _hn not in nick_band_lines:
            nick_band_lines[_hn] = [0, 0, 0, 0]
        for _bi, (_lo, _hi) in enumerate(_bands_def):
            if _lo <= _hh <= _hi:
                nick_band_lines[_hn][_bi] += _hl
                break

    # Active nicks (sorted by words or lines)
    sort_by  = "words" if pisg.get("SortByWords", True) else "lines"
    top_n    = pisg.get("ActiveNicks", 25)
    top_n2   = pisg.get("ActiveNicks2", 50)
    all_rows = get_top(network, channel, sort_by, period, top_n2)
    all_rows = [r for r in all_rows if r["value"] > 0]
    top_rows = all_rows[:top_n]
    rest_rows = all_rows[top_n:top_n2]

    # Pull full stats for each nick in top table
    nick_stats = {}
    with get_conn() as conn:
        for row in all_rows:
            nick = row["nick"]
            r = conn.execute("""
                SELECT s.*, n.last_seen, n.first_seen, n.id as nick_id
                FROM nicks n JOIN stats s ON s.nick_id=n.id
                WHERE n.nick=? AND n.network=? AND n.channel=? AND s.period=?
            """, (nick, network, channel, period)).fetchone()
            if r:
                nick_stats[nick] = dict(r)

    threshold = pisg.get("BigNumbersThreshold", 50)
    if str(threshold).startswith("sqrt"):
        import math
        max_lines = max((nick_stats[n].get("lines", 0) for n in nick_stats), default=0)
        threshold = int(math.sqrt(max_lines))

    # Qualifying nicks for big numbers (min threshold lines)
    qualified = [n for n in nick_stats if nick_stats[n].get("lines", 0) >= threshold]

    # Channel totals
    with get_conn() as conn:
        totals = conn.execute("""
            SELECT SUM(s.lines) as lines, SUM(s.words) as words,
                   SUM(s.letters) as letters, COUNT(DISTINCT n.id) as nicks
            FROM nicks n JOIN stats s ON s.nick_id=n.id
            WHERE n.network=? AND n.channel=? AND s.period=0 AND s.words>0
        """, (network, channel)).fetchone()

    total_lines  = totals["lines"] or 0
    total_words  = totals["words"] or 0
    avg_cpl      = f"{totals['letters'] / totals['lines']:.1f}" if totals["lines"] else "0"
    period_names = ["all-time", "today", "this week", "this month"]
    title        = web.get("title", "IRC Stats")
    project_url  = web.get("project_url", "https://github.com/TehPeGaSuS/Statsbot")
    # Use the bot's nick for this network as the maintainer string.
    # Checks for a per-network nick override, falls back to bot.nick.
    _net_entry  = next((n for n in config.get("networks", []) if n.get("name") == network), {})
    maintainer  = _net_entry.get("nick") or config.get("bot", {}).get("nick", "")
    now_str      = datetime.now().strftime("%Y-%m-%d %H:%M")
    now_long     = datetime.now().strftime("%A %d %B %Y - %H:%M:%S")

    # Earliest first_seen across all nicks gives us the tracking start date
    with get_conn() as conn:
        _fs = conn.execute(
            "SELECT MIN(first_seen) as fs FROM nicks WHERE network=? AND channel=?",
            (network, channel)
        ).fetchone()
    tracking_start = _fs["fs"] if _fs and _fs["fs"] else None
    if tracking_start:
        from datetime import timedelta
        start_dt  = datetime.fromtimestamp(tracking_start)
        days_tracked = max(1, (datetime.now() - start_dt).days)
    else:
        days_tracked = 0

    # ── HTML construction ─────────────────────────────────────────────────────
    H = []  # HTML buffer

    def h(s): H.append(s)
    def section(title_str):
        h(f'<h2 class="section-title">{title_str}</h2>')
    def hicell(content, small=None, example=None):
        ex_html = f'<br><span class="small"><b>For example, like this:</b><br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{example}</span>' if example else ""
        extra = f'<br><span class="small">{small}</span>' if small else ""
        h(f'<tr><td class="hicell">{content}{extra}{ex_html}</td></tr>')
    # ── Page header ───────────────────────────────────────────────────────────
    h(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{channel} @ {network} — {title}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
:root {{
  --bg:      #0d0d1a;
  --bg2:     #1a1a2e;
  --bg3:     #1e2245;
  --border:  #2a3860;
  --text:    #c8d3f5;
  --muted:   #565f89;
  --faint:   #3d4a6b;
  --blue:    #7aa2f7;
  --green:   #9ece6a;
  --yellow:  #e0af68;
  --red:     #f7768e;
  --cyan:    #7dcfff;
  --tab-act: #3850B8;
  --header-grad: linear-gradient(135deg,#1a1a3e,var(--bg));
}}
body.light {{
  --bg:      #f5f6fa;
  --bg2:     #ffffff;
  --bg3:     #e8eaf0;
  --border:  #c5cade;
  --text:    #1e2245;
  --muted:   #6b7299;
  --faint:   #9299b8;
  --blue:    #3558d6;
  --green:   #3a7d0e;
  --yellow:  #a06000;
  --red:     #c0132a;
  --cyan:    #0077aa;
  --tab-act: #3558d6;
  --header-grad: linear-gradient(135deg,#dde2f5,var(--bg));
}}
@media (prefers-color-scheme: light) {{
  :root:not(.dark-override) {{
    --bg:      #f5f6fa;
    --bg2:     #ffffff;
    --bg3:     #e8eaf0;
    --border:  #c5cade;
    --text:    #1e2245;
    --muted:   #6b7299;
    --faint:   #9299b8;
    --blue:    #3558d6;
    --green:   #3a7d0e;
    --yellow:  #a06000;
    --red:     #c0132a;
    --cyan:    #0077aa;
    --tab-act: #3558d6;
    --header-grad: linear-gradient(135deg,#dde2f5,var(--bg));
  }}
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: var(--bg); color: var(--text);
        font-family: 'Segoe UI', Tahoma, sans-serif; font-size: 14px; }}

/* Header */
.page-header {{ background: var(--header-grad);
                border-bottom: 1px solid var(--border); padding: 1.5rem 2rem; }}
.page-header a {{ color: var(--blue); text-decoration: none; font-size: .85rem; }}
.page-header h1 {{ font-size: 1.8rem; color: var(--blue); margin: .3rem 0 .2rem; }}
.page-header .meta {{ color: var(--muted); font-size: .82rem; }}
.page-header .subtitle {{ color: var(--muted); font-size: .85rem;
                           margin-top: .3rem; line-height: 1.5; }}

/* Layout */
.container {{ max-width: 1200px; margin: 1.5rem auto; padding: 0 1.5rem; }}

/* Section titles */
h2.section-title {{ font-size: .8rem; color: var(--blue); text-transform: uppercase;
                    letter-spacing: 1.5px; padding: .5rem 0 .4rem;
                    border-bottom: 1px solid var(--border); margin: 2rem 0 .8rem; }}

/* Main nick table */
/* Responsive table wrapper — horizontal scroll on mobile, invisible on desktop */
.tscroll {{ overflow-x: auto; -webkit-overflow-scrolling: touch; margin-bottom: 1rem; }}
.tscroll > table {{ margin-bottom: 0; min-width: 480px; }}

.nick-table {{ width: 100%; border-collapse: collapse; margin-bottom: 1rem; }}
.nick-table th {{ background: var(--bg3); color: var(--blue); text-align: left;
                  padding: .5rem .7rem; font-size: .78rem; text-transform: uppercase;
                  letter-spacing: .5px; }}
.nick-table td {{ padding: .38rem .7rem; border-bottom: 1px solid var(--bg3); font-size: .88rem; }}
.nick-table .rank {{ color: var(--muted); width: 36px; }}
.rank-1 td {{ background: rgba(122,162,247,.08); }}
.nick-name {{ color: var(--green); font-weight: bold; }}
.val {{ color: var(--yellow); }}
.quote-cell {{ color: var(--muted); font-style: italic; font-size: .82rem; max-width: 320px;
               overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.bar-wrap {{ background: var(--bg3); border-radius: 3px; height: 10px; width: 140px; }}
.bar-fill {{ height: 100%; border-radius: 3px;
             background: linear-gradient(90deg, var(--tab-act), var(--blue)); }}

/* Also active */
.also-active {{ color: var(--muted); font-size: .82rem; margin: .5rem 0 1.5rem; }}
.also-active span {{ margin-right: .4rem; }}

/* Big numbers / hicell */
table.bignums {{ width: 100%; border-collapse: collapse; margin-bottom: .5rem; }}
td.hicell {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
             padding: .7rem 1rem; font-size: .9rem; margin-bottom: .5rem; }}
table.bignums tr {{ display: block; margin-bottom: .5rem; }}
table.bignums td {{ display: block; }}
.small {{ color: var(--muted); font-size: .82rem; }}
.example {{ color: var(--muted); font-size: .8rem; font-style: italic; margin-top: .2rem; }}
b {{ color: var(--cyan); }}

/* Activity chart */
.chart-wrap {{ height: 140px; margin-bottom: 1rem; }}

/* Per-nick hour chart */
.byhour-table {{ width: 100%; border-collapse: collapse; margin-bottom: 1rem; }}
.byhour-table th {{ background: var(--bg3); color: var(--blue); text-align: left;
                    padding: .45rem .8rem; font-size: .8rem; text-transform: uppercase; letter-spacing: .5px; }}
.byhour-table td {{ padding: .38rem .8rem; border-bottom: 1px solid var(--bg3); font-size: .88rem; }}
.byhour-table .rank {{ color: var(--muted); width: 36px; text-align: center; }}
.byhour-table .rank-1 {{ color: var(--yellow); font-weight: bold; }}
.byhour-cell {{ color: var(--fg); }}
.byhour-cell .cnt {{ color: var(--muted); font-size: .78rem; margin-left: .3rem; }}
.band-legend {{ display: flex; gap: 1.5rem; justify-content: center; font-size: .82rem; color: var(--muted); margin: .5rem 0 1rem; }}
.bh-bar.blue-h   {{ background: #4a7ab5; border-radius: 2px; }}
.bh-bar.green-h  {{ background: #4a9b5e; border-radius: 2px; }}
.bh-bar.yellow-h {{ background: #b5963a; border-radius: 2px; }}
.bh-bar.red-h    {{ background: #b54a4a; border-radius: 2px; }}

/* Word cloud */
.word-cloud {{ display: flex; flex-wrap: wrap; gap: .35rem; margin-bottom: 1rem; }}
.word-tag {{ background: var(--bg3); border: 1px solid var(--border); border-radius: 4px;
             padding: .18rem .45rem; font-size: .78rem; color: var(--blue); }}
.word-tag .wc {{ color: var(--muted); font-size: .72rem; margin-left: .3rem; }}

/* Tables (smileys, refs, urls etc.) */
.info-table {{ width: 100%; border-collapse: collapse; margin-bottom: 1rem; }}
.info-table th {{ background: var(--bg3); color: var(--blue); text-align: left;
                  padding: .4rem .7rem; font-size: .78rem; text-transform: uppercase; }}
.info-table td {{ padding: .35rem .7rem; border-bottom: 1px solid var(--bg3); font-size: .85rem; }}
.info-table .rank {{ color: var(--muted); width: 32px; }}

/* Topics */
.topic-row {{ padding: .5rem 0; border-bottom: 1px solid var(--bg3); font-size: .85rem; }}
.topic-row .topic-text {{ font-style: italic; color: var(--text); }}
.topic-row .topic-meta {{ color: var(--muted); font-size: .78rem; margin-top: .15rem; }}

/* Misc items */
.misc-item {{ padding: .35rem 0; border-bottom: 1px solid var(--bg3); font-size: .85rem; }}
.misc-item .by {{ color: var(--muted); margin-left: .4rem; font-size: .78rem; }}
.misc-item .when {{ color: var(--blue); font-size: .75rem; margin-left: .4rem; }}

/* Summary strip */
.summary-strip {{ display: flex; gap: .8rem; flex-wrap: wrap; margin-bottom: 1.5rem; }}
.s-card {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
           padding: .6rem 1.1rem; flex: 1; min-width: 110px; text-align: center; }}
.s-card .sv {{ font-size: 1.3rem; font-weight: bold; color: var(--blue); }}
.s-card .sl {{ font-size: .7rem; color: var(--muted); margin-top: .2rem;
               text-transform: uppercase; letter-spacing: .4px; }}

/* Period tabs */
.tabs {{ display: flex; gap: .4rem; margin-bottom: 1.5rem; flex-wrap: wrap; }}
.tab {{ padding: .35rem .9rem; border-radius: 20px; border: 1px solid var(--border);
        color: var(--muted); text-decoration: none; font-size: .82rem; }}
.tab.active {{ background: var(--tab-act); color: #fff; border-color: var(--tab-act); }}
.tab:hover:not(.active) {{ border-color: var(--blue); color: var(--blue); }}

/* Legend */
.legend {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
           padding: 1rem; font-size: .82rem; color: var(--muted); margin-top: 2rem; }}
.legend b {{ color: var(--text); }}


/* ── Theme toggle ── */
.theme-toggle {{
  position: fixed; bottom: 1.2rem; right: 1.2rem; z-index: 999;
  background: var(--bg2); border: 1px solid var(--border);
  border-radius: 50%; width: 2.4rem; height: 2.4rem;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; font-size: 1.1rem; box-shadow: 0 2px 8px rgba(0,0,0,.3);
  transition: background .2s, border-color .2s;
  user-select: none;
}}
.theme-toggle:hover {{ border-color: var(--blue); }}
.footer {{ text-align: center; color: var(--muted); font-size: .78rem;
           margin: 2rem 0 1rem; border-top: 1px solid var(--bg3); padding-top: 1rem; }}

/* ── Mobile ────────────────────────────────────────────────────────── */
@media (max-width: 600px) {{
  .container {{ padding: 0 .6rem; }}
  .page-header {{ padding: 1rem; }}
  .page-header h1 {{ font-size: 1.3rem; }}
  .summary-strip {{ gap: .4rem; }}
  .s-card {{ min-width: 80px; padding: .4rem .6rem; }}
  .s-card .sv {{ font-size: 1rem; }}
  .tabs {{ gap: .3rem; }}
  .tab {{ padding: .3rem .7rem; font-size: .78rem; }}
  .tscroll > table {{ font-size: .8rem; }}

}}

/* Live user count badge */
#live-count {{ display: inline-block; background: var(--bg3); border-radius: 4px;
               padding: .1rem .5rem; font-size: .78rem; color: var(--cyan); margin-left: .5rem; }}
</style>
</head>
<body>
<div class="page-header">
  <a href="/">← All networks</a>
  <h1>{channel} <span style="color:var(--muted);font-size:1rem">on {network}</span>
    <span id="live-count">●</span>
  </h1>
  <p class="subtitle">Statistics generated on {now_long}</p>
  <p class="subtitle">During this {days_tracked}-day reporting period, a total of
  <b>{total_users}</b> different nicks were represented on {channel}.</p>
</div>
<div class="container">
""")

    # ── Period tabs ───────────────────────────────────────────────────────────
    h('<div class="tabs">')
    for i, pn in enumerate(period_names):
        active = ' active' if i == period else ''
        h(f'<a class="tab{active}" href="?period={i}">{pn}</a>')
    h('</div>')

    # ── Summary strip ─────────────────────────────────────────────────────────
    top_words_nick  = get_top(network, channel, "words",   period, 1)
    top_smiles_nick = get_top(network, channel, "smileys", period, 1)
    top_sad_nick    = get_top(network, channel, "sad",     period, 1)
    top_mins_nick   = get_top(network, channel, "minutes", period, 1)
    top_words_nick  = [r for r in top_words_nick  if r["value"] > 0]
    top_smiles_nick = [r for r in top_smiles_nick if r["value"] > 0]
    top_sad_nick    = [r for r in top_sad_nick    if r["value"] > 0]
    top_mins_nick   = [r for r in top_mins_nick   if r["value"] > 0]

    h('<div class="summary-strip">')
    h(f'<div class="s-card"><div class="sv" id="users-card">{total_users}</div><div class="sl">users</div></div>')
    peak_label = f"peak users · {_ts_date(peak_data['peak_at'])}" if peak_data["peak_at"] else "peak users"
    h(f'<div class="s-card"><div class="sv">{peak_data["peak"]}</div><div class="sl">{peak_label}</div></div>')
    h(f'<div class="s-card"><div class="sv">{top_words_nick[0]["nick"] if top_words_nick else "—"}</div><div class="sl">top talker</div></div>')
    h(f'<div class="s-card"><div class="sv">{top_smiles_nick[0]["nick"] if top_smiles_nick else "—"}</div><div class="sl">happiest :)</div></div>')
    h(f'<div class="s-card"><div class="sv">{top_sad_nick[0]["nick"] if top_sad_nick else "—"}</div><div class="sl">saddest :(</div></div>')
    h(f'<div class="s-card"><div class="sv">{top_mins_nick[0]["nick"] if top_mins_nick else "—"}</div><div class="sl">most online</div></div>')
    h('</div>')

    # ── Activity by hour ──────────────────────────────────────────────────────
    if pisg.get("ShowActiveTimes", True):
        section("Most active times")
        h(f'<div class="chart-wrap"><canvas id="hourChart"></canvas></div>')
        if pisg.get("ShowLegend", True):
            h('<div class="band-legend">')
            for _lc, _ll in [("bh-bar blue-h","0-5"), ("bh-bar green-h","6-11"),
                              ("bh-bar yellow-h","12-17"), ("bh-bar red-h","18-23")]:
                h(f'<span><span class="{_lc}" style="width:40px;height:15px;'
                  f'display:inline-block;vertical-align:middle"></span> = {_ll}</span>')
            h('</div>')

    # ── Main nick table ───────────────────────────────────────────────────────
    section("Most active nicks")
    show_wpl      = pisg.get("ShowWpl", True)
    show_cpl      = pisg.get("ShowCpl", False)
    show_lastseen = pisg.get("ShowLastSeen", True)
    show_words    = pisg.get("ShowWords", True)
    show_lines    = pisg.get("ShowLines", True)
    show_quote    = pisg.get("ShowRandQuote", True)
    show_time     = pisg.get("ShowTime", True)

    h('<div class="tscroll"><table class="nick-table"><thead><tr>')
    h('<th class="rank">#</th><th>Nick</th>')
    if show_lines: h('<th>Number of lines</th>')
    if show_words: h('<th>Number of Words</th>')
    if show_wpl:   h('<th>Words per line</th>')
    if show_cpl:   h('<th>Chars per line</th>')
    if show_time:  h('<th>When?</th>')
    if show_lastseen: h('<th>Last seen</th>')
    if show_quote: h('<th>Random quote</th>')
    h('</tr></thead><tbody>')

    max_val = top_rows[0]["value"] if top_rows else 1
    for i, row in enumerate(top_rows):
        nick = row["nick"]
        st   = nick_stats.get(nick, {})
        lines = st.get("lines", 0)
        words = st.get("words", 0)
        wpl   = f"{words/lines:.1f}" if lines else "0"
        cpl   = f"{st.get('letters',0)/lines:.1f}" if lines else "0"
        last  = _ago(st.get("last_seen", 0))
        pct   = int(row["value"] / max_val * 100) if max_val else 0
        rank_cls = ' class="rank-1"' if i == 0 else ""

        # Get a random quote within length bounds (MinQuote/MaxQuote)
        q = ""
        if show_quote:
            min_q = pisg.get("MinQuote", 25)
            max_q = pisg.get("MaxQuote", 65)
            from database.models import get_quote_for_nick, get_conn as _gc2
            # Try to find a quote in the right length range
            with _gc2() as _qc:
                _qrows = _qc.execute(
                    """SELECT q.quote FROM quotes q JOIN nicks n ON n.id=q.nick_id
                       WHERE n.nick=? AND n.network=? AND n.channel=?
                       AND length(q.quote) BETWEEN ? AND ?
                       ORDER BY RANDOM() LIMIT 1""",
                    (nick, network, channel, min_q, max_q)
                ).fetchone()
            if _qrows:
                q = _qrows["quote"][:80]
            else:
                # Fall back to any quote
                qr = get_quote_for_nick(nick, network, channel)
                q  = qr["quote"][:80] if qr else ""

        h(f'<tr{rank_cls}>')
        h(f'<td class="rank">{i+1}</td>')
        h(f'<td><span class="nick-name">{nick}</span><br>'
          f'<div class="bar-wrap"><div class="bar-fill" style="width:{pct}%"></div></div></td>')
        if show_lines: h(f'<td class="val">{lines:,}</td>')
        if show_words: h(f'<td class="val">{words:,}</td>')
        if show_wpl:   h(f'<td>{wpl}</td>')
        if show_cpl:   h(f'<td>{cpl}</td>')
        if show_time:
            _nb = nick_band_lines.get(nick, [0, 0, 0, 0])
            _nb_total = sum(_nb) or 1
            _band_cols = ["blue-h", "green-h", "yellow-h", "red-h"]
            _bars = ""
            for _bi, _bv in enumerate(_nb):
                _bw = max(1, int(_bv / _nb_total * 40)) if _bv else 0
                if _bw:
                    _bars += (f'<span class="bh-bar {_band_cols[_bi]}" ')
                    _bars += (f'style="width:{_bw}px;display:inline-block;')
                    _bars += (f'height:15px;vertical-align:middle"></span>')
            h(f'<td style="white-space:nowrap">{_bars}</td>')
        if show_lastseen: h(f'<td class="small">{last}</td>')
        if show_quote: h(f'<td class="quote-cell" title="{q}">{q}</td>')
        h('</tr>')

    h('</tbody></table></div>')

    # "These didn't make the top"
    if rest_rows:
        h('<div class="also-active"><i>These didn\'t make the top:</i> ')
        for row in rest_rows:
            st = nick_stats.get(row["nick"], {})
            v  = st.get(sort_by, row["value"])
            h(f'<span class="word-tag">{row["nick"]} ({v:,})</span>')
        h('</div>')
    # "By the way, there were X other nicks"
    total_other = len(all_rows) - len(top_rows) - len(rest_rows)
    if total_other > 0:
        h(f'<p class="small" style="margin:.5rem 0 1.5rem"><b>By the way, there were {total_other} other nicks.</b></p>')

    # ── Big numbers ───────────────────────────────────────────────────────────
    section("Big numbers")
    h('<table class="bignums">')

    def _bignum_row(text, subtext=None):
        hicell(text, subtext)

    # Questions
    if pisg.get("ShowBigNumbers", True) and qualified:
        qdata = {n: (nick_stats[n].get("questions",0), nick_stats[n].get("lines",1))
                 for n in qualified if nick_stats[n].get("questions",0) > 0}
        if qdata:
            ranked = sorted(qdata, key=lambda n: qdata[n][0]/qdata[n][1], reverse=True)
            n1, (q1, l1) = ranked[0], qdata[ranked[0]]
            pct1 = f"{q1/l1*100:.1f}"
            text = f"Is <b>{n1}</b> a little bit slower than the rest, or just asking too many questions?  {pct1}% lines contained a question!"
            sub  = f"<b>{ranked[1]}</b> didn't know that much either. {qdata[ranked[1]][0]/qdata[ranked[1]][1]*100:.1f}% of their lines were questions." if len(ranked) > 1 else None
            _bignum_row(text, sub)

    # Shouting (CAPS)
    if pisg.get("ShowBigNumbers", True) and qualified:
        cdata = {n: (nick_stats[n].get("caps",0), nick_stats[n].get("lines",1))
                 for n in qualified if nick_stats[n].get("caps",0) > 0}
        if cdata:
            ranked = sorted(cdata, key=lambda n: cdata[n][0]/cdata[n][1], reverse=True)

            # Entry 1 — loudest / most caps (rank 1 and 2 as sub)
            n1, (c1, l1) = ranked[0], cdata[ranked[0]]
            pct1 = f"{c1/l1*100:.1f}"
            ex1  = nick_stats.get(n1, {}).get("caps_ex", None)
            ex1_fmt = f"&lt;{n1}&gt; {ex1}" if ex1 else None
            if float(pct1) >= 10:
                text1 = f"The loudest one was <b>{n1}</b>, who yelled {pct1}% of the time!"
                sub1  = None
                if len(ranked) > 1:
                    p2 = f"{cdata[ranked[1]][0]/cdata[ranked[1]][1]*100:.1f}"
                    sub1 = f"Another <i>old yeller</i> was <b>{ranked[1]}</b>, who shouted {p2}% of the time!"
                hicell(text1, sub1, example=ex1_fmt)

            # Entry 2 — shift-key / Caps-Lock (rank 1 if low %, otherwise rank 2+)
            # Show for the first nick whose % < 10, or rank 2 if rank 1 was already shown above
            caps_low = [n for n in ranked if cdata[n][0]/cdata[n][1]*100 < 10]
            # If rank1 was shown as "loudest", show shift-key entry for rank2 onwards
            shift_candidates = ranked[1:] if float(pct1) >= 10 else ranked
            if shift_candidates:
                ns = shift_candidates[0]
                ps = f"{cdata[ns][0]/cdata[ns][1]*100:.1f}"
                exs = nick_stats.get(ns, {}).get("caps_ex", None)
                exs_fmt = f"&lt;{ns}&gt; {exs}" if exs else None
                text2 = f"It seems that <b>{ns}</b>'s shift-key is hanging: {ps}% of the time they wrote UPPERCASE."
                sub2  = None
                if len(shift_candidates) > 1:
                    ns2 = shift_candidates[1]
                    ps2 = f"{cdata[ns2][0]/cdata[ns2][1]*100:.1f}"
                    sub2 = f"<b>{ns2}</b> just forgot to deactivate their Caps-Lock. They wrote UPPERCASE {ps2}% of the time."
                hicell(text2, sub2, example=exs_fmt)

    # Violent
    if pisg.get("ShowBigNumbers", True) and qualified:
        vdata = {n: nick_stats[n].get("violent",0) for n in qualified if nick_stats[n].get("violent",0) > 0}
        if vdata:
            ranked = sorted(vdata, key=vdata.get, reverse=True)
            _vc = vdata[ranked[0]]
            text = f"<b>{ranked[0]}</b> is a very aggressive person. They attacked others <b>{_vc}</b> {'time' if _vc == 1 else 'times'}."
            sub  = f"<b>{ranked[1]}</b> can't control their aggressions, either. They picked on others <b>{vdata[ranked[1]]}</b> times." if len(ranked) > 1 else None
            ex = nick_stats.get(ranked[0], {}).get("violent_ex", None)
            hicell(text, sub, example=ex)
            # Attacked victims
            atat = {n: nick_stats[n].get("attacked", 0) for n in qualified if nick_stats[n].get("attacked", 0) > 0}
            if atat:
                av = sorted(atat, key=atat.get, reverse=True)
                ax = nick_stats.get(av[0], {}).get("attacked_ex", None)
                _ac = atat[av[0]]
                atext = f"Poor <b>{av[0]}</b>, nobody likes them. They were attacked <b>{_ac}</b> {'time' if _ac == 1 else 'times'}."
                asub  = f"<b>{av[1]}</b> seems to be unliked too. They got beaten <b>{atat[av[1]]}</b> times." if len(av) > 1 else None
                hicell(atext, asub, example=ax)
        else:
            _bignum_row("Nobody beat anyone up. Everybody was friendly.")

    # Smiles
    if pisg.get("ShowBigNumbers", True) and qualified:
        sdata = {n: (nick_stats[n].get("smileys",0), nick_stats[n].get("lines",1))
                 for n in qualified if nick_stats[n].get("smileys",0) > 0}
        if sdata:
            ranked = sorted(sdata, key=lambda n: sdata[n][0]/sdata[n][1], reverse=True)
            n1, (s1, l1) = ranked[0], sdata[ranked[0]]
            pct1 = f"{s1/l1*100:.1f}"
            text = f"<b>{n1}</b> brings happiness to the world. {pct1}% of their lines contained smiling faces. :)"
            sub  = f"<b>{ranked[1]}</b> isn't a sad person either, smiling {sdata[ranked[1]][0]/sdata[ranked[1]][1]*100:.1f}% of the time." if len(ranked) > 1 else None
            _bignum_row(text, sub)
        else:
            _bignum_row("Nobody smiles in this channel! Cheer up guys and girls.")

    # Sad
    if pisg.get("ShowBigNumbers", True) and qualified:
        sddata = {n: (nick_stats[n].get("sad",0), nick_stats[n].get("lines",1))
                  for n in qualified if nick_stats[n].get("sad",0) > 0}
        if sddata:
            ranked = sorted(sddata, key=lambda n: sddata[n][0]/sddata[n][1], reverse=True)
            n1, (s1, l1) = ranked[0], sddata[ranked[0]]
            pct1 = f"{s1/l1*100:.1f}"
            text = f"<b>{n1}</b> seems to be sad at the moment: {pct1}% of their lines contained sad faces. :("
            sub  = f"<b>{ranked[1]}</b> is also a sad person, frowning {sddata[ranked[1]][0]/sddata[ranked[1]][1]*100:.1f}% of the time." if len(ranked) > 1 else None
            _bignum_row(text, sub)
        else:
            _bignum_row("Nobody is sad in this channel! What a happy channel. :-)")

    # Line lengths
    if pisg.get("ShowBigNumbers", True) and qualified:
        ldata = {n: nick_stats[n].get("letters",0) / max(nick_stats[n].get("lines",1),1)
                 for n in qualified if nick_stats[n].get("letters",0) > 0}
        if ldata:
            longest  = max(ldata, key=ldata.get)
            shortest = min(ldata, key=ldata.get)
            ch_avg   = sum(ldata.values()) / len(ldata)
            _bignum_row(
                f"<b>{longest}</b> wrote the longest lines, averaging {ldata[longest]:.1f} letters per line.",
                f"#channel average was {ch_avg:.1f} letters per line.".replace("#channel", channel)
            )
            if longest != shortest:
                # Find second shortest for "tight-lipped too" sub
                sorted_short = sorted(ldata, key=ldata.get)
                short_sub = (f"<b>{sorted_short[1]}</b> was tight-lipped, too, "
                             f"averaging {ldata[sorted_short[1]]:.1f} characters."
                             if len(sorted_short) > 1 and sorted_short[1] != longest else None)
                _bignum_row(
                    f"<b>{shortest}</b> wrote the shortest lines, averaging {ldata[shortest]:.1f} characters per line.",
                    short_sub
                )

    # Words total + wpl big numbers
    if qualified:
        wdata_total = {n: nick_stats[n].get("words", 0) for n in qualified if nick_stats[n].get("words", 0) > 0}
        if wdata_total:
            top_words_n = sorted(wdata_total, key=wdata_total.get, reverse=True)
            tw1 = top_words_n[0]
            sub_words = (f"{tw1}'s faithful follower, <b>{top_words_n[1]}</b>, "
                         f"didn't speak so much: {wdata_total[top_words_n[1]]:,} words."
                         if len(top_words_n) > 1 else None)
            _bignum_row(f"<b>{tw1}</b> spoke a total of {wdata_total[tw1]:,} words!", sub_words)
        wpl_data = {n: nick_stats[n].get("words", 0) / max(nick_stats[n].get("lines", 1), 1)
                    for n in qualified if nick_stats[n].get("words", 0) > 0}
        if wpl_data:
            best_wpl   = max(wpl_data, key=wpl_data.get)
            ch_avg_wpl = total_words / total_lines if total_lines else 0
            _bignum_row(
                f"<b>{best_wpl}</b> wrote an average of {wpl_data[best_wpl]:.2f} words per line.",
                f"Channel average was {ch_avg_wpl:.2f} words per line."
            )

    h('</table>')

    # ── Most active nicks by hour ─────────────────────────────────────────────
    if pisg.get("ShowMostActiveByHour", True):
        bands = [(0,5,"0-5"), (6,11,"6-11"), (12,17,"12-17"), (18,23,"18-23")]
        if nick_band_lines:
            n_bh_rows = pisg.get("ActiveNicksByHour", 10)
            band_ranked = []
            for bi in range(4):
                ranked = sorted(nick_band_lines.keys(),
                                key=lambda n: nick_band_lines[n][bi], reverse=True)
                ranked = [(n, nick_band_lines[n][bi]) for n in ranked if nick_band_lines[n][bi] > 0]
                band_ranked.append(ranked[:n_bh_rows])
            max_rows = max(len(b) for b in band_ranked)
            if max_rows > 0:
                section("Most active nicks by hours")
                show_bh_graph = pisg.get("ShowMostActiveByHourGraph", True)
                # Per-band max for scaling bars (independent per column like pisg)
                band_max = [band_ranked[bi][0][1] if band_ranked[bi] else 1 for bi in range(4)]
                band_colors = ["blue-h", "green-h", "yellow-h", "red-h"]
                h('<div class="tscroll"><table class="byhour-table"><thead><tr>')
                h('<th class="rank">#</th>')
                for _, _, label in bands:
                    h(f'<th>{label}</th>')
                h('</tr></thead><tbody>')
                for i in range(max_rows):
                    rank_cls = ' rank-1' if i == 0 else ''
                    h(f'<tr><td class="rank{rank_cls}">{i+1}</td>')
                    for bi in range(4):
                        if i < len(band_ranked[bi]):
                            bnick, bcnt = band_ranked[bi][i]
                            if show_bh_graph:
                                bar_w = max(1, int(bcnt / band_max[bi] * 100))
                                bar = (f'<span class="bh-bar {band_colors[bi]}" '
                                       f'style="width:{bar_w}px;display:inline-block;'
                                       f'height:15px;vertical-align:middle;margin-right:4px"></span>')
                            else:
                                bar = ''
                            h(f'<td class="byhour-cell">{bar}{bnick}'
                              f'<span class="cnt"> - {bcnt}</span></td>')
                        else:
                            h('<td></td>')
                    h('</tr>')
                h('</tbody></table></div>')

    # ── Most used words ───────────────────────────────────────────────────────
    if pisg.get("ShowMuw", True) and top_words_ch:
        section("Most used words")
        h('<div class="tscroll"><table class="info-table"><thead><tr>')
        h('<th class="rank">#</th><th>Word</th><th>Number of Uses</th><th>Last Used by</th>')
        h('</tr></thead><tbody>')
        for i_w, w in enumerate(top_words_ch):
            last = w.get("last_used_by") or ""
            h(f'<tr><td class="rank">{i_w+1}</td>'
              f'<td style="font-family:monospace">{w["word"]}</td>'
              f'<td class="val">{w["count"]}</td>'
              f'<td class="small">{last}</td></tr>')
        h('</tbody></table></div>')

    # ── Most referenced nicks ─────────────────────────────────────────────────
    if pisg.get("ShowMrn", True) and nick_refs:
        section("Most referenced nicks")
        h('<div class="tscroll"><table class="info-table"><thead><tr>'
          '<th class="rank">#</th><th>Nick</th><th>Number of Uses</th><th>Last by</th>'
          '</tr></thead><tbody>')
        for i, r in enumerate(nick_refs):
            h(f'<tr><td class="rank">{i+1}</td><td class="nick-name">{r["mentioned"]}</td>'
              f'<td class="val">{r["count"]}</td><td class="small">{r.get("by_nick","")}</td></tr>')
        h('</tbody></table></div>')

    # ── Smiley frequency ──────────────────────────────────────────────────────
    if pisg.get("ShowSmileys", True) and top_smileys:
        section("Smileys :-)")
        h('<div class="tscroll"><table class="info-table"><thead><tr>'
          '<th class="rank">#</th><th>Smiley</th><th>Uses</th><th>Top user</th>'
          '</tr></thead><tbody>')
        for i, r in enumerate(top_smileys):
            h(f'<tr><td class="rank">{i+1}</td><td style="font-size:1.1rem">{r["smiley"]}</td>'
              f'<td class="val">{r["total"]}</td><td class="small">{r.get("top_user","")}</td></tr>')
        h('</tbody></table></div>')

    # ── Karma ─────────────────────────────────────────────────────────────────
    if pisg.get("ShowKarma", True) and (karma_top or karma_bottom):
        section("Karma")
        h('<div class="tscroll"><table class="info-table"><thead><tr>'
          '<th class="rank">#</th><th>Nick</th><th>Score</th>'
          '</tr></thead><tbody>')
        for i, r in enumerate(karma_top):
            score = r["score"]
            colour = "var(--green)" if score > 0 else "var(--red)"
            sign   = "+" if score > 0 else ""
            h(f'<tr><td class="rank">{i+1}</td>'
              f'<td class="nick-name">{r["nick"]}</td>'
              f'<td class="val" style="color:{colour}">{sign}{score}</td></tr>')
        top_nicks = {r["nick"].lower() for r in karma_top}
        for r in karma_bottom:
            if r["nick"].lower() not in top_nicks:
                score = r["score"]
                h(f'<tr><td class="rank">—</td>'
                  f'<td class="nick-name">{r["nick"]}</td>'
                  f'<td class="val" style="color:var(--red)">{score}</td></tr>')
        h('</tbody></table></div>')

    # ── Most referenced URLs ──────────────────────────────────────────────────
    if pisg.get("ShowMru", True) and recent_urls:
        section("Most referenced URLs")
        h('<div class="tscroll"><table class="info-table"><thead><tr>'
          '<th>URL</th><th>Number of Uses</th><th>Last by</th><th>When</th>'
          '</tr></thead><tbody>')
        for u in recent_urls:
            url = u["url"]
            disp = url[:70] + "…" if len(url) > 70 else url
            h(f'<tr><td><a href="{url}" target="_blank" rel="noopener" style="color:var(--blue)">{disp}</a></td>'
              f'<td class="val">{u.get("count", 1)}</td>'
              f'<td class="small">{u.get("nick","")}</td>'
              f'<td class="small">{_ago(u["ts"])}</td></tr>')
        h('</tbody></table></div>')

    # ── Recent kicks (pisg-style prose) ──────────────────────────────────────
    if recent_kicks:
        h('<table class="bignums">')
        for k in recent_kicks:
            victim  = k["victim"]
            kicker  = k["kicker"]
            reason  = (k["reason"] or "").strip()
            when    = _ago(k["ts"])
            text    = f"<b>{victim}</b> wasn't very popular, getting kicked by <b>{kicker}</b> {when}!"
            example = f"*** {victim} was kicked by {kicker}"
            if reason:
                example += f" ({reason})"
            hicell(text, example=example)
        h('</table>')

    # ── Latest topics ─────────────────────────────────────────────────────────
    if pisg.get("ShowTopics", True) and recent_topics:
        section("Latest Topics")
        h('<div class="tscroll"><table class="info-table">')
        for t in recent_topics:
            _tdt = datetime.fromtimestamp(t["ts"]) if t["ts"] else None
            _twhen = (_tdt.strftime("%-d days ago at %H:%M")
                      if _tdt and (datetime.now() - _tdt).days > 1
                      else ("today at " + _tdt.strftime("%H:%M") if _tdt
                            and _tdt.date() == datetime.now().date()
                            else ("yesterday at " + _tdt.strftime("%H:%M") if _tdt
                                  else _ago(t["ts"]))))
            h(f'<tr>'
              f'<td class="topic-text" style="font-style:italic">{t["topic"]}</td>'
              f'<td style="font-weight:bold;white-space:nowrap">{_twhen} by {t["set_by"]}</td>'
              f'</tr>')
        _tc = len(recent_topics)
        h(f'<tr><td colspan="2" style="text-align:center;font-size:.78rem;color:var(--muted)">'
          f'The topic was set {_tc} {"time" if _tc == 1 else "times"}.</td></tr>')
        h('</table></div>')

    # ── Other numbers ─────────────────────────────────────────────────────────
    section("Other interesting numbers")
    h('<table class="bignums">')

    # Got kicked (victim)
    if pisg.get("ShowBigNumbers", True):
        kt = get_top(network, channel, "kicks", period, 3)
        kt = [r for r in kt if r["value"] > 0]
        if kt:
            _kv = kt[0]["value"]
            text = f"<b>{kt[0]['nick']}</b> wasn't very popular, getting kicked {_kv} {'time' if _kv == 1 else 'times'}!"
            sub  = f"<b>{kt[1]['nick']}</b> seemed to be hated too: {kt[1]['value']} kicks were received." if len(kt) > 1 else None
            _bignum_row(text, sub)

    # Most kicks given
    if pisg.get("ShowBigNumbers", True):
        kg = get_top(network, channel, "kick_given", period, 3)
        kg = [r for r in kg if r["value"] > 0]
        if kg:
            _kg0v = kg[0]["value"]
            text = f"<b>{kg[0]['nick']}</b> is either insane or just a fair op, kicking a total of {_kg0v} {'person' if _kg0v == 1 else 'people'}!"
            sub  = f"{kg[0]['nick']}'s faithful follower, <b>{kg[1]['nick']}</b>, kicked about {kg[1]['value']} people." if len(kg) > 1 else None
            _bignum_row(text, sub)


    # Ops given / taken (pisg order: given then taken)
    show_ops     = pisg.get("ShowOps",     True)
    show_voice   = pisg.get("ShowVoice",   False)  # pisg default: disabled
    show_halfops = pisg.get("ShowHalfops", False)  # pisg default: disabled

    def _get_opvoice_top(stat, limit=3):
        return get_top(network, channel, stat, 0, limit)

    if show_ops:
        og = [r for r in _get_opvoice_top("op_given", 3) if r["value"] > 0]
        ot = [r for r in _get_opvoice_top("op_taken", 3) if r["value"] > 0]
        if og:
            _ogv = og[0]["value"]
            text = f"<b>{og[0]['nick']}</b> donated {_ogv} {'op' if _ogv == 1 else 'ops'} in the channel..."
            sub  = f"<b>{og[1]['nick']}</b> was also generous with ops, giving {og[1]['value']} times." if len(og) > 1 else None
            hicell(text, sub)
        else:
            hicell(f"Strange, no op was given on {channel}!")
        if ot:
            _otv = ot[0]["value"]
            text = f"<b>{ot[0]['nick']}</b> is the channel's deop machine, removing ops from <b>{_otv}</b> {'person' if _otv == 1 else 'people'}."
            sub  = f"<b>{ot[1]['nick']}</b> also took ops away {ot[1]['value']} times." if len(ot) > 1 else None
            hicell(text, sub)
        elif og:
            hicell(f"Wow, no op was taken on {channel}!")

    if show_halfops:
        hog = [r for r in _get_opvoice_top("halfop_given", 3) if r["value"] > 0]
        hot = [r for r in _get_opvoice_top("halfop_taken", 3) if r["value"] > 0]
        if hog:
            _hv = hog[0]["value"]
            text = f"<b>{hog[0]['nick']}</b> donated {_hv} {'halfop' if _hv == 1 else 'halfops'} in the channel..."
            sub  = f"<b>{hog[1]['nick']}</b> also gave halfops {hog[1]['value']} times." if len(hog) > 1 else None
            hicell(text, sub)
        else:
            hicell(f"Strange, no halfop was given on {channel}!")
        if hot:
            _htv = hot[0]["value"]
            text = f"<b>{hot[0]['nick']}</b> took halfops away {_htv} {'time' if _htv == 1 else 'times'}."
            hicell(text)
        elif hog:
            hicell(f"Wow, no halfop was taken on {channel}!")

    if show_voice:
        vg = [r for r in _get_opvoice_top("voice_given", 3) if r["value"] > 0]
        vt = [r for r in _get_opvoice_top("voice_taken", 3) if r["value"] > 0]
        if vg:
            _vgv = vg[0]["value"]
            text = f"<b>{vg[0]['nick']}</b> is very generous with voice, handing it out <b>{_vgv}</b> {'time' if _vgv == 1 else 'times'}."
            sub  = f"<b>{vg[1]['nick']}</b> was also quite vocal about giving voice, {vg[1]['value']} times." if len(vg) > 1 else None
            hicell(text, sub)
        else:
            hicell(f"Strange, no voices were given on {channel}!")
        if vt:
            _vtv = vt[0]["value"]
            text = f"<b>{vt[0]['nick']}</b> took voice away <b>{_vtv}</b> {'time' if _vtv == 1 else 'times'} — someone had to."
            sub  = f"<b>{vt[1]['nick']}</b> also silenced people {vt[1]['value']} times." if len(vt) > 1 else None
            hicell(text, sub)
        elif vg:
            hicell(f"No voices were taken on {channel}!")

    # Most actions
    if pisg.get("ShowBigNumbers", True):
        ac = get_top(network, channel, "actions", period, 3)
        ac = [r for r in ac if r["value"] > 0]
        if ac:
            _acv = ac[0]["value"]
            text = f"<b>{ac[0]['nick']}</b> always lets us know what they're doing: {_acv} {'action' if _acv == 1 else 'actions'}!"
            sub  = f"Also, <b>{ac[1]['nick']}</b> tells us what's up with {ac[1]['value']} actions." if len(ac) > 1 else None
            ax   = nick_stats.get(ac[0]["nick"], {}).get("action_ex", None)
            hicell(text, sub, example=ax)
        else:
            _bignum_row("No actions in this channel!")

    # Monologues
    if pisg.get("ShowBigNumbers", True) and qualified:
        mdata = {n: nick_stats[n].get("monologues",0) for n in qualified if nick_stats[n].get("monologues",0) > 0}
        if mdata:
            ranked = sorted(mdata, key=mdata.get, reverse=True)
            _mc  = mdata[ranked[0]]
            text = f"<b>{ranked[0]}</b> talks to themselves a lot. They wrote over 5 lines in a row <b>{_mc}</b> {'time' if _mc == 1 else 'times'}!"
            sub  = f"Another lonely one was <b>{ranked[1]}</b>, who managed to hit {mdata[ranked[1]]} times." if len(ranked) > 1 else None
            _bignum_row(text, sub)

    # Most joins
    if pisg.get("ShowBigNumbers", True):
        jn = get_top(network, channel, "joins", period, 1)
        jn = [r for r in jn if r["value"] > 0]
        if jn:
            _jv = jn[0]["value"]
            _bignum_row(f"<b>{jn[0]['nick']}</b> couldn't decide whether to stay or go. {_jv} {'join' if _jv == 1 else 'joins'} during this period!")

    # Most foul
    if pisg.get("ShowBigNumbers", True) and qualified:
        fdata = {n: nick_stats[n].get("foul", 0) / max(nick_stats[n].get("words", 1), 1)
                 for n in qualified if nick_stats[n].get("foul", 0) > 0}
        if fdata:
            ranked_f = sorted(fdata, key=fdata.get, reverse=True)
            pct1f = f"{fdata[ranked_f[0]]*100:.1f}"
            text  = f"<b>{ranked_f[0]}</b> has quite a potty mouth. {pct1f}% of their words were foul language."
            sub   = f"<b>{ranked_f[1]}</b> also makes sailors blush, {fdata[ranked_f[1]]*100:.1f}% of the time." if len(ranked_f) > 1 else None
            _fex  = nick_stats.get(ranked_f[0], {}).get("foul_ex", None)
            ex    = f"&lt;{ranked_f[0]}&gt; {_fex}" if _fex else None
            hicell(text, sub, example=ex)
        else:
            _bignum_row("Nobody is foul-mouthed here! Remarkable.")

    h('</table>')  # close Other interesting numbers 

    # ── Stats summary ──────────────────────────────────────────────────────────
    if True:
        avg_wpl = f"{total_words/total_lines:.1f}" if total_lines else "0"
        by_str  = f" by {maintainer}" if maintainer else ""
        topic_count = len(recent_topics)
        h(f'''<div class="legend">
  <b>Total lines:</b> {total_lines:,} &nbsp;·&nbsp;
  <b>Unique nicks:</b> {total_users} &nbsp;·&nbsp;
  <b>Avg words/line:</b> {avg_wpl} &nbsp;·&nbsp;
  <b>Avg chars/line:</b> {avg_cpl}<br>
  <b>Topics set:</b> {topic_count} times<br>
  <br>Stats for <b>{channel}</b> on <b>{network}</b>{by_str} &mdash;
  generated {now_str} by <a href="{project_url}" style="color:var(--blue)">Statsbot</a>
  inspired by <a href="http://pisg.sourceforge.net/" style="color:var(--blue)">pisg</a>
</div>''')

    # ── Footer ────────────────────────────────────────────────────────────────
    h(f'<div class="footer"><a href="{project_url}" style="color:var(--muted)">Statsbot</a> — inspired by pisg by Morten Brix Pedersen and others</div>')
    h('<button class="theme-toggle" id="themeToggle" title="Toggle light/dark"></button>')
    h('</div>') # /container

    # ── JavaScript ───────────────────────────────────────────────────────────
    h(f"""<script>
// Hourly activity chart
const hdata = {json.dumps(hourly_data)};
const labels = Array.from({{length:24}}, (_,i) => i.toString().padStart(2,'0')+':00');
const vals = labels.map((_,i) => hdata[i] || 0);
// Band colours matching legend: blue=0-5, green=6-11, yellow=12-17, red=18-23
const bandColor = i => i <= 5 ? '#4a7ab5' : i <= 11 ? '#4a9b5e' : i <= 17 ? '#b5963a' : '#b54a4a';
new Chart(document.getElementById('hourChart'), {{
  type: 'bar',
  data: {{
    labels,
    datasets: [{{
      data: vals,
      backgroundColor: vals.map((_,i) => bandColor(i)),
      borderRadius: 3,
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ grid: {{ color: getComputedStyle(document.body).getPropertyValue('--bg3').trim() }},
           ticks: {{ color: getComputedStyle(document.body).getPropertyValue('--muted').trim(), font: {{ size: 10 }} }} }},
      y: {{ grid: {{ color: getComputedStyle(document.body).getPropertyValue('--bg3').trim() }},
           ticks: {{ color: getComputedStyle(document.body).getPropertyValue('--muted').trim() }},
           beginAtZero: true }}
    }}
  }}
}});

// Live user count
(function() {{
  const net = {json.dumps(network)};
  const chanSlug = {json.dumps(channel.lstrip('#'))};
  function update() {{
    fetch(`/api/${{net}}/${{chanSlug}}/online`)
      .then(r => r.json())
      .then(d => {{
        if (d.online >= 0) {{
          document.getElementById('live-count').textContent = d.online + ' online';
          document.getElementById('users-card').textContent = d.online;
        }}
      }}).catch(() => {{}});
  }}
  update();
  setInterval(update, 30000);
}})();
</script>
""")
    h("""<script>
(function() {
  var btn = document.getElementById('themeToggle');
  function getTheme() { return localStorage.getItem('theme'); }
  function applyTheme(t) {
    var root = document.documentElement;
    if (t === 'light') {
      document.body.classList.add('light');
      root.classList.remove('dark-override');
      btn.textContent = '🌙';
    } else if (t === 'dark') {
      document.body.classList.remove('light');
      root.classList.add('dark-override');
      btn.textContent = '☀️';
    } else {
      document.body.classList.remove('light');
      root.classList.remove('dark-override');
      var preferLight = window.matchMedia('(prefers-color-scheme: light)').matches;
      btn.textContent = preferLight ? '🌙' : '☀️';
    }
  }
  applyTheme(getTheme());
  btn.addEventListener('click', function() {
    var cur = getTheme();
    // If no preference stored, treat current effective theme as the baseline
    if (!cur) { cur = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'; }
    var next = (cur === 'light') ? 'dark' : 'light';
    localStorage.setItem('theme', next);
    applyTheme(next);
  });
})();
</script>
</body>
</html>""")

    return "\n".join(H)
