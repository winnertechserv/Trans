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
| **Overview** | eleven headline figures, searchable allocation with category filter, top movers |
| **Holdings** | every position, filterable by status and asset; click a flow count to open every trade |
| **Daily buys** | your recurring programme: per ticker, per day, share of new money |
| **Dividends** | lifetime total, by year, by ticker |
| **Analysis** | sector exposure, concentration, the sub-1% tail, capital deployed by month/quarter/year |
| **Fundamentals** | three-way compare, or every stock against every metric in one grid |
| **Research** | optional TradingAgents reports per holding (see below) |
| **Sync & cost** | sync prompts, backup and restore, token/cost ledger |

Every filter, sort and search you set is remembered in the browser, so a screen comes back
the way you left it.

### The header

Eleven figures, each with a tooltip saying how it is derived:

**Current value · Currently invested · Unrealized gain · Total invested · Total sold ·
Realized gain/loss · Dividends · Unrealized XIRR · Realized XIRR · Net XIRR · XIRR 1Y**

Money still in and money already out are kept apart, because a single "invested" figure
reads as a collapse the moment you have sold anything. `Realized + Unrealized + Dividends`
reconciles to net profit exactly, which is the check that nothing is double-counted.

The four XIRRs are four slices of the same cash flows — still held, fully sold, everything,
and positions opened in the last year. Each is a real XIRR over its own subset, never a
blend, so they do not add up to each other and are not meant to.

### Analyses you can run

- **XIRR** per holding and per portfolio, money-weighted, on a 365-day basis
- **Realised versus unrealised**, split by cost basis rather than guessed
- **Booked profit per month, quarter or year**, over 1/3/5/10 years or all time, filtered
  by asset type
- **Every trade behind a position**, with each sale matched FIFO to the buys it sold —
  dates, quantities, days held, and the gain on each lot
- **Allocation and concentration**, top 1/3/5/10 and the sub-1% tail
- **Fundamentals**, sector-appropriate, one stock, three side by side, or all at once
- **Re-entry detection** — positions sold out and bought back, where XIRR is set mostly by
  the first episode
- **Optional third-party research** through TradingAgents

## Two markets, kept separate

Trans supports a US broker (Robinhood, USD) and an Indian one (Zerodha, NSE/BSE, INR).
A toggle in the header switches between them and every tab follows.

**They are never combined.** There is no "All" view, no conversion and no blended
return. Summing two currencies needs an FX rate, and currency movement would then sit
inside your reported returns — real, but not stock performance. Each market is shown in
its own currency, with its own XIRR, formatted for its own locale (₹41,36,165 the Indian
way, $108,664 the American way).

Every view has its own URL, so it can be bookmarked and shared, and back/forward work:

| URL | View |
|---|---|
| `/us` · `/in` | Overview for that market |
| `/in/holdings` `/us/dividends` `/in/analysis` … | the matching tab |
| `/us/fundamentals/MSFT` | Fundamentals on one holding |
| `/us/research/MSFT` | Research with a report open |

Refreshing a deep link restores that exact view. Unknown paths fall back to Overview.

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

**Required**

- **macOS or Linux with `python3`.** Standard library only — no `pip install`, and no
  dependency on the `sqlite3` command-line tool (it is missing from many minimal Linux
  images). Verified from a clean checkout: every module imports, and every API endpoint
  and deep-link route answers with an empty database and no config file present.
- **[Claude Code](https://claude.com/claude-code)**, opened in this folder. It is what
  talks to your broker; there are no API keys to manage for syncing.
- **At least one supported broker account** — see the table below.

**Optional, per feature**

| For | You need |
|---|---|
| Paytm Money statements | `pdftotext` (poppler): `brew install poppler` / `apt install poppler-utils` |
| Backups | Google Drive for Desktop, signed in |
| Research tab | a TradingAgents checkout and either Ollama or an Anthropic API key |
| Plain-English report rewrite | `ANTHROPIC_API_KEY` (costs about $0.02 a time, with consent) |

Nothing above is needed to run the dashboard or to import a file.

## Brokers supported

| Broker | Market | How | History |
|---|---|---|---|
| **Robinhood** | US | MCP, live | full |
| **Zerodha (Kite)** | India | MCP for holdings and funds, CSV for history | full, with a tradebook export |
| **Paytm Money** | India | statement files only | full, one PDF per financial year |

A market can span several brokers — India is Zerodha and Paytm together — and the two
markets are never blended, because summing them would need an FX rate and currency
movement would then land inside reported returns.

**[IMPORTING.md](IMPORTING.md) is the full guide**: which file to export from where, how
to import it, and the five ways Indian data genuinely differs — series codes, renames,
splits, demergers, and why India shows no dividends.

---

## Setup

### 1. Connect your broker to Claude Code

Add whichever you have. Both can be connected at once.

```bash
claude mcp add --transport http robinhood https://agent.robinhood.com/mcp/trading
claude mcp add --transport http kite     https://mcp.kite.trade/mcp
```

Paytm Money has no MCP — it is statements only, and needs no setup here. See
[IMPORTING.md](IMPORTING.md).

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

To import files instead, drop them in `sync/inbox/` and run `python3 app/ingest.py inbox`.
Re-importing is always safe — every importer deduplicates, so overlapping exports and
repeated files add only what is new.

| File | From |
|---|---|
| `tradebook-*-EQ.csv` | Zerodha Console → Reports → Tradebook |
| `tradebook-*-MF.csv` | Zerodha Console → Reports → Tradebook → Mutual funds |
| `Transactions_*.pdf` | Paytm Money → Reports → Transactions (needs `PAYTM_PDF_PASSWORD`) |

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

It is off by default and installs nothing on your behalf. TradingAgents needs 22 pip
packages, so it is never bundled — you clone it yourself and Trans talks to it by
subprocess, staying stdlib-only. Pick a backend — **local Ollama** (free,
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
| `app/ingest.py` | envelope JSON, Zerodha CSVs and Paytm statements → SQLite, idempotent |
| `app/paytm.py` | Paytm statement parser, checked against each statement's own totals |
| `app/markets.py` | markets, brokers per market, symbol normalising, renames, demergers |
| `app/analytics.py` | XIRR (four scopes), cost basis, realised/unrealised, FIFO trade log |
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

| Doc | Covers |
|---|---|
| [IMPORTING.md](IMPORTING.md) | brokers, file formats, and how Indian data differs |
| [SETUP.md](SETUP.md) | moving to another machine, broker connection details |
| [BACKUP.md](BACKUP.md) | snapshots, Drive, restore, scheduling |
| [SETUP-ANALYSIS.md](SETUP-ANALYSIS.md) | the optional Research tab |
| [CLAUDE.md](CLAUDE.md) | how Claude Code should operate this repo |

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
