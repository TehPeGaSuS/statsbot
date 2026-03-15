# Statsbot

A modern IRC statistics bot inspired by [pisg](https://pisg.github.io/), built
for the 21st century. Instead of parsing log files after the fact, Statsbot
connects to IRC as a bot, collects statistics in real time, and serves a
live web dashboard — no log files, no cron jobs, no static HTML generation.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Why Statsbot instead of pisg?

| | pisg | Statsbot |
|---|---|---|
| Data source | Parse log files | Live IRC connection |
| Setup | Configure bot logging + cron + pisg | Just run Statsbot |
| Log format support | 30+ parsers to maintain | Not needed |
| Output | Static HTML, regenerated periodically | Live web server |
| Stats periods | One fixed window | All-time, today, week, month |
| Peak users | ✗ | ✓ with timestamp |
| Live user count | ✗ | ✓ updates every 30s |
| Karma (`nick++` / `nick--`) | ✓ | ✓ |
| Op/voice/halfop stats | ✓ | ✓ |
| Multi-network | ✗ | ✓ |
| Admin via IRC | ✗ | ✓ via PM commands |

If you already know pisg, the `pisg:` section in `config.yml` uses the same
option names — `ActiveNicks`, `ShowBigNumbers`, `WordHistory`, etc. — so the
[pisg docs](https://pisg.github.io/docs/) apply directly.

---

## Features

- Tracks **words, lines, letters, actions, kicks, modes, bans, joins, topics,
  minutes online, smileys (happy + sad separately), questions, CAPS lines,
  violent actions, foul language, monologues**
- **Karma** — `nick++` / `nick--` suffix syntax; only counts if the target is
  in the channel; nicks containing `--` or `++` are handled correctly
  (e.g. `Mike----` awards −1 to nick `Mike--`)
- **Big numbers** — questions, shouting %, CAPS %, violence + victim tracking
  with example lines, smiles %, sad %, line lengths, monologues, words per line
- **Other interesting numbers** — kicks given/received, most actions, most joins,
  foul language %
- **Most active by hour** — pisg-style 4-band table (0–5, 6–11, 12–17, 18–23)
- **Most used words** — filterable by length and ignore list, with last-used-by nick
- **Most referenced nicks** — who gets mentioned most in conversation, displayed
  with original casing
- **Smiley frequency table** — which specific smiley used most, and by whom
- **Most referenced URLs** — deduplicated with use count and last poster
- **Latest topics** with setter and timestamp
- **Random quotes** in the main table — length-filtered (MinQuote/MaxQuote),
  first message always logged so new speakers never show blank
- **Period tabs** — all-time, today, this week, this month
- **Peak users** with date
- **Live user count** badge, updates every 30 seconds
- **Multi-network** — connect to Libera, Undernet, PTirc simultaneously
- **Op/voice/halfop stats** — who gave ops, who got deopped, who hands out voice;
  pisg-style prose sentences; `ShowOps`, `ShowVoice`, `ShowHalfops` toggles
- **Fully clickable cards** on the landing and network pages
- **PM admin interface** — identify, ignore management, master management
- **bcrypt password auth** — session lasts until disconnect, works from any nick
- **Auto-auth** via hostmask — silent authentication on join if host matches
- **Channel-scoped ignores** — per-channel or network-wide
- **Automatic DB migrations** — upgrading never requires manual schema changes

---

## Requirements

- Python 3.11+
- A virtualenv (required on Debian 12+, Ubuntu 24.04+, and any distro that
  ships PEP 668 — direct `pip install` into the system Python is blocked)

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/TehPeGaSuS/Statsbot.git
cd Statsbot

# 2. Create and activate a virtualenv
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure
cp config/config.yml.example config/config.yml
nano config/config.yml          # set your networks, channels, nick

# 5. Set up master password(s) — interactive wizard
python main.py --setup

# 6. Run
python main.py
```

> **Debian 12+ / Ubuntu 24.04+ note:** these distros enforce PEP 668 and block
> `pip install` outside a virtualenv. Always use `.venv` as shown above.

The web dashboard is at `http://localhost:8033/` by default.

---

## Configuration overview

```yaml
bot:        # nick, altnick, realname, ident
networks:   # list of IRC servers and channels
stats:      # tracking options, ignore lists, smiley lists
web:        # dashboard host/port/public_url
commands:   # prefix, flood protection
pisg:       # page layout (pisg-compatible option names)
database:   # SQLite path
logging:    # level and log file
```

See **[DOCS.md](DOCS.md)** for the complete reference.

---

## IRC commands

### Channel commands

| Command | Description |
|---------|-------------|
| `!stats` | Link to the stats page for this channel |
| `!top [n]` | Top N users by words (default 3, max 10) |
| `!quote [nick]` | Random quote, optionally from a specific nick |

### PM commands (`/msg statsbot <command>`)

| Command | Description |
|---------|-------------|
| `identify <master> <password>` | Authenticate — works from any nick on any network |
| `logout` / `whoami` / `status` | Session management |
| `ignore add [#chan] <pattern>` | Add ignore (network-wide if no `#chan`) |
| `ignore del [#chan] <pattern>` | Remove ignore |
| `ignore list [#chan]` | List ignores |
| `master add <nick>` | Add master (bot asks for password interactively) |
| `master del <nick>` / `master list` | Manage masters |
| `set page [#chan] <url>` | Override `!stats` URL for a channel |

---

## Stats page URL structure

```
http://yourserver:8033/                        — all networks (clickable cards)
http://yourserver:8033/<network>/              — channels on a network (clickable cards)
http://yourserver:8033/<network>/<channel>/    — full pisg-style stats page
http://yourserver:8033/<network>/<channel>/?period=1   — today
```

Period values: `0` = all-time (default), `1` = today, `2` = this week, `3` = this month.

Set `web.public_url` in config so `!stats` generates proper external links:

```yaml
web:
  public_url: "https://stats.yourserver.org"
```

---

## Karma

Users can award or deduct karma points by appending `++` or `--` to a nick:

```
<Alice> Bob++
<Alice> Bob--
```

Rules:
- **Suffix only** — `nick++` and `nick--`; prefix forms (`++nick`) are not recognised
- **Channel membership** — the target nick must currently be in the channel; random
  words ending in `++`/`--` are silently ignored
- **No self-karma** — you cannot modify your own score
- **Nicks containing `--` or `++`** are handled correctly: `Mike----` strips
  exactly the last two characters, awarding −1 to nick `Mike--`

Karma scores appear in the **Karma** section of the channel stats page, sorted
highest to lowest. Negative scores are shown in red. Toggle with `ShowKarma`
in the `pisg:` config section.

---

## Running as a service

Two options: a **user unit** (recommended — no root required, starts on login
or boot if lingering is enabled) or a **system unit** (runs as a dedicated
user, requires root to install).

### User unit (recommended)

```ini
# ~/.config/systemd/user/statsbot.service
[Unit]
Description=Statsbot IRC statistics bot
After=network.target

[Service]
Type=simple
WorkingDirectory=%h/Statsbot
ExecStart=%h/Statsbot/.venv/bin/python main.py
Restart=on-failure
RestartSec=30
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
```

```bash
# Install and start
systemctl --user daemon-reload
systemctl --user enable --now statsbot

# Optional: keep running after logout (requires root once)
loginctl enable-linger $USER

# Useful commands
systemctl --user status statsbot
journalctl --user -u statsbot -f
```

`%h` expands to your home directory. Adjust `WorkingDirectory` and
`ExecStart` if you cloned Statsbot somewhere other than `~/Statsbot`.

### System unit

Use this if you want Statsbot to run as a dedicated system user
(e.g. `statsbot`) rather than your own account.

```ini
# /etc/systemd/system/statsbot.service
[Unit]
Description=Statsbot IRC statistics bot
After=network.target

[Service]
Type=simple
User=statsbot
WorkingDirectory=/opt/statsbot
ExecStart=/opt/statsbot/.venv/bin/python main.py
Restart=on-failure
RestartSec=30
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
# Create a dedicated user (no login shell, no home directory)
sudo useradd -r -s /usr/sbin/nologin -d /opt/statsbot statsbot
sudo mkdir -p /opt/statsbot
sudo chown statsbot:statsbot /opt/statsbot

# Install and start (as root)
sudo systemctl daemon-reload
sudo systemctl enable --now statsbot

# Logs
sudo journalctl -u statsbot -f
```

---

## Project structure

```
Statsbot/
├── main.py                  # Entry point, --setup wizard
├── config/
│   └── config.yml           # All configuration
├── bot/
│   ├── auth.py              # Master auth — sessions, bcrypt, auto-auth via mask
│   ├── connector.py         # Async IRC connection, RFC 1459 parser, WHO handler
│   ├── parser.py            # Message parsing: words, smileys, caps, violent, foul
│   ├── sensors.py           # Event handlers — stats, quotes, karma, monologues
│   └── scheduler.py         # Daily/weekly/monthly stat resets
├── database/
│   └── models.py            # SQLite schema, all queries, auto-migrations
├── irc/
│   ├── commands.py          # Channel commands: !stats, !top, !quote
│   └── pm_commands.py       # PM admin: identify, ignore, master, set
└── web/
    ├── dashboard.py         # Flask: landing, network pages, JSON API
    └── pisg_page.py         # Full pisg-style channel stats page
```

---

## Contributing

Issues and pull requests are welcome. If you're adding a feature, please:

- Follow the existing code style (no external deps beyond `requirements.txt`)
- Add a test in the relevant `python -c` style check if possible
- Update `DOCS.md` if you add or change a config option

If you're familiar with pisg, feature parity PRs are especially welcome —
see the "not yet implemented" table below for what's missing.

## What's not yet implemented vs pisg

| pisg feature | Status |
|---|---|
| User pictures | Not yet |
| Gender stats | Not yet |
| Daily activity graph (lines per day) | Not yet |
| NickTracking / nick aliases | Not yet |
| Music charts (`now playing:`) | Not yet |
| Op/voice/halfop statistics | ✓ |
| `ShowTime` (when-active time bar) | Not yet |

All other pisg features are implemented. Contributions welcome.
