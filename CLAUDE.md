# Trans — working notes for Claude Code

*Not documentation — this file is loaded automatically by Claude Code when it is opened in
this folder, and it is what makes the trigger words below actually work. It has to stay at
the repo root. Human-facing docs are in [docs/](docs/).*

**Trans** — transparency for your portfolio: a local dashboard over brokerage data. `./run.sh` → http://127.0.0.1:8787

Config lives in **`config.json`**, which is gitignored: account number, Drive folder,
and `ticker_sectors` (the holdings list). Read it via `app/config.py`. Never hardcode an
account number or a real holdings list in the repo — this code is shared.

## Trigger words

**"accounts"** — call `get_accounts` and show the account numbers (mask all but the
last 4 when displaying). Offer to write the default one into `config.json` under
`broker.account_number` and `broker.crypto_account_number`. This is usually the first
thing a new user needs.

**"classify tickers"** — run `python3 app/classify_cli.py` to list unmapped holdings and
the valid sector keys, decide the right sector for each (P/B leads for banks, P/FFO for
REITs, distribution yield for MLPs, P/NAV for BDCs, and so on), and write them into
`config.json` under `ticker_sectors`. Never put a real holdings list in a committed file.

**"sync" / "update the data"** — follow `sync/prompts/daily.txt` verbatim. It is
regenerated on every Sync-tab load and already carries the correct delta cursor
(`created_at_gte`). It ends by running `python3 app/ingest.py inbox`. Report rows added
per kind and the new data-through date. Do not ask for confirmation first.

**"sync fundamentals" / "refresh metrics"** — same, with `sync/prompts/fundamentals.txt`.

**"bootstrap"** — a full-history build for an empty database. Follow
`sync/prompts/bootstrap.txt`. Paginate to exhaustion; the cursor is base64 of
`p=<ISO timestamp>` so you may forge cursors at date boundaries and fetch in parallel,
but you MUST merge the page ranges afterwards and verify coverage is contiguous before
reporting success. Gaps are silent data loss.

**"analyse <TICKER>" / "research <TICKER>"** — optional TradingAgents integration.
Check `python3 app/analysis.py` first; if it is not ready, show the reason and stop.
On the **anthropic** backend the run costs money: show the estimate and get explicit
confirmation before starting, then POST /api/analysis/run with `consented: true`.
On **ollama** it is free — just start it. Runs take minutes; poll
GET /api/analysis/status rather than blocking. Report the decision line and where the
report lives. Always present the result as **third-party generated research, not a
recommendation**, and never restate it as your own investment advice.

**"refresh navs" / "update nav"** — `python3 app/navs.py`. Fetches AMFI's daily NAV file
(public, no key, nothing personal sent) and marks every mutual fund position. Zerodha
funds match on ISIN; Paytm funds carry none, so the scheme name must reduce to exactly one
Direct/Growth scheme or it is skipped and reported. Report which funds moved and by how
much. `--dry-run` writes nothing.

**"backup"** — `python3 app/backup.py snapshot manual` (or POST /api/backup). Only the
database file is copied; code lives in git, not Drive. Snapshots go to
`<drive root>/trans`, auto-detected on macOS and Linux. Report the filename and
transaction count, and say it is *queued* to Drive, not uploaded — Drive syncs
asynchronously and the app cannot see whether the upload finished.
`python3 app/backup.py auto` applies the staleness rule instead (backs up only if the
newest snapshot is older than `backup.max_age_days`, default 7).

**"restore"** — `python3 app/restore_cli.py`, or POST /api/restore from the web app.
Always list options and confirm before overwriting; never auto-pick a snapshot.
After restoring, the sync cursor rewinds automatically to the restored data's last
transaction date and the prompts are regenerated — so the natural follow-up is to tell
the user to say `sync`, which will fetch exactly the gap.

`sync/prompts/*.txt` are generated and gitignored, so a fresh clone has none. `./run.sh`
regenerates them on every start; if you need them sooner, run `python3 app/sync.py`.

## Rules

- Ingest is idempotent (deduped on broker `order_id`) — overlapping fetches are safe.
  Never dedupe by hand; re-running is always the correct fix.
- Never call the Anthropic API for a sync, a backup, or a restore. All of it is free at
  point of use. Log runs to `token_ledger` with `source='claude_code'`.
- Before any commit, run `python3 scripts/check_clean.py`. It refuses if personal data
  (database, raw broker JSON, exported CSVs, account numbers) would be committed.
- If the broker MCP tools are not in the tool list, the session predates the OAuth
  handshake. Tell the user to restart Claude Code. Do not curl the endpoint — it returns
  `authentication required` for any unauthenticated call and proves nothing.

## Data notes that are easy to get wrong

- **Dividends are reconstructed from DRIP orders** (`placed_agent='drip'`), recorded as a
  dividend inflow *plus* the reinvestment buy. There is no dividend endpoint on the MCP.
  This captures reinvested dividends only; cash dividends taken as cash are invisible.
- **Stock splits and ADR ratio changes**: order history holds pre-split quantities, so
  derived share counts will not match the broker's current position for any ticker that
  split. This does **not** affect XIRR — it is cash-flow driven and terminal value uses
  the broker's current share count. Do not "correct" historical quantities; reconcile
  and explain the difference instead.
- **Period alignment**: when deriving a margin, the numerator and denominator must cover
  the same period. Filing-year operating income over TTM revenue is wrong. Pull revenue
  from the same filing.
- **Zerodha symbols need normalising on both sides.** Holdings carry the NSE series code
  (`MTARTECH-BE`) and the tradebook does not (`MTARTECH`); renamed companies keep the old
  symbol on old trades (`GET&D` -> `GVT&D`). Everything goes through
  `markets.canonical_symbol()`. If a holding shows no XIRR, suspect a rename first and
  check whether an old symbol's net quantity completes it — add confirmed renames to
  `markets.RENAMES`, or per-user ones to `ticker_aliases` in `config.json`.
- **Tradebook re-uploads are safe.** Dedupe is `INSERT OR IGNORE` on
  `zt:<trade_id>:<date>:<raw symbol>` — the *raw* symbol from the CSV, never the
  normalised one, so re-uploading the same or overlapping exports adds nothing even if
  `RENAMES`/`ticker_aliases` changed in between. The flip side: a rename added later does
  not reach rows already stored, because the whole row is skipped. Run
  `python3 app/ingest.py remap` for that — re-uploading will not do it.
- **Paytm Money is statements only.** No API, no MCP. Per-financial-year PDFs go in
  `sync/inbox`; `app/paytm.py` parses the text (`pdftotext -layout`, poppler — a system
  tool, not a pip dependency) and the password comes from `PAYTM_PDF_PASSWORD`, never
  config. Each statement is checked against its own Fresh Purchase and Withdrawal totals
  and skipped whole if it does not add up. No ISIN and no trade id: holdings key on folio
  plus a hash of the scheme name, because one folio can hold several schemes and one
  scheme is spelled several ways across years. Paytm reports no holdings or live NAV, so
  positions are derived from the transactions and marked at the last transacted NAV, with
  `asof` showing how stale that is.
- **Demerged shares carry no cost basis** (`markets.DEMERGERS`, extendable via
  `demergers` in config.json). A demerger should apportion the parent's basis between
  parent and children; Zerodha instead gives the children their own average and leaves
  the parent's untouched, counting the same money twice — VEDL's 120 shares cost 34,680
  and the four Vedanta entities carried a further 16,528 nobody paid. Treating the
  children as free puts the whole cost on the parent. Do NOT extend this to SGBs, bonds
  or merger remnants: those were bought with real money that simply is not in the equity
  tradebook.
- **India spans two brokers.** `markets.brokers_of()` returns them; reads use
  `broker IN (...)`. `broker_of()` is still the primary, for sync prompts.
- **Zerodha mutual funds need `get_mf_holdings`.** They sit outside the demat, so
  `get_holdings()` does not return them and the tradebook does not contain them — they
  were invisible until that call was added. Keyed on the ISIN Kite reports as
  `tradingsymbol`, with the fund name stored as a `name` metric. Holdings only, so no
  XIRR. Kite has no MF order history endpoint.
- **The Zerodha tradebook is the EQ segment only.** Bonds and SGBs bought in the primary
  market, and shares received from a demerger, have no purchase row and never will. They
  fall back to the broker's average cost for a basis and honestly report no XIRR. This is
  correct, not a gap to fill.
- Only rows with empty `axises` in SEC facts are consolidated totals; the rest are
  segment breakdowns.
- `pricing.json` starts unverified — do not present its dollar figures as authoritative.
