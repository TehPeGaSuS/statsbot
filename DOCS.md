# ircstats configuration reference

This document covers all configuration options for ircstats. Option names in
the `pisg:` section deliberately match [pisg](https://pisg.github.io/) so
that anyone familiar with pisg can migrate without reading much.

---

## Table of contents

1. [Bot settings](#bot-settings)
2. [Network settings](#network-settings)
3. [Stats tracking](#stats-tracking)
4. [Web dashboard](#web-dashboard)
5. [IRC commands](#irc-commands)
6. [pisg-style page options](#pisg-style-page-options)
   - [Main nick table](#main-nick-table)
   - [Big numbers](#big-numbers)
   - [Section toggles](#section-toggles)
   - [History limits](#history-limits)
   - [Karma](#karma-options)
   - [Page](#page)
7. [Database](#database)
8. [Logging](#logging)

---

## Bot settings

```yaml
bot:
  nick: "statsbot"       # Primary nick
  altnick: "statsbot_"   # Fallback nick if primary is taken
  realname: "IRC Stats Bot"
  ident: "statsbot"
  # Masters are configured via: python main.py --setup
```

Masters (users allowed to run admin commands) are stored in the database with
bcrypt-hashed passwords. Use `python main.py --setup` to add them interactively.
See [PM commands](#irc-commands) for runtime management.

---

## Network settings

```yaml
networks:
  - name: "libera"                  # Internal name — used in URLs and DB
    host: "irc.libera.chat"
    port: 6697                      # 6667 plain, 6697 SSL
    ssl: true
    channels:
      - "#yourchannel"

    # ── Authentication (pick one or combine) ──────────────────────────────

    # SASL PLAIN — preferred on modern networks (Libera, OFTC, hackint …)
    sasl:
      username: "statsbot"
      password: "your_password"

    # NickServ IDENTIFY — legacy, works on most networks including Undernet
    # nickserv_password: "your_password"

    # Nick ghosting — if primary nick is taken, send GHOST and reclaim it.
    # Requires nickserv_password or sasl.password.
    # ghost: true

    # Server password — for BNCs or private servers that require PASS
    # server_password: "your_password"

    # ── Post-connect commands ─────────────────────────────────────────────
    # Sent after authentication completes. {nick} = current bot nick.
    # on_connect:
    #   - "MODE {nick} +x"      # request cloak (Undernet, others)
    #   - "UMODE2 +x"           # alternative cloak (UnrealIRCd)
    #   - "MODE {nick} +B"      # set bot flag
```

### Authentication flow

1. If `server_password` is set, `PASS` is sent before `NICK`/`USER`.
2. If `sasl` is set, `CAP REQ :sasl` is sent before `NICK`/`USER`. On success
   the bot sends `AUTHENTICATE PLAIN` → base64(`user\0user\0pass`) → `CAP END`.
   If the server rejects SASL, falls back to NickServ.
3. On `001` (welcome), if SASL didn't auth, NickServ `IDENTIFY` is sent.
4. If `ghost: true` and the primary nick is taken (`433`), the bot:
   - Switches to `altnick`
   - Sends `PRIVMSG NickServ :GHOST <nick> <password>`
   - Watches for the confirmation notice and then sends `NICK <primary>`
5. `on_connect` commands fire after authentication is confirmed (or immediately
   on `001` if no auth is configured).

### Per-network identity

Any of the four identity fields (`nick`, `altnick`, `realname`, `ident`) set
inside a network entry override the global `bot:` defaults for that network
only. This lets the bot present different nicks on different networks:

```yaml
bot:
  nick: "statsbot"          # used everywhere unless overridden
  altnick: "statsbot_"

networks:
  - name: "libera"
    host: "irc.libera.chat"
    # inherits bot.nick = "statsbot"

  - name: "undernet"
    host: "irc.undernet.org"
    nick: "ChanStatsBot"    # overrides bot.nick on Undernet only
    altnick: "ChanStats_"
```

`{nick}` in `on_connect` commands substitutes the actual nick the bot is
currently using on that network (could be `altnick` if primary was taken).

### Multiple networks

Add more entries to the `networks:` list — each connects independently:

```yaml
networks:
  - name: "libera"
    host: "irc.libera.chat"
    port: 6697
    ssl: true
    channels:
      - "#bots"
    sasl:
      username: "statsbot"
      password: "libera_pass"

  - name: "undernet"
    host: "irc.undernet.org"
    port: 6667
    ssl: false
    channels:
      - "#allnitecafe"
    nickserv_password: "undernet_pass"
    ghost: true
    on_connect:
      - "MODE {nick} +x"

  - name: "ptirc"
    host: "irc.ptirc.org"
    port: 6667
    ssl: false
    channels:
      - "#lobby"
      - "#help"
```

Each network gets its own session, ignore list, and stats namespace. The web
dashboard groups channels by network automatically.

---

## Stats tracking

```yaml
stats:
  ignore:
    - "ChanServ"
    - "*Serv"
    - "*bot*"
    - "*!*@services.example.org"  # full hostmask pattern
  # Channel-specific ignores via PM: /msg statsbot ignore add #chan pattern

  auto_ignore_bots: false   # Auto-ignore nicks with IRC +B umode. Unreliable —
                             # real users can set +B. Default off; use ignore list.

  expire_days: 30           # Remove nicks not seen for X days (0 = never)
  log_wordstats: true       # Track word frequency per nick
  min_word_length: 3        # Minimum chars for a word to be tracked in DB
                             # (display filter is controlled by WordLength in pisg:)
  quote_frequency: 5        # Log every Nth message as a random quote.
                             # First message from any nick is always logged.
  display_urls: 5           # How many recent URLs to show (also see UrlHistory)
  display_kicks: 5          # How many recent kicks to show
  kick_context: 5           # Lines of channel context saved with each kick

  happy_smileys: [...]      # List of smileys counted as happy (smileys stat)
  sad_smileys: [...]        # List of smileys counted as sad (sad stat)
```

### Ignore patterns

Patterns support wildcards (`*`, `?`). Two forms are recognised:

- **Nick pattern** — no `!` or `@`: matches against the nick only
  - `ChanServ`, `*bot*`, `*Serv`
- **Hostmask pattern** — contains `!` or `@`: matched against `nick!user@host`
  - `*!*@services.ptirc.org`, `badnick!*@*`

Config-level ignores apply network-wide. Channel-specific ignores can be added
at runtime via PM commands.

---

## Web dashboard

```yaml
web:
  enabled: true
  host: "0.0.0.0"
  port: 8033
  public_url: ""     # e.g. https://stats.yourserver.org
                     # Used by !stats to generate the channel link.
                     # If empty, falls back to http://localhost:PORT/
  title: "IRC Stats"
  topnr: 30          # Users shown in landing page top lists
```

The dashboard serves three pages:

| URL | Description |
|-----|-------------|
| `/` | Landing page — network cards (whole card is clickable) |
| `/<network>/` | Network page — channel cards (whole card is clickable) |
| `/<network>/<channel>/` | Full pisg-style channel stats page |

The landing page shows each network as a card displaying the network name,
IRC server address, user count, and channel count. Clicking anywhere on the
card navigates to the network page. The network page shows each channel as a
card with tracked users, total words, and total lines; clicking navigates to
the full channel stats page.

Live user count on the channel page polls `/api/<network>/<channel>/online`
every 30 seconds via JavaScript.

---

## IRC commands

The default command prefix is `!`, set globally under `commands.prefix` in
`config.yml`. If a network already uses `!` for something else (e.g. Anope
BotServ fantasy commands), you can override it per network with `cmd_prefix`:

```yaml
commands:
  prefix: "!"               # global default

networks:
  - name: "libera"
    host: "irc.libera.chat"
    # inherits prefix "!"

  - name: "undernet"
    host: "irc.undernet.org"
    cmd_prefix: "."         # use . on this network instead
```

### Channel commands (public)

| Command | Description |
|---------|-------------|
| `!stats` | Link to stats page for this channel |
| `!top [n]` | Top N users by words, single line (default 3, max 10) |
| `!quote [nick]` | Random quote, optionally from a specific nick |

### PM commands (`/msg statsbot <command>`)

**Authentication:**

| Command | Description |
|---------|-------------|
| `identify <master_nick> <password>` | Authenticate as a master. Works from any nick on any network. Session lasts until you disconnect. |
| `logout` | End your session |
| `whoami` | Show your current identity |
| `status` | Show connected channels and user counts |

**Ignore management** (requires auth):

| Command | Description |
|---------|-------------|
| `ignore add [#channel] <pattern>` | Add ignore. Omit `#channel` for network-wide. |
| `ignore del [#channel] <pattern>` | Remove ignore |
| `ignore list [#channel]` | List ignores, optionally filtered by channel |

**Master management** (requires auth):

| Command | Description |
|---------|-------------|
| `master add <nick>` | Add a master (bot will ask for password interactively) |
| `master del <nick>` | Remove a master |
| `master list` | List all masters |

**Configuration** (requires auth):

| Command | Description |
|---------|-------------|
| `set page [#channel] <url>` | Override the stats URL returned by `!stats` |

---

## pisg-style page options

All options go under the `pisg:` section in `config.yml`. Names match pisg
exactly so the [pisg documentation](https://pisg.github.io/docs/) applies.

### Main nick table

#### `ActiveNicks`
Number of nicks shown in the "Most active nicks" table.
**Default:** `25` — pisg default: 25

#### `ActiveNicks2`
Number of nicks shown in the "These didn't make it to the top" secondary list.
**Default:** `50` — pisg default: 30

#### `SortByWords`
Sort the main nick table by words (`true`) or lines (`false`).
**Default:** `true` — pisg default: false (lines)

#### `ShowLines`
Show the line count column in the main nick table.
**Default:** `true`

#### `ShowWords`
Show the word count column in the main nick table.
**Default:** `true` — pisg default: false

#### `ShowWpl`
Show the words-per-line column.
**Default:** `true` — pisg default: false

#### `ShowCpl`
Show the characters-per-line column.
**Default:** `false` — pisg default: false

#### `ShowLastSeen`
Show when each nick was last seen (e.g. "2 days ago").
**Default:** `true`

#### `ShowRandQuote`
Show a random quote from each nick in the main table.
**Default:** `true`

#### `MinQuote`
Minimum character length for a quote to be selected for display.
Falls back to any quote if none found in range.
**Default:** `25` — pisg default: 25

#### `MaxQuote`
Maximum character length for a quote to be selected.
**Default:** `65` — pisg default: 65

---

### Big numbers

#### `ShowBigNumbers`
Master switch for the "Big numbers" and "Other interesting numbers" sections.
Setting this to `false` hides both sections entirely.
**Default:** `true`

#### `BigNumbersThreshold`
Minimum lines a nick must have to appear in big numbers statistics
(questions, CAPS, smileys, line lengths, sad faces). Set to `"sqrt"` for
automatic threshold (square root of the most active nick's line count).
**Default:** `"sqrt"` — pisg default: sqrt

#### `ViolentWords`
Words considered aggressive/violent. Used to detect `/me slaps` style actions.
The first nick mentioned after a violent word is recorded as the victim.
**Default:** `["slaps", "beats", "kicks", "hits", "smacks", "stabs"]` — pisg default: `slaps beats smacks`

#### `FoulWords`
Words considered foul language. Tracked as a percentage of total words.
**Default:** `["ass", "fuck", "shit", "bitch", "cunt", "cock", "dick"]`

> **Note:** In ircstats, individual big number sub-sections (questions, CAPS,
> violence, smiles, etc.) are all controlled by `ShowBigNumbers`. In pisg they
> were always on when `ShowBigNumbers` was on. Future versions may add
> individual toggles.

---

### Section toggles

#### `ShowMostActiveByHour`
Show the "Most active nicks by hour" table, split into four 6-hour bands
(0–5, 6–11, 12–17, 18–23). pisg equivalent: `ShowMostActiveByHour`.
**Default:** `true` — pisg default: false

#### `ShowSmileys`
Show the smiley frequency table — which specific smileys were used most
and by whom.
**Default:** `true` — pisg default: false

#### `ShowMrn`
Show the "Most referenced nicks" section — which nicks are mentioned most
in conversation. Nick casing is preserved as it appears on IRC.
**Default:** `true` — pisg default: true

#### `ShowMru`
Show the "Most referenced URLs" section. URLs are deduplicated — repeated
posts of the same URL increment the count.
**Default:** `true` — pisg default: true

#### `ShowLegend`
Show the page legend/footer with totals (lines, nicks, avg wpl, avg cpl,
topics set count).
**Default:** `true`

---

### History limits

#### `WordHistory`
Maximum number of words shown in "Most used words".
**Default:** `10` — pisg default: 10

#### `WordLength`
Minimum word length to appear in "Most used words". Words shorter than
this are filtered from display (but still tracked in the DB).
**Default:** `5` — pisg default: 5

#### `IgnoreWords`
Space or list of words to exclude from "Most used words" and "Most referenced
nicks" detection. Useful for filtering common words.
**Default:** `[]` (empty)

```yaml
IgnoreWords: ["there", "about", "think", "would", "going"]
```

#### `NickHistory`
Maximum number of nicks shown in "Most referenced nicks".
**Default:** `5` — pisg default: 5

#### `SmileyHistory`
Maximum number of smileys shown in the smiley frequency table.
**Default:** `10` — pisg default: 10

#### `UrlHistory`
Maximum number of URLs shown in "Most referenced URLs".
**Default:** `10` — pisg default: 5

#### `TopicHistory`
Maximum number of topics shown in "Latest topics".
**Default:** `5` — pisg default: 3

#### `ActiveNicksByHour`
Number of rows in the "Most active nicks by hour" table (per band).
**Default:** `10` — pisg default: 10

---

### Karma options

#### `ShowKarma`
Show the Karma leaderboard section on the channel stats page. Scores are
sorted highest to lowest; positive scores are green, negative are red.
**Default:** `true`

#### `KarmaHistory`
Maximum number of nicks shown in the karma table.
**Default:** `10`

#### How karma works

Users award or deduct karma by appending `++` or `--` to a nick in any
channel message:

```
<Alice> Bob++        ← Bob's score +1
<Alice> Bob--        ← Bob's score -1
```

**Rules enforced by the bot:**

- **Suffix only** — `nick++` / `nick--`. Prefix forms (`++nick`) are ignored.
- **Channel membership required** — the target nick must be present in the
  channel at the time of the message. Messages referencing absent nicks or
  random words ending in `++`/`--` are silently ignored.
- **No self-karma** — a user cannot modify their own score.
- **Nicks containing `--` or `++`** are handled correctly. The bot strips
  exactly the last two characters to find the nick, so `Mike----` awards −1
  to nick `Mike--`, not to nick `Mike`.
- Karma is stored per-channel; a nick's score on one network/channel is
  independent of any other.

---

### Page

#### `Maintainer`
Name shown in the page footer ("stats by X"). Can be your nick or the
bot's name.
**Default:** `""` (empty, not shown)

---

## Database

```yaml
database:
  path: "data/stats.db"    # Path to SQLite database file
```

The database is SQLite — zero setup, zero dependencies beyond Python's
built-in `sqlite3`. Schema migrations run automatically on startup so
upgrading ircstats never requires manual DB changes.

---

## Logging

```yaml
logging:
  level: "INFO"             # DEBUG, INFO, WARNING, ERROR
  file: "data/ircstats.log"
```

---

## Differences from pisg

| Feature | pisg | ircstats |
|---------|------|----------|
| Data source | Static log files | Live IRC bot (real-time) |
| Log parsers | 30+ formats | Not needed — we receive events directly |
| Tracking | Per-mask (nick!user@host) | Per-nick |
| Nick merging | Aliases + NickTracking | Not supported (each nick is its own entry) |
| Output | Static HTML file | Live Flask web server |
| Periods | One fixed period (log window) | All-time, today, this week, this month |
| Peak users | Not tracked | Tracked with timestamp |
| Live count | Not possible | Yes, polls every 30s |
| Karma (`nick++`) | Yes | Yes |
| User pictures | Yes | Not yet |
| Gender stats | Yes | Not yet |
| Music charts | Yes | Not yet |
| Daily activity graph | Yes | Not yet |
| NickTracking / aliases | Yes | Not yet |
