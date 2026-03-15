"""
web/dashboard.py
Flask-based live stats dashboard — mirrors livestats.c / webfiles.c
Serves a pisg-style HTML stats page at http://host:8033/
"""

import time
import json
import logging
from datetime import datetime
from typing import Optional

from flask import Flask, render_template_string, jsonify, abort, request, redirect, url_for

log = logging.getLogger("dashboard")

app = Flask(__name__)
_config = {}
_db_path = "data/stats.db"
# Live channel member counts — updated by connectors via register_connector()
_connectors: list = []


def register_connector(connector) -> None:
    """Called from main.py after connectors are created."""
    _connectors.append(connector)


def get_online_count(network: str, channel: str) -> int:
    """Return live member count from connector state, or -1 if unavailable."""
    for conn in _connectors:
        if conn.network == network:
            members = conn._channel_members.get(channel, set())
            return len(members)
    return -1


@app.template_filter("datefmt")
def _datefmt(ts):
    if not ts:
        return ""
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y")


def set_config(config: dict, db_path: str):
    global _config, _db_path
    _config = config
    _db_path = db_path
    from database.models import set_db_path
    set_db_path(db_path)


def _ts(ts: int) -> str:
    if not ts:
        return "never"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "?"


def _ago(ts: int) -> str:
    if not ts:
        return ""
    diff = int(time.time()) - ts
    if diff < 60:
        return f"{diff}s ago"
    if diff < 3600:
        return f"{diff//60}m ago"
    if diff < 86400:
        return f"{diff//3600}h ago"
    return f"{diff//86400}d ago"


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    from database.models import get_channels, count_users, get_conn
    web_cfg = _config.get("web", {})

    all_channels = get_channels()

    # Build per-network summary
    networks = {}
    for ch in all_channels:
        net = ch["network"]
        chan = ch["channel"]
        if net not in networks:
            # Look up server host from config
            host = next(
                (n["host"] for n in _config.get("networks", []) if n["name"] == net),
                net
            )
            networks[net] = {"name": net, "host": host, "channels": [], "users": 0}
        user_count = count_users(net, chan)
        networks[net]["channels"].append({"name": chan, "users": user_count})
        networks[net]["users"] += user_count

    # Global totals
    with get_conn() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM nicks").fetchone()[0]
        total_lines = conn.execute("SELECT COALESCE(SUM(lines),0) FROM stats WHERE period=0").fetchone()[0]

    network_list = sorted(networks.values(), key=lambda n: n["name"])
    total_channels = sum(len(n["channels"]) for n in network_list)

    return render_template_string(INDEX_TMPL,
        network_list=network_list,
        total_users=total_users,
        total_lines=total_lines,
        total_channels=total_channels,
        title=web_cfg.get("title", "IRC Stats"),
        project_url=web_cfg.get("project_url", "https://github.com/TehPeGaSuS/Statsbot"),
        now=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )



@app.route("/<network>/")
@app.route("/<network>")
def network_stats(network: str):
    from database.models import get_channels, count_users, get_conn
    web_cfg = _config.get("web", {})

    all_channels = get_channels(network)
    if not all_channels:
        abort(404)

    host = next(
        (n["host"] for n in _config.get("networks", []) if n["name"] == network),
        network
    )

    channels = []
    for ch in all_channels:
        chan = ch["channel"]
        users = count_users(network, chan)
        with get_conn() as conn:
            row = conn.execute(
                """SELECT COALESCE(SUM(s.words),0) as words,
                          COALESCE(SUM(s.lines),0) as lines
                   FROM nicks n JOIN stats s ON s.nick_id=n.id
                   WHERE n.network=? AND n.channel=? AND s.period=0 AND s.words>0""",
                (network, chan)
            ).fetchone()
        channels.append({
            "name": chan,
            "users": users,
            "words": row["words"] if row else 0,
            "lines": row["lines"] if row else 0,
        })

    channels.sort(key=lambda c: c["words"], reverse=True)

    return render_template_string(NETWORK_TMPL,
        network=network,
        host=host,
        channels=channels,
        title=web_cfg.get("title", "IRC Stats"),
        project_url=web_cfg.get("project_url", "https://github.com/TehPeGaSuS/Statsbot"),
        now=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

@app.route("/<network>/<path:channel>/")
@app.route("/<network>/<path:channel>")
def channel_stats(network: str, channel: str):
    channel = channel.rstrip("/")
    if not channel.startswith("#"):
        channel = "#" + channel
    period = int(request.args.get("period", 0))
    from web.pisg_page import build_page
    html = build_page(network, channel, period, _config)
    return html


@app.route("/api/<network>/<path:channel>/online")
def api_online(network: str, channel: str):
    if not channel.startswith("#"):
        channel = "#" + channel
    channel = channel.rstrip("/")
    count = get_online_count(network, channel)
    return jsonify({"online": count, "network": network, "channel": channel})


@app.route("/api/<network>/<path:channel>/top")
def api_top(network: str, channel: str):
    if not channel.startswith("#"):
        channel = "#" + channel
    from database.models import get_top
    stat = request.args.get("stat", "lines")
    period = int(request.args.get("period", 0))
    limit = int(request.args.get("limit", 10))
    try:
        rows = get_top(network, channel, stat, period, limit)
        return jsonify(rows)
    except ValueError as e:
        abort(400, str(e))


@app.route("/api/<network>/<path:channel>/nick/<nick>")
def api_nick(network: str, channel: str, nick: str):
    if not channel.startswith("#"):
        channel = "#" + channel
    from database.models import get_nick_all_stats, get_hourly_activity, get_conn
    period = int(request.args.get("period", 0))
    s = get_nick_all_stats(nick, network, channel, period)
    if not s:
        abort(404)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM nicks WHERE nick=? AND network=? AND channel=?",
            (nick, network, channel)
        ).fetchone()
    if row:
        hourly = get_hourly_activity(row["id"])
        s["hourly"] = hourly
    return jsonify(dict(s))


# ─── HTML Templates ───────────────────────────────────────────────────────────

NETWORK_TMPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ network }} — {{ title }}</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0d0d1a; color: #c8d3f5; font-family: 'Segoe UI', Tahoma, monospace; }
.header { background: linear-gradient(135deg, #1a1a3e 0%, #0d0d1a 100%);
          border-bottom: 1px solid #2a3860; padding: 1.5rem 2rem; }
.header a { color: #7aa2f7; text-decoration: none; font-size: .85rem; }
.header h1 { font-size: 1.8rem; color: #7aa2f7; margin-top: .3rem; }
.header .meta { color: #565f89; font-size: .82rem; margin-top: .25rem; font-family: monospace; }
.container { max-width: 1100px; margin: 2rem auto; padding: 0 1.5rem; }
.chan-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 1rem; }
.chan-card { background: #1a1a2e; border: 1px solid #1e2a45; border-radius: 10px;
             padding: 1.2rem 1.4rem; transition: border-color .2s, box-shadow .2s;
             display: block; text-decoration: none; color: inherit; cursor: pointer; }
.chan-card:hover { border-color: #7aa2f7; box-shadow: 0 0 0 1px #7aa2f7; }
.chan-card .cn { font-size: 1.1rem; font-weight: bold; margin-bottom: .8rem; color: #7aa2f7; }
.stat-row { display: flex; justify-content: space-between; padding: .22rem 0;
             border-bottom: 1px solid #1a1a3e; font-size: .83rem; }
.stat-row:last-child { border-bottom: none; }
.stat-row .sk { color: #565f89; }
.stat-row .sv { color: #e0af68; font-weight: bold; }
.footer { text-align: center; color: #3d4a6b; font-size: .78rem;
          margin: 3rem 0 1.5rem; border-top: 1px solid #1a1a3e; padding-top: 1rem; }

@media (max-width: 600px) {
  .container { padding: 0 .7rem; }
  .header { padding: 1rem; }
  .chan-grid { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<div class="header">
  <a href="/">← All networks</a>
  <h1>{{ network }}</h1>
  <div class="meta">{{ host }}</div>
</div>
<div class="container">
  {% if channels %}
  <div class="chan-grid">
    {% for ch in channels %}
    <a class="chan-card" href="/{{ network }}/{{ ch.name[1:] }}/">
      <div class="cn">{{ ch.name }}</div>
      <div class="stat-row"><span class="sk">Tracked users</span><span class="sv">{{ ch.users }}</span></div>
      <div class="stat-row"><span class="sk">Total words</span><span class="sv">{{ "{:,}".format(ch.words) }}</span></div>
      <div class="stat-row"><span class="sk">Total lines</span><span class="sv">{{ "{:,}".format(ch.lines) }}</span></div>
    </a>
    {% endfor %}
  </div>
  {% else %}
  <p style="color:#565f89;text-align:center;padding:3rem 0">No channels tracked on this network yet.</p>
  {% endif %}
</div>
<div class="footer"><a href="{{ project_url }}" style="color:#3d4a6b">Statsbot</a> &mdash; inspired by stats.mod by G'Quann / Florian Sander</div>
</body>
</html>"""


INDEX_TMPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0d0d1a; color: #c8d3f5; font-family: 'Segoe UI', Tahoma, monospace; }

.header { background: linear-gradient(135deg, #1a1a3e 0%, #0d0d1a 100%);
          border-bottom: 1px solid #2a3860; padding: 2.5rem 2rem; text-align: center; }
.header h1 { font-size: 2.2rem; color: #7aa2f7; letter-spacing: 2px; margin-bottom: .4rem; }
.header .sub { color: #565f89; font-size: .85rem; }

.container { max-width: 960px; margin: 2.5rem auto; padding: 0 1.5rem; }

/* Global stats bar */
.globals { display: flex; gap: 1rem; margin-bottom: 2.5rem; flex-wrap: wrap; }
.glob-card { flex: 1; min-width: 140px; background: #1a1a2e;
             border: 1px solid #1e2a45; border-radius: 10px;
             padding: .9rem 1.4rem; text-align: center; }
.glob-card .gv { font-size: 1.8rem; font-weight: bold; color: #7aa2f7; }
.glob-card .gl { font-size: .75rem; color: #565f89; margin-top: .25rem;
                  text-transform: uppercase; letter-spacing: .5px; }

/* Section label */
.section-label { font-size: .75rem; color: #565f89; text-transform: uppercase;
                  letter-spacing: 1px; margin-bottom: 1rem; }

/* Network cards */
.net-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1.2rem; }
.net-card { background: #1a1a2e; border: 1px solid #1e2a45; border-radius: 10px;
            padding: 1.4rem; display: block; text-decoration: none; color: inherit;
            cursor: pointer; transition: border-color .2s, box-shadow .2s; }
.net-card:hover { border-color: #9ece6a; box-shadow: 0 0 0 1px #9ece6a; }
.net-card .net-name { font-size: 1.15rem; font-weight: bold; color: #9ece6a;
                       margin-bottom: .2rem; }
.net-card .net-host { font-size: .8rem; color: #565f89; margin-bottom: 1rem;
                       font-family: monospace; }
.net-meta { display: flex; gap: 1.2rem; }
.net-meta .nm { text-align: center; }
.net-meta .nmv { font-size: 1.2rem; font-weight: bold; color: #e0af68; }
.net-meta .nml { font-size: .72rem; color: #565f89; text-transform: uppercase; letter-spacing: .4px; }

.empty { color: #565f89; text-align: center; padding: 3rem 0; font-size: .9rem; }

.footer { text-align: center; color: #3d4a6b; font-size: .78rem;
          margin: 3rem 0 1.5rem; border-top: 1px solid #1a1a3e; padding-top: 1rem; }

@media (max-width: 600px) {
  .container { padding: 0 .7rem; }
  .header { padding: 1.2rem 1rem; }
  .globals { gap: .5rem; }
  .glob-card { min-width: 100px; padding: .6rem .8rem; }
  .glob-card .gv { font-size: 1.3rem; }
  .net-grid { grid-template-columns: 1fr; }
}
</style>
</head>
<body>

<div class="header">
  <h1>📊 {{ title }}</h1>
  <div class="sub">updated {{ now }}</div>
</div>

<div class="container">

  <!-- Global totals -->
  <div class="globals">
    <div class="glob-card">
      <div class="gv">{{ total_users }}</div>
      <div class="gl">users tracked</div>
    </div>
    <div class="glob-card">
      <div class="gv">{{ network_list | length }}</div>
      <div class="gl">networks</div>
    </div>
    <div class="glob-card">
      <div class="gv">{{ total_channels }}</div>
      <div class="gl">channels</div>
    </div>
    <div class="glob-card">
      <div class="gv">{{ "{:,}".format(total_lines) }}</div>
      <div class="gl">lines logged</div>
    </div>
  </div>

  {% if network_list %}
  <div class="section-label">Networks</div>
  <div class="net-grid">
    {% for net in network_list %}
    <a class="net-card" href="/{{ net.name }}/">
      <div class="net-name">{{ net.name }} →</div>
      <div class="net-host">{{ net.host }}</div>
      <div class="net-meta">
        <div class="nm">
          <div class="nmv">{{ net.users }}</div>
          <div class="nml">users</div>
        </div>
        <div class="nm">
          <div class="nmv">{{ net.channels | length }}</div>
          <div class="nml">channels</div>
        </div>
      </div>
    </a>
    {% endfor %}
  </div>
  {% else %}
  <div class="empty">No channels tracked yet — connect the bot to a channel to start.</div>
  {% endif %}

</div>

<div class="footer"><a href="{{ project_url }}" style="color:#3d4a6b">Statsbot</a> &mdash; inspired by stats.mod by G'Quann / Florian Sander</div>
</body>
</html>"""


CHANNEL_TMPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ channel }} — {{ title }}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0d0d1a; color: #c8d3f5;
       font-family: 'Segoe UI', Tahoma, monospace; font-size: 14px; }

.header { background: linear-gradient(135deg, #1a1a3e 0%, #0d0d1a 100%);
          border-bottom: 1px solid #2a3860; padding: 1.5rem 2rem; }
.header h1 { font-size: 1.8rem; color: #7aa2f7; }
.header .meta { color: #565f89; font-size: .85rem; margin-top: .3rem; }
.header a { color: #7aa2f7; text-decoration: none; font-size: .85rem; }

.container { max-width: 1200px; margin: 1.5rem auto; padding: 0 1rem; }

/* Period tabs */
.tabs { display: flex; gap: .5rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
.tab { padding: .4rem 1rem; border-radius: 20px; border: 1px solid #2a3860;
       color: #565f89; text-decoration: none; font-size: .85rem; transition: all .2s; }
.tab.active { background: #3850B8; color: #fff; border-color: #3850B8; }
.tab:hover:not(.active) { border-color: #7aa2f7; color: #7aa2f7; }

/* Section headers */
.section { margin-bottom: 2rem; }
.section-title { font-size: 1rem; color: #7aa2f7; letter-spacing: 1px;
                  text-transform: uppercase; margin-bottom: .8rem;
                  padding-bottom: .4rem; border-bottom: 1px solid #1e2a45; }

/* Stats table */
.tscroll { overflow-x: auto; -webkit-overflow-scrolling: touch; margin-bottom: 1rem; }
.tscroll > table { margin-bottom: 0; min-width: 480px; }
.stats-table { width: 100%; border-collapse: collapse; }
.stats-table th { background: #1a1a3e; color: #7aa2f7; text-align: left;
                   padding: .5rem .7rem; font-size: .8rem; letter-spacing: .5px;
                   text-transform: uppercase; }
.stats-table td { padding: .4rem .7rem; border-bottom: 1px solid #1a1a3e; }
.stats-table tr:hover td { background: #1a1a3e; }
.stats-table .rank { color: #565f89; width: 40px; }
.stats-table .nick { color: #9ece6a; font-weight: bold; }
.stats-table .bar-cell { width: 200px; }
.bar-wrap { background: #1a1a3e; border-radius: 3px; height: 12px; overflow: hidden; }
.bar-fill { height: 100%; border-radius: 3px;
             background: linear-gradient(90deg, #3850B8, #7aa2f7); }
.val { color: #e0af68; font-weight: bold; }

/* Two-column grid */
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }
@media (max-width: 700px) { .grid-2 { grid-template-columns: 1fr; } }

/* Card */
.card { background: #1a1a2e; border: 1px solid #1e2a45; border-radius: 8px;
         padding: 1rem; }

/* Heatmap chart */
.chart-wrap { position: relative; height: 160px; }

/* Word cloud */
.word-cloud { display: flex; flex-wrap: wrap; gap: .4rem; padding: .5rem 0; }
.word-tag { background: #1a1a3e; border: 1px solid #2a3860; border-radius: 4px;
             padding: .2rem .5rem; font-size: .8rem; color: #7aa2f7; }
.word-tag .wc { color: #565f89; font-size: .75rem; margin-left: .3rem; }

/* Misc items */
.misc-item { padding: .4rem 0; border-bottom: 1px solid #1a1a3e; font-size: .85rem; }
.misc-item .by { color: #565f89; margin-left: .5rem; font-size: .8rem; }
.misc-item .when { color: #3d5afe; font-size: .75rem; margin-left: .5rem; }

/* Nick table (user list) */
.nick-alpha a { color: #7aa2f7; text-decoration: none; margin: 0 .2rem; }

/* Summary bar */
.summary { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
.summary-item { background: #1a1a2e; border: 1px solid #1e2a45; border-radius: 8px;
                 padding: .6rem 1.2rem; flex: 1; min-width: 120px; text-align: center; }
.summary-item .sv { font-size: 1.4rem; color: #7aa2f7; font-weight: bold; line-height: 1.2; }
.summary-item .sl { font-size: .72rem; color: #565f89; margin-top: .3rem; text-transform: uppercase; letter-spacing: .4px; }

.footer { text-align: center; color: #565f89; font-size: .8rem;
           margin: 3rem 0 1rem; border-top: 1px solid #1a1a3e; padding-top: 1rem; }
</style>
</head>
<body>

<div class="header">
  <a href="/">← All channels</a>
  <h1>{{ channel }}  <span style="color:#565f89;font-size:1rem">on {{ network }}</span></h1>
  <div class="meta">{{ total_users }} tracked{% if active_users < total_users %} · {{ active_users }} active{% endif %} · peak {{ peak }} · generated {{ now }}</div>
</div>

<div class="container">

  <!-- Period tabs -->
  <div class="tabs">
    {% for pn in period_names %}
    <a class="tab {% if period == loop.index0 %}active{% endif %}"
       href="?period={{ loop.index0 }}">{{ pn }}</a>
    {% endfor %}
  </div>

  <!-- Summary cards: value on top, label below -->
  <div class="summary">
    <div class="summary-item">
      <div class="sv">{{ total_users }}</div>
      <div class="sl">users</div>
    </div>
    <div class="summary-item">
      <div class="sv">{{ peak }}</div>
      <div class="sl">peak users{% if peak_at %} · {{ peak_at | datefmt }}{% endif %}</div>
    </div>
    {% set tw = tops.get('words', []) | selectattr('value', '>', 0) | list %}
    <div class="summary-item">
      <div class="sv">{{ tw[0]['nick'] if tw else '—' }}</div>
      <div class="sl">top talker</div>
    </div>
    {% set ts_ = tops.get('smileys', []) | selectattr('value', '>', 0) | list %}
    <div class="summary-item">
      <div class="sv">{{ ts_[0]['nick'] if ts_ else '—' }}</div>
      <div class="sl">happiest :)</div>
    </div>
    {% set tsd = tops.get('sad', []) | selectattr('value', '>', 0) | list %}
    <div class="summary-item">
      <div class="sv">{{ tsd[0]['nick'] if tsd else '—' }}</div>
      <div class="sl">saddest :(</div>
    </div>
    <div class="summary-item">
      <div class="sv">{{ top_minutes[0]['nick'] if top_minutes else '—' }}</div>
      <div class="sl">most online</div>
    </div>
  </div>

  <!-- Activity heatmap -->
  <div class="section">
    <div class="section-title">Activity by Hour</div>
    <div class="card">
      <div class="chart-wrap">
        <canvas id="heatChart"></canvas>
      </div>
    </div>
  </div>

  <!-- Top tables -->
  {% for stat in topstats %}
  {% set rows = tops.get(stat, []) %}
  {% if rows %}
  <div class="section">
    <div class="section-title">Top {{ stat.replace('_',' ') }} ({{ period_name }})</div>
    <div class="tscroll"><table class="stats-table">
      <thead><tr>
        <th class="rank">#</th>
        <th>Nick</th>
        <th>Value</th>
        <th class="bar-cell">Chart</th>
      </tr></thead>
      <tbody>
      {% set max_val = rows[0]['value'] if rows else 1 %}
      {% for row in rows %}
      <tr>
        <td class="rank">{{ loop.index }}</td>
        <td class="nick">{{ row['nick'] }}</td>
        <td class="val">
          {% if stat == 'wpl' %}{{ "%.2f"|format(row['value']) }}
          {% else %}{{ row['value'] }}{% endif %}
        </td>
        <td class="bar-cell">
          {% set pct = ((row['value'] / max_val * 100) if max_val else 0)|int %}
          {% set pct = pct if pct <= 100 else 100 %}
          <div class="bar-wrap">
            <div class="bar-fill" style="width:{{ pct }}%"></div>
          </div>
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
  {% endfor %}

  <!-- Word cloud + Topics + URLs + Kicks -->
  <div class="grid-2">

    {% if top_words %}
    <div class="section">
      <div class="section-title">Top Words</div>
      <div class="card">
        <div class="word-cloud">
          {% for w in top_words %}
          <span class="word-tag">{{ w.word }}<span class="wc">{{ w.count }}</span></span>
          {% endfor %}
        </div>
      </div>
    </div>
    {% endif %}

    {% if recent_topics %}
    <div class="section">
      <div class="section-title">Recent Topics</div>
      <div class="card">
        {% for t in recent_topics %}
        <div class="misc-item">
          {{ t.topic[:80] }}{% if t.topic|length > 80 %}…{% endif %}
          <span class="by">— {{ t.set_by }}</span>
          <span class="when">{{ ago(t.ts) }}</span>
        </div>
        {% endfor %}
      </div>
    </div>
    {% endif %}

    {% if recent_urls %}
    <div class="section">
      <div class="section-title">Recent URLs</div>
      <div class="card">
        {% for u in recent_urls %}
        <div class="misc-item">
          <a href="{{ u.url }}" target="_blank" rel="noopener"
             style="color:#7aa2f7;text-decoration:none">
             {{ u.url[:60] }}{% if u.url|length > 60 %}…{% endif %}
          </a>
          <span class="by">— {{ u.nick or '?' }}</span>
          <span class="when">{{ ago(u.ts) }}</span>
        </div>
        {% endfor %}
      </div>
    </div>
    {% endif %}

    {% if recent_kicks %}
    <div class="section">
      <div class="section-title">Recent Kicks</div>
      <div class="card">
        {% for k in recent_kicks %}
        <div class="misc-item">
          <span style="color:#f7768e">{{ k.victim }}</span>
          kicked by <span style="color:#9ece6a">{{ k.kicker }}</span>
          <span class="by">({{ k.reason[:40] if k.reason else '' }})</span>
          <span class="when">{{ ago(k.ts) }}</span>
        </div>
        {% endfor %}
      </div>
    </div>
    {% endif %}

  </div>

  <!-- User list -->
  {% if nick_list %}
  <div class="section">
    <div class="section-title">All Users ({{ nick_list|length }})</div>
    <div class="card">
      <div class="word-cloud">
        {% for n in nick_list %}
        <span class="word-tag" title="{{ n.lines }} lines{% if n.is_bot %} · +B bot umode{% endif %}">{{ n.nick }}{% if n.is_bot %}<span class="wc">+B</span>{% endif %}</span>
        {% endfor %}
      </div>
    </div>
  </div>
  {% endif %}

</div><!-- /container -->

<div class="footer">
  <a href="{{ project_url }}" style="color:#565f89">Statsbot</a> — inspired by stats.mod by G'Quann / Florian Sander &nbsp;·&nbsp; {{ now }}
</div>

<script>
// Hourly activity bar chart
const hourlyRaw = {{ hourly_data }};
const labels = Array.from({length:24}, (_,i) => i.toString().padStart(2,'0')+':00');
const data = labels.map((_,i) => hourlyRaw[i] || 0);

const ctx = document.getElementById('heatChart').getContext('2d');
new Chart(ctx, {
  type: 'bar',
  data: {
    labels,
    datasets: [{
      label: 'lines',
      data,
      backgroundColor: data.map((v, i) => {
        const max = Math.max(...data, 1);
        const alpha = 0.3 + (v/max)*0.7;
        return `rgba(122,162,247,${alpha.toFixed(2)})`;
      }),
      borderRadius: 3,
    }]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { color: '#1a1a3e' }, ticks: { color: '#565f89', font: { size: 10 } } },
      y: { grid: { color: '#1a1a3e' }, ticks: { color: '#565f89' }, beginAtZero: true }
    }
  }
});

// Live user count — poll every 30s
(function() {
  const net = {{ network|tojson }};
  const chan = {{ channel|tojson }};
  const chanSlug = chan.startsWith('#') ? chan.slice(1) : chan;
  const url = `/api/${net}/${chanSlug}/online`;

  function updateOnline() {
    fetch(url)
      .then(r => r.json())
      .then(data => {
        if (data.online >= 0) {
          const cards = document.querySelectorAll('.summary-item');
          if (cards[0]) {
            cards[0].querySelector('.sv').textContent = data.online;
            cards[0].querySelector('.sl').textContent = 'online now';
          }
        }
      })
      .catch(() => {});
  }

  updateOnline();
  setInterval(updateOnline, 30000);
})();
</script>
</body>
</html>"""


def run_dashboard(config: dict, db_path: str):
    """Start the Flask server (blocking)."""
    set_config(config, db_path)
    web_cfg = config.get("web", {})
    host = web_cfg.get("host", "0.0.0.0")
    port = web_cfg.get("port", 8033)
    log.info(f"Starting dashboard at http://{host}:{port}/")
    app.run(host=host, port=port, debug=False, use_reloader=False)
