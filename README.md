# Trans

**Transparency for your portfolio.**

A local dashboard over your brokerage data: per-ticker XIRR, exactly what you bought
each day, every dividend, allocation and concentration, and fundamentals chosen for
each holding's sector.

Everything runs on your machine. **Python standard library only — nothing to install.**
Data lives in a SQLite file you own. Claude Code is the only tool needed to operate it.

```bash
./run.sh          # http://127.0.0.1:8787
```

## What you get

| Tab | Shows |
|---|---|
| **Overview** | allocation, weights, per-ticker XIRR, best and worst performers |
| **Holdings** | sortable table — XIRR, invested, value, dividends, P/L, simple return |
| **Daily buys** | your recurring programme: per ticker, per day, share of new money |
| **Dividends** | lifetime total, by year, by ticker |
| **Analysis** | sector exposure, concentration (top 1/3/5/10), the sub-1% tail, monthly contributions |
| **Fundamentals** | sector-appropriate metrics per holding |
| **Research** | optional TradingAgents reports per holding (see below) |
| **Sync & cost** | sync prompts, backup and restore, token/cost ledger |

## How it works

```
Claude Code --(broker MCP)--> sync/inbox/*.json --(ingest)--> portfolio.db --> web UI
```

**Why Claude Code is in that path.** The broker's MCP server is reachable *only* from
inside a Claude Code session — the OAuth token lives in Claude Code's MCP client, not on
disk in a form a web server can read. A browser tab can never fetch broker data directly.
So every fetch starts in Claude Code, lands in `sync/inbox/` as JSON, and is ingested.

**Ingest is idempotent**, deduped on the broker's order id. Re-running a sync, a
bootstrap, or an overlapping fetch is always safe and never double-counts. When something
looks wrong, re-running is the correct fix.

## Requirements

- **macOS or Linux with `python3`.** Standard library only — no `pip install`, and no
  dependency on the `sqlite3` command-line tool (it is missing from many minimal Linux
  images).
- **[Claude Code](https://claude.com/claude-code)**, opened in this folder.
- **A Robinhood account.** Other brokers need `app/sync.py` adapted — see [SETUP.md](SETUP.md).
- *Optional:* **Google Drive for Desktop**, for backups.

---

## Setup

### 1. Connect your broker to Claude Code

```bash
claude mcp add --transport http robinhood https://agent.robinhood.com/mcp/trading
```

### 2. Restart Claude Code, then authenticate

Restart, then run `/mcp` → `robinhood` and complete the OAuth flow **in a desktop
browser**. Mobile browsers do not finish it.

The restart is not optional. Claude Code fetches a server's tool list when the session
connects, so a session started *before* you authenticated keeps the logged-out tool list
and the account tools appear to be missing. If that happens, restart.

Do not try to test the endpoint with `curl` — an unauthenticated request returns
`authentication required` regardless of whether you are logged in, so it tells you
nothing.

Robinhood's agent server grants **read access** to positions, balances, and full order
history. Trade placement is confined to a separate, deliberately funded account. Trans
only ever reads.

### 3. Clone and configure

```bash
git clone https://github.com/winnertechserv/trans.git && cd trans
cp config.example.json config.json
```

### 4. Find your account number

In Claude Code, say:

```
accounts
```

It calls `get_accounts` and shows your account numbers. Put the default one into
`config.json` under `broker.account_number` (and the same value in
`crypto_account_number`).

### 5. Check where you stand

```bash
./run.sh
```

With no data yet it prints your two options and exits. That is expected.

### 6. Build the database

In Claude Code, say:

```
bootstrap
```

This pulls your **entire** order history, positions and quotes, then ingests them.

Be patient here — this is the slow step. A multi-year account with ~10,000 orders takes
roughly 80 paginated API calls, and Claude Code has to verify the page ranges join up
with no gaps. It is a session, not a button press. You only do it once.

### 7. Classify your holdings

```
classify tickers
```

The Fundamentals tab picks metrics per sector, so it needs to know what each ticker is.
Unmapped tickers still work — they just get a generic metric set. This writes
`ticker_sectors` into `config.json`.

### 8. Pull fundamentals

```
sync fundamentals
```

Weekly from then on.

### 9. Run it

```bash
./run.sh
```

→ **http://127.0.0.1:8787** (bound to loopback; never exposed off your machine)

### 10. Optional — turn on backups

Install **Google Drive for Desktop** and sign in with a personal account. Approve the
macOS prompts: Privacy & Security → **Files and Folders**, and General → **Login Items
& Extensions** → enable the Drive *File Provider* extension (skipping this is the usual
reason the folder never appears).

Verify the mount exists:

```bash
ls -d ~/Library/CloudStorage/GoogleDrive-*/My\ Drive
```

Snapshots then go to `<your Drive>/trans` automatically — no further configuration.

---

## Daily use

In Claude Code, in this folder:

| Say | It does |
|---|---|
| `sync` | fetches orders, positions and quotes since your last sync, and ingests them |
| `sync fundamentals` | refreshes company metrics (weekly is plenty) |
| `backup` | writes a database snapshot to your Drive folder |
| `restore` | lists snapshots and restores the one you pick |
| `accounts` | shows your broker account numbers |
| `classify tickers` | maps unclassified holdings to sectors |
| `bootstrap` | rebuilds the database from scratch |

Or use the buttons in the **Sync & cost** tab. The page refreshes itself when new data
lands, so you do not need to reload.

That is the whole daily habit: **say `sync`.**

## Backups and restore

**Only the database is backed up** — one `.db` file per snapshot. Code lives in git,
never in Drive.

A backup happens when you press **Back up now**, or automatically whenever the newest
snapshot is more than **7 days old** (`backup.max_age_days`), checked at startup and
while the app is open. None of this costs AI tokens: the button posts to the local
Python server, which writes the file, and Drive's own daemon uploads it.

Restore from the **Backup** card, or:

```bash
./run.sh --restore
```

It lists snapshots newest first and asks which — it never auto-picks. Your current
database is preserved as `portfolio.db.pre-restore` first.

**Restoring rewinds the sync cursor.** After a restore, Trans recomputes how current the
data is and regenerates the sync prompts to match. Restore a snapshot from two weeks ago
and the next `sync` fetches exactly those two weeks — nothing refetched, nothing missed.

Depth — why snapshots use `VACUUM INTO` rather than `cp`, the manifest format, retention,
and why "backed up" means *queued to Drive* — is in **[BACKUP.md](BACKUP.md)**.

## Moving to another computer

```bash
# install Google Drive for Desktop and sign in first
git clone https://github.com/winnertechserv/trans.git && cd trans
cp config.example.json config.json     # edit: account number
./run.sh --restore                     # pick the newest snapshot
./run.sh
```

Then say `sync` to catch up on anything after that snapshot. Full walkthrough in
[SETUP.md](SETUP.md).

## Command reference

```bash
./run.sh                 # start the dashboard
./run.sh <port>          # start on a specific port
./run.sh --backup        # snapshot now
./run.sh --restore       # interactive restore
./run.sh --help

python3 app/backup.py snapshot|auto|list|restore <file>
python3 app/ingest.py  inbox|bootstrap
python3 app/classify_cli.py       # list unclassified holdings
python3 app/analysis.py           # research availability + cost estimate
python3 app/restore_cli.py        # same as ./run.sh --restore
python3 scripts/check_clean.py    # refuse-to-commit-personal-data check
python3 test_xirr.py              # 16 solver tests
```

### config.json

Gitignored. Holds every personal value in the project.

| Key | Meaning |
|---|---|
| `broker.account_number` | your account; find it with `accounts` |
| `broker.crypto_account_number` | usually the same value |
| `drive_backup_dir` | optional — auto-detected on macOS and Linux |
| `ticker_sectors` | your holdings → sector map, written by `classify tickers` |
| `backup.max_age_days` | staleness threshold for automatic backups (default 7) |
| `backup.keep_daily` / `keep_monthly` | retention (default 30 / 12) |
| `port` | default 8787 |
| `tradingagents` | optional research integration — see [SETUP-ANALYSIS.md](SETUP-ANALYSIS.md) |

## Research (optional)

Trans can run **[TradingAgents](https://github.com/TauricResearch/TradingAgents)** over a
holding — five analysts, a bull/bear debate, a trader and a risk panel — and store the
report in the Research tab.

It is off by default and installs nothing on your behalf. TradingAgents has no license
and needs 22 pip packages, so it is never bundled: you clone it yourself and Trans talks
to it by subprocess, staying stdlib-only. Pick a backend — **local Ollama** (free,
private, weaker) or the **Anthropic API** (paid, better, with a consent prompt and cost
logging before every run).

Output is third-party generated research, clearly attributed — **not a recommendation**,
and not checked by Trans.

Setup: **[SETUP-ANALYSIS.md](SETUP-ANALYSIS.md)**.

## Sector-aware fundamentals

One metric table for every company would be wrong, so `app/sectors.py` defines **19
sectors** across **38 metrics** and picks per holding:

- **P/E is omitted for REITs and MLPs** — depreciation and D&A distort earnings. They
  lead with P/FFO and distribution yield instead.
- **P/B leads for banks**, where book value is the meaningful anchor and P/E is noise.
- **P/NAV leads for BDCs**; price against net asset value is the whole question.
- **ETFs** get fund-level figures only — expense ratio and yield, not company metrics.

Edit `SECTOR_METRICS` in `app/sectors.py` to change what appears. Your ticker→sector map
lives in `config.json`, not in code, so the repo never discloses what you own.

## Project layout

| Path | Role |
|---|---|
| `app/db.py` | schema — transactions, positions, fundamentals, quotes, backups, ledgers |
| `app/ingest.py` | envelope JSON → SQLite, idempotent |
| `app/analytics.py` | XIRR, allocation, dividends, buy programme, concentration |
| `app/sectors.py` | sector → which metrics matter |
| `app/sync.py` | delta cursor + prompt generation |
| `app/backup.py` | snapshot, manifest, prune, restore |
| `app/config.py` | config loader; cross-platform Drive detection |
| `app/costs.py` | token and cost ledger |
| `app/server.py` | JSON API + static UI |
| `xirr.py` | the solver — stdlib, no dependencies |
| `portfolio.py` | transactions + positions → per-ticker results |
| `cli.py` | command-line report |
| `scripts/` | scheduled backup, pre-commit personal-data guard |

## Cost policy

- **`claude_code`** — work done in the Claude Code session you already pay for. Tokens
  are logged for visibility; there is no incremental charge. Sync, bootstrap, backup and
  restore all run on this path.
- **`claude_api`** — a direct Anthropic API call. Requires explicit consent before
  running and is logged with a dollar cost.

`pricing.json` ships with `"verified": false` and placeholder rates. Confirm them at
[anthropic.com/pricing](https://www.anthropic.com/pricing) before trusting any dollar
figure; the UI shows a warning banner until you do.

## Privacy

Everything stays on your machine. `.gitignore` keeps the database, raw broker JSON,
exported CSVs and `config.json` out of git — so the repo contains no account number, no
balances, no quantities, and not even the list of tickers you hold.

```bash
python3 scripts/check_clean.py    # run before committing
```

It fails loudly if personal data would be included, including a check for your holdings
leaking into committed code.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `./run.sh` says "No portfolio data found" | empty database — `bootstrap` or `./run.sh --restore` |
| `No config.json yet` | `cp config.example.json config.json` and set the account number |
| Broker tools missing in Claude Code | session predates the OAuth handshake — restart Claude Code |
| `Port 8787 is already in use` | another copy is running: `./run.sh 8788`, or `pkill -f 'app/server.py'` |
| "Backup folder does not exist" | Drive not signed in, or the File Provider extension is disabled |
| Backups succeed but Drive stays empty | Drive is paused, signed out, or out of quota — check the menu-bar icon |
| Fundamentals mostly "not loaded" | run `sync fundamentals` |
| Fundamentals metrics look generic | run `classify tickers` |
| Research tab says it is unavailable | it names the missing piece — see [SETUP-ANALYSIS.md](SETUP-ANALYSIS.md) |

## Library

`xirr.py` and `portfolio.py` are standalone and broker-agnostic — anything that can
produce a flat transaction list plus a holdings snapshot works.

```bash
python3 cli.py -t sample_all.csv -p sample_positions.csv
python3 test_xirr.py     # 16 cases incl. Microsoft's documented XIRR example
```

### Input schema

**Transactions** (CSV or JSON). Column names are matched loosely -
`ticker`/`symbol`, `date`/`executed_at`, `quantity`/`shares`, etc.

| column | needed | notes |
|---|---|---|
| `date` | yes | `YYYY-MM-DD`, ISO timestamps, or `MM/DD/YYYY` |
| `ticker` | yes | for buy/sell/dividend |
| `type` | yes | `buy`, `sell`, `dividend` (also `deposit`, `withdrawal`) |
| `quantity`, `price` | for trades | amount derived as qty x price -/+ fees |
| `amount` | for dividends | signed automatically from `type` |
| `fees` | no | added to cost on buys, netted off proceeds on sells |

**Positions** - `ticker`, `quantity`, `price` (current mark). Used to close
out open positions at market value.

### Method

XIRR solves for the annual rate `r` where

```
sum_i  CF_i / (1 + r) ** ((d_i - d_0) / 365)  =  0
```

Money out is negative (buys), money in is positive (sells, dividends, and
the current market value of what you still hold, dated `--as-of`).

365-day basis, matching Excel/Sheets XIRR. Newton-Raphson clamped to
`r > -1`, falling back to bracketed bisection when Newton diverges.

Two things this buys you:

- **No cost-basis method needed.** XIRR is purely cash-flow driven, so
  FIFO vs average cost never enters into it.
- **Reinvested dividends handle themselves.** A DRIP shows up as a
  dividend inflow plus a buy outflow, which nets correctly.

Overall XIRR aggregates every trade and dividend flow plus total current
market value - i.e. return on invested capital. It deliberately ignores
deposits and withdrawals, so idle cash does not drag the number. If you
want return on the whole account including cash drag instead, feed
`deposit`/`withdrawal` rows and compute against those.

Closed positions get no terminal flow and are marked `closed`.
A ticker bought only today has no valid XIRR; the tool says so in the
STATUS column rather than printing a fake number.
