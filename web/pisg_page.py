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

    # ── HTML construction ─────────────────────────────────────────────────────
    H = []  # HTML buffer

    def h(s): H.append(s)
    def section(title_str):
        h(f'<h2 class="section-title">{title_str}</h2>')
    def hicell(content, small=None, example=None):
        ex_html = f'<br><span class="small example"><b>For example:</b> {example}</span>' if example else ""
        extra = f'<br><span class="small">{small}</span>' if small else ""
        h(f'<tr><td class="hicell">{content}{extra}{ex_html}</td></tr>')
    # ── Page header ───────────────────────────────────────────────────────────
    h(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{channel} @ {network} — {title}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
:root {{
  --bg:       #0d0d1a;
  --bg2:      #1a1a2e;
  --bg3:      #1e2245;
  --border:   #2a3860;
  --text:     #c8d3f5;
  --muted:    #565f89;
  --blue:     #7aa2f7;
  --green:    #9ece6a;
  --yellow:   #e0af68;
  --red:      #f7768e;
  --cyan:     #7dcfff;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: var(--bg); color: var(--text);
        font-family: 'Segoe UI', Tahoma, sans-serif; font-size: 14px; }}

/* Header */
.page-header {{ background: linear-gradient(135deg, #1a1a3e, var(--bg));
                border-bottom: 1px solid var(--border); padding: 1.5rem 2rem; }}
.page-header a {{ color: var(--blue); text-decoration: none; font-size: .85rem; }}
.page-header h1 {{ font-size: 1.8rem; color: var(--blue); margin: .3rem 0 .2rem; }}
.page-header .meta {{ color: var(--muted); font-size: .82rem; }}

/* Layout */
.container {{ max-width: 1200px; margin: 1.5rem auto; padding: 0 1.5rem; }}

/* Section titles */
h2.section-title {{ font-size: .8rem; color: var(--blue); text-transform: uppercase;
                    letter-spacing: 1.5px; padding: .5rem 0 .4rem;
                    border-bottom: 1px solid var(--border); margin: 2rem 0 .8rem; }}

/* Main nick table */
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
             background: linear-gradient(90deg, #3850B8, var(--blue)); }}

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
.byhour-cell {{ color: var(--green); }}
.byhour-cell .cnt {{ color: var(--muted); font-size: .78rem; margin-left: .3rem; }}

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
.tab.active {{ background: #3850B8; color: #fff; border-color: #3850B8; }}
.tab:hover:not(.active) {{ border-color: var(--blue); color: var(--blue); }}

/* Legend */
.legend {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
           padding: 1rem; font-size: .82rem; color: var(--muted); margin-top: 2rem; }}
.legend b {{ color: var(--text); }}

.footer {{ text-align: center; color: var(--muted); font-size: .78rem;
           margin: 2rem 0 1rem; border-top: 1px solid var(--bg3); padding-top: 1rem; }}

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
  <div class="meta">
    {total_users} users tracked · peak {peak_data['peak']}
    {' · ' + _ts_date(peak_data['peak_at']) if peak_data['peak_at'] else ''} · {now_str}
  </div>
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
    section("Most active times")
    h(f'<div class="chart-wrap"><canvas id="hourChart"></canvas></div>')

    # ── Main nick table ───────────────────────────────────────────────────────
    section("Most active nicks")
    show_wpl      = pisg.get("ShowWpl", True)
    show_cpl      = pisg.get("ShowCpl", False)
    show_lastseen = pisg.get("ShowLastSeen", True)
    show_words    = pisg.get("ShowWords", True)
    show_lines    = pisg.get("ShowLines", True)
    show_quote    = pisg.get("ShowRandQuote", True)

    h('<table class="nick-table"><thead><tr>')
    h('<th class="rank">#</th><th>Nick</th>')
    if show_lines: h('<th>Lines</th>')
    if show_words: h('<th>Words</th>')
    if show_wpl:   h('<th>wpl</th>')
    if show_cpl:   h('<th>cpl</th>')
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
        if show_lastseen: h(f'<td class="small">{last}</td>')
        if show_quote: h(f'<td class="quote-cell" title="{q}">{q}</td>')
        h('</tr>')

    h('</tbody></table>')

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
            text = f"Is <b>{n1}</b> stupid or just asking too many questions? {pct1}% of their lines were questions!"
            sub  = f"<b>{ranked[1]}</b> didn't know that much either with {qdata[ranked[1]][0]/qdata[ranked[1]][1]*100:.1f}% questions." if len(ranked) > 1 else None
            _bignum_row(text, sub)

    # Shouting (CAPS)
    if pisg.get("ShowBigNumbers", True) and qualified:
        cdata = {n: (nick_stats[n].get("caps",0), nick_stats[n].get("lines",1))
                 for n in qualified if nick_stats[n].get("caps",0) > 0}
        if cdata:
            ranked = sorted(cdata, key=lambda n: cdata[n][0]/cdata[n][1], reverse=True)
            n1, (c1, l1) = ranked[0], cdata[ranked[0]]
            pct1 = f"{c1/l1*100:.1f}"
            text = f"The loudest one was <b>{n1}</b>, who yelled {pct1}% of the time!"
            sub  = f"Another old yeller was <b>{ranked[1]}</b>, who shouted {cdata[ranked[1]][0]/cdata[ranked[1]][1]*100:.1f}% of the time." if len(ranked) > 1 else None
            ex = nick_stats.get(n1, {}).get("caps_ex", None)
            hicell(text, sub, example=ex)

    # Violent
    if pisg.get("ShowBigNumbers", True) and qualified:
        vdata = {n: nick_stats[n].get("violent",0) for n in qualified if nick_stats[n].get("violent",0) > 0}
        if vdata:
            ranked = sorted(vdata, key=vdata.get, reverse=True)
            text = f"<b>{ranked[0]}</b> is a very aggressive person. They attacked others <b>{vdata[ranked[0]]}</b> times."
            sub  = f"<b>{ranked[1]}</b> can't control their aggressions either, attacking <b>{vdata[ranked[1]]}</b> times." if len(ranked) > 1 else None
            ex = nick_stats.get(ranked[0], {}).get("violent_ex", None)
            hicell(text, sub, example=ex)
            # Attacked victims
            atat = {n: nick_stats[n].get("attacked", 0) for n in qualified if nick_stats[n].get("attacked", 0) > 0}
            if atat:
                av = sorted(atat, key=atat.get, reverse=True)
                ax = nick_stats.get(av[0], {}).get("attacked_ex", None)
                atext = f"Poor <b>{av[0]}</b>, nobody likes them. They were attacked <b>{atat[av[0]]}</b> times."
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
                f"Channel average was {ch_avg:.1f} letters per line."
            )
            if longest != shortest:
                _bignum_row(
                    f"<b>{shortest}</b> wrote the shortest lines, averaging {ldata[shortest]:.1f} characters per line."
                )

    # Words per line (big number)
    if qualified:
        wpl_data = {n: nick_stats[n].get("words", 0) / max(nick_stats[n].get("lines", 1), 1)
                    for n in qualified if nick_stats[n].get("words", 0) > 0}
        if wpl_data:
            best_wpl  = max(wpl_data, key=wpl_data.get)
            ch_avg_wpl = total_words / total_lines if total_lines else 0
            _bignum_row(
                f"<b>{best_wpl}</b> wrote an average of {wpl_data[best_wpl]:.2f} words per line.",
                f"Channel average was {ch_avg_wpl:.2f} words per line."
            )

    # Monologues
    if pisg.get("ShowBigNumbers", True) and qualified:
        mdata = {n: nick_stats[n].get("monologues",0) for n in qualified if nick_stats[n].get("monologues",0) > 0}
        if mdata:
            ranked = sorted(mdata, key=mdata.get, reverse=True)
            text = f"<b>{ranked[0]}</b> talks to themselves a lot. They wrote over 5 lines in a row <b>{mdata[ranked[0]]}</b> times!"
            sub  = f"Another lonely one was <b>{ranked[1]}</b>, who managed to hit {mdata[ranked[1]]} times." if len(ranked) > 1 else None
            _bignum_row(text, sub)

    h('</table>')

    # ── Other numbers ─────────────────────────────────────────────────────────
    section("Other interesting numbers")
    h('<table class="bignums">')

    # Got kicked
    if pisg.get("ShowBigNumbers", True):
        kdata = {nick_stats[n]["nick"] if "nick" in nick_stats.get(n,{}) else n:
                 nick_stats[n].get("kicks",0) for n in nick_stats if nick_stats[n].get("kicks",0) > 0}
        # Actually get from top
        kt = get_top(network, channel, "kicks", period, 3)
        kt = [r for r in kt if r["value"] > 0]
        if kt:
            text = f"<b>{kt[0]['nick']}</b> wasn't very popular, getting kicked {kt[0]['value']} times!"
            sub  = f"<b>{kt[1]['nick']}</b> seemed to be hated too: {kt[1]['value']} kicks." if len(kt) > 1 else None
            _bignum_row(text, sub)

    # Most kicks given
    if pisg.get("ShowBigNumbers", True):
        kg = get_top(network, channel, "kick_given", period, 3)
        kg = [r for r in kg if r["value"] > 0]
        if kg:
            text = f"<b>{kg[0]['nick']}</b> is either insane or just a fair op, kicking {kg[0]['value']} people!"
            sub  = f"{kg[0]['nick']}'s faithful follower, <b>{kg[1]['nick']}</b>, kicked about {kg[1]['value']} people." if len(kg) > 1 else None
            _bignum_row(text, sub)
        else:
            _bignum_row("Nice opers here, no one got kicked!")

    # Most actions
    if pisg.get("ShowBigNumbers", True):
        ac = get_top(network, channel, "actions", period, 3)
        ac = [r for r in ac if r["value"] > 0]
        if ac:
            text = f"<b>{ac[0]['nick']}</b> always lets us know what they're doing: {ac[0]['value']} actions!"
            sub  = f"Also, <b>{ac[1]['nick']}</b> tells us what's up with {ac[1]['value']} actions." if len(ac) > 1 else None
            ax = nick_stats.get(ac[0]["nick"], {}).get("action_ex", None)
            hicell(text, sub, example=ax)
        else:
            _bignum_row("No actions in this channel!")

    # Most foul
    if pisg.get("ShowBigNumbers", False) and qualified:
        fdata = {n: nick_stats[n].get("foul", 0) / max(nick_stats[n].get("words", 1), 1)
                 for n in qualified if nick_stats[n].get("foul", 0) > 0 and nick_stats[n].get("lines", 0) > 15}
        if fdata:
            ranked_f = sorted(fdata, key=fdata.get, reverse=True)
            pct1f = f"{fdata[ranked_f[0]]*100:.1f}"
            text = f"<b>{ranked_f[0]}</b> has quite a potty mouth. {pct1f}% of their words were foul language."
            sub  = f"<b>{ranked_f[1]}</b> also makes sailors blush, {fdata[ranked_f[1]]*100:.1f}% of the time." if len(ranked_f) > 1 else None
            ex   = nick_stats.get(ranked_f[0], {}).get("foul_ex", None)
            hicell(text, sub, example=ex)
        else:
            _bignum_row("Nobody is foul-mouthed here! Remarkable.")

    # Most joins
    if pisg.get("ShowBigNumbers", True):
        jn = get_top(network, channel, "joins", period, 1)
        jn = [r for r in jn if r["value"] > 0]
        if jn:
            _bignum_row(f"<b>{jn[0]['nick']}</b> couldn't decide whether to stay or go. {jn[0]['value']} joins during this period!")

    h('</table>')

    # ── Most active by hour — pisg-style 4-band table ───────────────────────
    if pisg.get("ShowMostActiveByHour", True):
        # Fetch lines per nick per hour, aggregate into 4 bands
        # Band 0: 0-5, Band 1: 6-11, Band 2: 12-17, Band 3: 18-23
        bands = [(0,5,"0-5"), (6,11,"6-11"), (12,17,"12-17"), (18,23,"18-23")]
        nick_band_lines = {}  # {nick: [band0, band1, band2, band3]}
        with get_conn() as conn:
            rows_hr = conn.execute("""
                SELECT n.nick, ha.hour, ha.lines
                FROM hourly_activity ha
                JOIN nicks n ON n.id=ha.nick_id
                WHERE n.network=? AND n.channel=?
            """, (network, channel)).fetchall()
        for r in rows_hr:
            nick = r["nick"]
            hour = r["hour"]
            lines = r["lines"]
            if nick not in nick_band_lines:
                nick_band_lines[nick] = [0, 0, 0, 0]
            for bi, (lo, hi, _) in enumerate(bands):
                if lo <= hour <= hi:
                    nick_band_lines[nick][bi] += lines
                    break

        if nick_band_lines:
            # For each band, rank nicks by lines in that band
            n_rows = pisg.get("ActiveNicks", 25)
            band_ranked = []
            for bi in range(4):
                ranked = sorted(nick_band_lines.keys(),
                                key=lambda n: nick_band_lines[n][bi], reverse=True)
                ranked = [(n, nick_band_lines[n][bi]) for n in ranked if nick_band_lines[n][bi] > 0]
                band_ranked.append(ranked[:n_rows])

            n_bh_rows = pisg.get("ActiveNicksByHour", 10)
            band_ranked = [b[:n_bh_rows] for b in band_ranked]
            max_rows = max(len(b) for b in band_ranked)
            if max_rows > 0:
                section("Most active nicks by hour")
                h('<table class="byhour-table"><thead><tr>')
                h('<th class="rank">#</th>')
                for _, _, label in bands:
                    h(f'<th>{label}</th>')
                h('</tr></thead><tbody>')
                for i in range(max_rows):
                    rank_cls = ' rank-1' if i == 0 else ''
                    h(f'<tr><td class="rank{rank_cls}">{i+1}</td>')
                    for bi in range(4):
                        if i < len(band_ranked[bi]):
                            nick, cnt = band_ranked[bi][i]
                            h(f'<td class="byhour-cell">{nick}<span class="cnt">- {cnt}</span></td>')
                        else:
                            h('<td></td>')
                    h('</tr>')
                h('</tbody></table>')

    # ── Most used words ────────────────────────────────────────────────────────
    if top_words_ch:
        section("Most used words")
        h('<table class="info-table"><thead><tr>')
        h('<th class="rank">#</th><th>Word</th><th>Uses</th><th>Last used by</th>')
        h('</tr></thead><tbody>')
        for i_w, w in enumerate(top_words_ch):
            last = w.get("last_used_by") or ""
            h(f'<tr><td class="rank">{i_w+1}</td>'
              f'<td style="font-family:monospace">{w["word"]}</td>'
              f'<td class="val">{w["count"]}</td>'
              f'<td class="small">{last}</td></tr>')
        h('</tbody></table>')

        section("Most referenced nicks")
        h('<table class="info-table"><thead><tr><th class="rank">#</th><th>Nick</th><th>Times mentioned</th><th>Last by</th></tr></thead><tbody>')
        for i, r in enumerate(nick_refs):
            h(f'<tr><td class="rank">{i+1}</td><td class="nick-name">{r["mentioned"]}</td>'
              f'<td class="val">{r["count"]}</td><td class="small">{r.get("by_nick","")}</td></tr>')
        h('</tbody></table>')

    # ── Karma leaderboard ────────────────────────────────────────────────────
    if pisg.get("ShowKarma", True) and (karma_top or karma_bottom):
        section("Karma")
        h('<table class="info-table"><thead><tr>')
        h('<th class="rank">#</th><th>Nick</th><th>Score</th></tr></thead><tbody>')
        # Top positive karma
        for i, r in enumerate(karma_top):
            score = r["score"]
            colour = "var(--green)" if score > 0 else "var(--red)"
            sign   = "+" if score > 0 else ""
            h(f'<tr><td class="rank">{i+1}</td>'
              f'<td class="nick-name">{r["nick"]}</td>'
              f'<td class="val" style="color:{colour}">{sign}{score}</td></tr>')
        # Bottom (only if not already in top)
        top_nicks = {r["nick"].lower() for r in karma_top}
        extras = [r for r in karma_bottom if r["nick"].lower() not in top_nicks]
        for r in extras:
            score = r["score"]
            h(f'<tr><td class="rank">—</td>'
              f'<td class="nick-name">{r["nick"]}</td>'
              f'<td class="val" style="color:var(--red)">{score}</td></tr>')
        h('</tbody></table>')

    # ── Smiley frequency table ────────────────────────────────────────────────
    if pisg.get("ShowSmileys", True) and top_smileys:
        section("Smileys :-)")
        h('<table class="info-table"><thead><tr><th class="rank">#</th><th>Smiley</th><th>Uses</th><th>Top user</th></tr></thead><tbody>')
        for i, r in enumerate(top_smileys):
            h(f'<tr><td class="rank">{i+1}</td><td style="font-size:1.1rem">{r["smiley"]}</td>'
              f'<td class="val">{r["total"]}</td><td class="small">{r.get("top_user","")}</td></tr>')
        h('</tbody></table>')

    # ── Latest topics ─────────────────────────────────────────────────────────
    if recent_topics:
        section("Latest topics")
        h('<div>')
        for t in recent_topics:
            h(f'<div class="topic-row">'
              f'<div class="topic-text">"{t["topic"]}"</div>'
              f'<div class="topic-meta">set by {t["set_by"]} · {_ago(t["ts"])}</div>'
              f'</div>')
        h('</div>')

    # ── Recent URLs ───────────────────────────────────────────────────────────
    if pisg.get("ShowMru", True) and recent_urls:
        section("Most referenced URLs")
        h('<table class="info-table"><thead><tr><th>URL</th><th>Uses</th><th>Last by</th><th>When</th></tr></thead><tbody>')
        for u in recent_urls:
            url = u["url"]
            disp = url[:70] + "…" if len(url) > 70 else url
            h(f'<tr><td><a href="{url}" target="_blank" rel="noopener" style="color:var(--blue)">{disp}</a></td>'
              f'<td class="val">{u.get("count", 1)}</td>'
              f'<td class="small">{u.get("nick","")}</td><td class="small">{_ago(u["ts"])}</td></tr>')
        h('</tbody></table>')

    # ── Recent kicks ──────────────────────────────────────────────────────────
    if recent_kicks:
        h('<table class="info-table"><thead><tr><th>Victim</th><th>Kicked by</th><th>Reason</th><th>When</th></tr></thead><tbody>')
        for k in recent_kicks:
            h(f'<tr><td style="color:var(--red)">{k["victim"]}</td>'
              f'<td style="color:var(--green)">{k["kicker"]}</td>'
              f'<td class="small">{(k["reason"] or "")[:50]}</td>'
              f'<td class="small">{_ago(k["ts"])}</td></tr>')
        h('</tbody></table>')

    # ── Ops / Voice / Halfops ─────────────────────────────────────────────────
    show_ops     = pisg.get("ShowOps",     True)
    show_voice   = pisg.get("ShowVoice",   True)
    show_halfops = pisg.get("ShowHalfops", True)

    def _get_opvoice_top(stat, limit=3):
        return get_top(network, channel, stat, 0, limit)

    if show_ops or show_voice or show_halfops:
        _has_ops     = show_ops     and any(r["value"] > 0 for r in _get_opvoice_top("op_given"))
        _has_voice   = show_voice   and any(r["value"] > 0 for r in _get_opvoice_top("voice_given"))
        _has_halfops = show_halfops and any(r["value"] > 0 for r in _get_opvoice_top("halfop_given"))

        if _has_ops or _has_voice or _has_halfops:
            section("Ops, voice and halfops")
            h('<table class="bignums">')
            # ── Ops ───────────────────────────────────────────────────────
            if _has_ops:
                og = [r for r in _get_opvoice_top("op_given",  3) if r["value"] > 0]
                ot = [r for r in _get_opvoice_top("op_taken",  3) if r["value"] > 0]
                gr = [r for r in _get_opvoice_top("op_got",    3) if r["value"] > 0]
                dr = [r for r in _get_opvoice_top("deop_got",  3) if r["value"] > 0]

                if og:
                    text = (f"<b>{og[0]['nick']}</b> is either insane or just a fair op, "
                            f"giving ops to <b>{og[0]['value']}</b> people!")
                    sub  = (f"<b>{og[1]['nick']}</b> is also quite op-happy, "
                            f"handing out ops <b>{og[1]['value']}</b> times."
                            if len(og) > 1 else None)
                    hicell(text, sub)

                if ot:
                    text = (f"<b>{ot[0]['nick']}</b> is the channel's deop machine, "
                            f"removing ops from <b>{ot[0]['value']}</b> people.")
                    sub  = (f"<b>{ot[1]['nick']}</b> also took ops away "
                            f"<b>{ot[1]['value']}</b> times."
                            if len(ot) > 1 else None)
                    hicell(text, sub)

                if gr:
                    text = (f"<b>{gr[0]['nick']}</b> is a popular one — "
                            f"they were given ops <b>{gr[0]['value']}</b> times.")
                    sub  = (f"<b>{gr[1]['nick']}</b> was also trusted with ops "
                            f"<b>{gr[1]['value']}</b> times."
                            if len(gr) > 1 else None)
                    hicell(text, sub)

                if dr:
                    text = (f"Poor <b>{dr[0]['nick']}</b> — they got deopped "
                            f"<b>{dr[0]['value']}</b> times!")
                    sub  = (f"<b>{dr[1]['nick']}</b> also suffered "
                            f"<b>{dr[1]['value']}</b> deops."
                            if len(dr) > 1 else None)
                    hicell(text, sub)

            # ── Halfops ───────────────────────────────────────────────────
            if _has_halfops:
                hog = [r for r in _get_opvoice_top("halfop_given",   3) if r["value"] > 0]
                hot = [r for r in _get_opvoice_top("halfop_taken",   3) if r["value"] > 0]
                hgr = [r for r in _get_opvoice_top("halfop_got",     3) if r["value"] > 0]
                hdr = [r for r in _get_opvoice_top("dehalfop_got",   3) if r["value"] > 0]

                if hog:
                    text = (f"<b>{hog[0]['nick']}</b> dishes out halfops generously — "
                            f"<b>{hog[0]['value']}</b> times so far.")
                    sub  = (f"<b>{hog[1]['nick']}</b> also gave halfops "
                            f"<b>{hog[1]['value']}</b> times."
                            if len(hog) > 1 else None)
                    hicell(text, sub)

                if hot:
                    text = (f"<b>{hot[0]['nick']}</b> took halfops away "
                            f"<b>{hot[0]['value']}</b> times.")
                    hicell(text)

                if hgr:
                    text = (f"<b>{hgr[0]['nick']}</b> received halfops "
                            f"<b>{hgr[0]['value']}</b> times.")
                    hicell(text)

                if hdr:
                    text = (f"<b>{hdr[0]['nick']}</b> had their halfops removed "
                            f"<b>{hdr[0]['value']}</b> times.")
                    hicell(text)

            # ── Voice ─────────────────────────────────────────────────────
            if _has_voice:
                vg = [r for r in _get_opvoice_top("voice_given",  3) if r["value"] > 0]
                vt = [r for r in _get_opvoice_top("voice_taken",  3) if r["value"] > 0]
                vr = [r for r in _get_opvoice_top("voice_got",    3) if r["value"] > 0]
                dv = [r for r in _get_opvoice_top("devoice_got",  3) if r["value"] > 0]

                if vg:
                    text = (f"<b>{vg[0]['nick']}</b> is very generous with voice, "
                            f"handing it out <b>{vg[0]['value']}</b> times.")
                    sub  = (f"<b>{vg[1]['nick']}</b> was also quite vocal about giving voice, "
                            f"<b>{vg[1]['value']}</b> times."
                            if len(vg) > 1 else None)
                    hicell(text, sub)

                if vt:
                    text = (f"<b>{vt[0]['nick']}</b> took voice away "
                            f"<b>{vt[0]['value']}</b> times — someone had to.")
                    sub  = (f"<b>{vt[1]['nick']}</b> also silenced people "
                            f"<b>{vt[1]['value']}</b> times."
                            if len(vt) > 1 else None)
                    hicell(text, sub)

                if vr:
                    text = (f"<b>{vr[0]['nick']}</b> was voiced "
                            f"<b>{vr[0]['value']}</b> times — they must have something to say.")
                    sub  = (f"<b>{vr[1]['nick']}</b> also got voice "
                            f"<b>{vr[1]['value']}</b> times."
                            if len(vr) > 1 else None)
                    hicell(text, sub)

                if dv:
                    text = (f"<b>{dv[0]['nick']}</b> got devoiced "
                            f"<b>{dv[0]['value']}</b> times. Ouch.")
                    sub  = (f"<b>{dv[1]['nick']}</b> also lost voice "
                            f"<b>{dv[1]['value']}</b> times."
                            if len(dv) > 1 else None)
                    hicell(text, sub)

            h('</table>')

    # ── Legend ────────────────────────────────────────────────────────────────
    if pisg.get("ShowLegend", True):
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
    h('</div>') # /container

    # ── JavaScript ───────────────────────────────────────────────────────────
    h(f"""<script>
// Hourly activity chart
const hdata = {json.dumps(hourly_data)};
const labels = Array.from({{length:24}}, (_,i) => i.toString().padStart(2,'0')+':00');
const vals = labels.map((_,i) => hdata[i] || 0);
const mx = Math.max(...vals, 1);
new Chart(document.getElementById('hourChart'), {{
  type: 'bar',
  data: {{
    labels,
    datasets: [{{
      data: vals,
      backgroundColor: vals.map(v => `rgba(122,162,247,${{(0.3+v/mx*0.7).toFixed(2)}})` ),
      borderRadius: 3,
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ grid: {{ color: '#1a1a3e' }}, ticks: {{ color: '#565f89', font: {{ size: 10 }} }} }},
      y: {{ grid: {{ color: '#1a1a3e' }}, ticks: {{ color: '#565f89' }}, beginAtZero: true }}
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
</body>
</html>""")

    return "\n".join(H)
