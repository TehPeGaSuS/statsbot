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

    from database.models import get_enabled_networks
    all_networks = [n["name"] for n in get_enabled_networks()]
    return render_template_string(NETWORK_TMPL,
        network=network,
        host=host,
        channels=channels,
        all_networks=all_networks,
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

:root {
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
  --header-grad: linear-gradient(135deg,#1a1a3e 0%,#0d0d1a 100%);
}
body.light {
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
  --header-grad: linear-gradient(135deg,#dde2f5 0%,#f5f6fa 100%);
}
@media (prefers-color-scheme: light) {
  :root:not(.dark-override) {
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
    --header-grad: linear-gradient(135deg,#dde2f5 0%,#f5f6fa 100%);
  }
}

/* ── Theme toggle ── */
.theme-toggle {
  position: fixed; bottom: 1.2rem; right: 1.2rem; z-index: 999;
  background: var(--bg2); border: 1px solid var(--border);
  border-radius: 50%; width: 2.4rem; height: 2.4rem;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; font-size: 1.1rem; box-shadow: 0 2px 8px rgba(0,0,0,.3);
  transition: background .2s, border-color .2s;
  user-select: none;
}
.theme-toggle:hover { border-color: var(--blue); }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', Tahoma, monospace; }
.header { background: var(--header-grad);
          border-bottom: 1px solid var(--border); padding: 1.5rem 2rem; }
.header a { color: var(--blue); text-decoration: none; font-size: .85rem; }
.header h1 { font-size: 1.8rem; color: var(--blue); margin-top: .3rem; }
.header .meta { color: var(--muted); font-size: .82rem; margin-top: .25rem; font-family: monospace; }
.container { max-width: 1100px; margin: 2rem auto; padding: 0 1.5rem; }
.chan-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 1rem; }
.chan-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 10px;
             padding: 1.2rem 1.4rem; transition: border-color .2s, box-shadow .2s;
             display: block; text-decoration: none; color: inherit; cursor: pointer; }
.chan-card:hover { border-color: var(--blue); box-shadow: 0 0 0 1px var(--blue); }
.chan-card .cn { font-size: 1.1rem; font-weight: bold; margin-bottom: .8rem; color: var(--blue); }
.stat-row { display: flex; justify-content: space-between; padding: .22rem 0;
             border-bottom: 1px solid var(--bg3); font-size: .83rem; }
.stat-row:last-child { border-bottom: none; }
.stat-row .sk { color: var(--muted); }
.stat-row .sv { color: var(--yellow); font-weight: bold; }
.footer { text-align: center; color: var(--faint); font-size: .78rem;
          margin: 3rem 0 1.5rem; border-top: 1px solid var(--bg3); padding-top: 1rem; }

@media (max-width: 600px) {
  .container { padding: 0 .7rem; }
  .header { padding: 1rem; }
  .chan-grid { grid-template-columns: 1fr; }
  .nav-row { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:.5rem; }
  .net-switcher { display:flex; gap:.5rem; flex-wrap:wrap; }
  .net-switcher a { font-size:.78rem; color:var(--blue); padding:.2rem .6rem;
    border:1px solid var(--border); border-radius:12px; text-decoration:none; }
  .net-switcher a:hover { border-color:var(--blue); }
}
</style>
</head>
<body>
<div class="header">
  <div class="nav-row">
    <a href="/">← All networks</a>
    {% if all_networks|length > 1 %}
    <div class="net-switcher">
      {% for n in all_networks %}
        {% if n != network %}
          <a href="/{{ n }}/">{{ n }}</a>
        {% endif %}
      {% endfor %}
    </div>
    {% endif %}
  </div>
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
  <p style="color:var(--muted);text-align:center;padding:3rem 0">No channels tracked on this network yet.</p>
  {% endif %}
</div>
<div class="footer"><a href="{{ project_url }}" style="color:var(--faint)">Statsbot</a> &mdash; Inspired by <a href="https://pisg.github.io/" style="color:var(--faint)">PISG</a> by Morten Brix Pedersen and others</div>
<button class="theme-toggle" id="themeToggle" title="Toggle light/dark"></button>

<script>
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
      // follow OS
      document.body.classList.remove('light');
      root.classList.remove('dark-override');
      var preferLight = window.matchMedia('(prefers-color-scheme: light)').matches;
      btn.textContent = preferLight ? '🌙' : '☀️';
    }
  }
  applyTheme(getTheme());
  btn.addEventListener('click', function() {
    var cur = getTheme();
    if (!cur) { cur = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'; }
    var next = (cur === 'light') ? 'dark' : 'light';
    localStorage.setItem('theme', next);
    applyTheme(next);
  });
})();
</script>
</body>
</html>"""


INDEX_TMPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<style>

:root {
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
  --header-grad: linear-gradient(135deg,#1a1a3e 0%,#0d0d1a 100%);
}
body.light {
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
  --header-grad: linear-gradient(135deg,#dde2f5 0%,#f5f6fa 100%);
}
@media (prefers-color-scheme: light) {
  :root:not(.dark-override) {
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
    --header-grad: linear-gradient(135deg,#dde2f5 0%,#f5f6fa 100%);
  }
}

/* ── Theme toggle ── */
.theme-toggle {
  position: fixed; bottom: 1.2rem; right: 1.2rem; z-index: 999;
  background: var(--bg2); border: 1px solid var(--border);
  border-radius: 50%; width: 2.4rem; height: 2.4rem;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; font-size: 1.1rem; box-shadow: 0 2px 8px rgba(0,0,0,.3);
  transition: background .2s, border-color .2s;
  user-select: none;
}
.theme-toggle:hover { border-color: var(--blue); }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', Tahoma, monospace; }

.header { background: var(--header-grad);
          border-bottom: 1px solid var(--border); padding: 2.5rem 2rem; text-align: center; }
.header h1 { font-size: 2.2rem; color: var(--blue); letter-spacing: 2px; margin-bottom: .4rem; }
.header .sub { color: var(--muted); font-size: .85rem; }

.container { max-width: 960px; margin: 2.5rem auto; padding: 0 1.5rem; }

/* Global stats bar */
.globals { display: flex; gap: 1rem; margin-bottom: 2.5rem; flex-wrap: wrap; }
.glob-card { flex: 1; min-width: 140px; background: var(--bg2);
             border: 1px solid var(--border); border-radius: 10px;
             padding: .9rem 1.4rem; text-align: center; }
.glob-card .gv { font-size: 1.8rem; font-weight: bold; color: var(--blue); }
.glob-card .gl { font-size: .75rem; color: var(--muted); margin-top: .25rem;
                  text-transform: uppercase; letter-spacing: .5px; }

/* Section label */
.section-label { font-size: .75rem; color: var(--muted); text-transform: uppercase;
                  letter-spacing: 1px; margin-bottom: 1rem; }

/* Network cards */
.net-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1.2rem; }
.net-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 10px;
            padding: 1.4rem; display: block; text-decoration: none; color: inherit;
            cursor: pointer; transition: border-color .2s, box-shadow .2s; }
.net-card:hover { border-color: var(--green); box-shadow: 0 0 0 1px var(--green); }
.net-card .net-name { font-size: 1.15rem; font-weight: bold; color: var(--green);
                       margin-bottom: .2rem; }
.net-card .net-host { font-size: .8rem; color: var(--muted); margin-bottom: 1rem;
                       font-family: monospace; }
.net-meta { display: flex; gap: 1.2rem; }
.net-meta .nm { text-align: center; }
.net-meta .nmv { font-size: 1.2rem; font-weight: bold; color: var(--yellow); }
.net-meta .nml { font-size: .72rem; color: var(--muted); text-transform: uppercase; letter-spacing: .4px; }

.empty { color: var(--muted); text-align: center; padding: 3rem 0; font-size: .9rem; }

.footer { text-align: center; color: var(--faint); font-size: .78rem;
          margin: 3rem 0 1.5rem; border-top: 1px solid var(--bg3); padding-top: 1rem; }

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
  <div class="sub">Updated {{ now }}</div>
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

<div class="footer"><a href="{{ project_url }}" style="color:var(--faint)">Statsbot</a> &mdash; Inspired by <a href="https://pisg.github.io/" style="color:var(--faint)">PISG</a> by Morten Brix Pedersen and others</div>
<button class="theme-toggle" id="themeToggle" title="Toggle light/dark"></button>

<script>
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
      // follow OS
      document.body.classList.remove('light');
      root.classList.remove('dark-override');
      var preferLight = window.matchMedia('(prefers-color-scheme: light)').matches;
      btn.textContent = preferLight ? '🌙' : '☀️';
    }
  }
  applyTheme(getTheme());
  btn.addEventListener('click', function() {
    var cur = getTheme();
    if (!cur) { cur = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'; }
    var next = (cur === 'light') ? 'dark' : 'light';
    localStorage.setItem('theme', next);
    applyTheme(next);
  });
})();
</script>
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

:root {
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
  --header-grad: linear-gradient(135deg,#1a1a3e 0%,#0d0d1a 100%);
}
body.light {
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
  --header-grad: linear-gradient(135deg,#dde2f5 0%,#f5f6fa 100%);
}
@media (prefers-color-scheme: light) {
  :root:not(.dark-override) {
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
    --header-grad: linear-gradient(135deg,#dde2f5 0%,#f5f6fa 100%);
  }
}

/* ── Theme toggle ── */
.theme-toggle {
  position: fixed; bottom: 1.2rem; right: 1.2rem; z-index: 999;
  background: var(--bg2); border: 1px solid var(--border);
  border-radius: 50%; width: 2.4rem; height: 2.4rem;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; font-size: 1.1rem; box-shadow: 0 2px 8px rgba(0,0,0,.3);
  transition: background .2s, border-color .2s;
  user-select: none;
}
.theme-toggle:hover { border-color: var(--blue); }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text);
       font-family: 'Segoe UI', Tahoma, monospace; font-size: 14px; }

.header { background: var(--header-grad);
          border-bottom: 1px solid var(--border); padding: 1.5rem 2rem; }
.header h1 { font-size: 1.8rem; color: var(--blue); }
.header .meta { color: var(--muted); font-size: .85rem; margin-top: .3rem; }
.header a { color: var(--blue); text-decoration: none; font-size: .85rem; }

.container { max-width: 1200px; margin: 1.5rem auto; padding: 0 1rem; }

/* Period tabs */
.tabs { display: flex; gap: .5rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
.tab { padding: .4rem 1rem; border-radius: 20px; border: 1px solid var(--border);
       color: var(--muted); text-decoration: none; font-size: .85rem; transition: all .2s; }
.tab.active { background: var(--tab-act); color: #fff; border-color: var(--tab-act); }
.tab:hover:not(.active) { border-color: var(--blue); color: var(--blue); }

/* Section headers */
.section { margin-bottom: 2rem; }
.section-title { font-size: 1rem; color: var(--blue); letter-spacing: 1px;
                  text-transform: uppercase; margin-bottom: .8rem;
                  padding-bottom: .4rem; border-bottom: 1px solid #1e2a45; }

/* Stats table */
.tscroll { overflow-x: auto; -webkit-overflow-scrolling: touch; margin-bottom: 1rem; }
.tscroll > table { margin-bottom: 0; min-width: 480px; }
.stats-table { width: 100%; border-collapse: collapse; }
.stats-table th { background: var(--bg3); color: var(--blue); text-align: left;
                   padding: .5rem .7rem; font-size: .8rem; letter-spacing: .5px;
                   text-transform: uppercase; }
.stats-table td { padding: .4rem .7rem; border-bottom: 1px solid var(--bg3); }
.stats-table tr:hover td { background: var(--bg3); }
.stats-table .rank { color: var(--muted); width: 40px; }
.stats-table .nick { color: var(--green); font-weight: bold; }
.stats-table .bar-cell { width: 200px; }
.bar-wrap { background: var(--bg3); border-radius: 3px; height: 12px; overflow: hidden; }
.bar-fill { height: 100%; border-radius: 3px;
             background: linear-gradient(90deg, var(--tab-act), var(--blue)); }
.val { color: var(--yellow); font-weight: bold; }

/* Two-column grid */
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }
@media (max-width: 700px) { .grid-2 { grid-template-columns: 1fr; } }

/* Card */
.card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
         padding: 1rem; }

/* Heatmap chart */
.chart-wrap { position: relative; height: 160px; }

/* Word cloud */
.word-cloud { display: flex; flex-wrap: wrap; gap: .4rem; padding: .5rem 0; }
.word-tag { background: var(--bg3); border: 1px solid var(--border); border-radius: 4px;
             padding: .2rem .5rem; font-size: .8rem; color: var(--blue); }
.word-tag .wc { color: var(--muted); font-size: .75rem; margin-left: .3rem; }

/* Misc items */
.misc-item { padding: .4rem 0; border-bottom: 1px solid var(--bg3); font-size: .85rem; }
.misc-item .by { color: var(--muted); margin-left: .5rem; font-size: .8rem; }
.misc-item .when { color: var(--cyan); font-size: .75rem; margin-left: .5rem; }

/* Nick table (user list) */
.nick-alpha a { color: var(--blue); text-decoration: none; margin: 0 .2rem; }

/* Summary bar */
.summary { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
.summary-item { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
                 padding: .6rem 1.2rem; flex: 1; min-width: 120px; text-align: center; }
.summary-item .sv { font-size: 1.4rem; color: var(--blue); font-weight: bold; line-height: 1.2; }
.summary-item .sl { font-size: .72rem; color: var(--muted); margin-top: .3rem; text-transform: uppercase; letter-spacing: .4px; }

.footer { text-align: center; color: var(--muted); font-size: .8rem;
           margin: 3rem 0 1rem; border-top: 1px solid var(--bg3); padding-top: 1rem; }
</style>
</head>
<body>

<div class="header">
  <a href="/">← All channels</a>
  <h1>{{ channel }}  <span style="color:var(--muted);font-size:1rem">on {{ network }}</span></h1>
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
             style="color:var(--blue);text-decoration:none">
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
          <span style="color:var(--red)">{{ k.victim }}</span>
          kicked by <span style="color:var(--green)">{{ k.kicker }}</span>
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
  <a href="{{ project_url }}" style="color:var(--muted)">Statsbot</a> — Inspired by <a href="https://pisg.github.io/" style="color:var(--faint)">PISG</a> by Morten Brix Pedersen and others &nbsp;·&nbsp; {{ now }}
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
      x: { grid: { color: getComputedStyle(document.body).getPropertyValue('--bg3').trim() }, ticks: { color: getComputedStyle(document.body).getPropertyValue('--muted').trim(), font: { size: 10 } } },
      y: { grid: { color: getComputedStyle(document.body).getPropertyValue('--bg3').trim() }, ticks: { color: getComputedStyle(document.body).getPropertyValue('--muted').trim() }, beginAtZero: true }
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
<button class="theme-toggle" id="themeToggle" title="Toggle light/dark"></button>

<script>
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
      // follow OS
      document.body.classList.remove('light');
      root.classList.remove('dark-override');
      var preferLight = window.matchMedia('(prefers-color-scheme: light)').matches;
      btn.textContent = preferLight ? '🌙' : '☀️';
    }
  }
  applyTheme(getTheme());
  btn.addEventListener('click', function() {
    var cur = getTheme();
    if (!cur) { cur = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'; }
    var next = (cur === 'light') ? 'dark' : 'light';
    localStorage.setItem('theme', next);
    applyTheme(next);
  });
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
