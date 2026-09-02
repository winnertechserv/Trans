# Trans — application internals

Zero dependencies — stdlib `http.server` + `sqlite3`. Start with `./run.sh`
then open http://127.0.0.1:8787 (bound to loopback only).

## Why sync works the way it does

The Robinhood MCP server is reachable **only from inside a Claude Code session** —
the OAuth token lives in Claude Code's MCP client. A browser tab cannot call it.
So every broker fetch originates in Claude Code and lands in `sync/inbox/`:

    Claude Code --(MCP)--> sync/inbox/*.json --(ingest)--> portfolio.db --> web UI

The app generates the exact prompt; you copy one line, or just say "sync" in an
open session. No data is ever copied by hand.

Envelope format:

    {"kind":"orders_equity|orders_crypto|positions|quotes|fundamentals",
     "fetched_at":"<ISO>","data":[ ... ]}

Ingest is **idempotent** — deduped on broker `order_id`, so overlapping fetches
are safe. Re-running a full bootstrap adds 0 rows.

## Layout

| file | role |
|---|---|
| `db.py`        | schema (transactions, positions, fundamentals, quotes, token_ledger, sync_runs) |
| `ingest.py`    | envelope -> SQLite, idempotent. `bootstrap` \| `inbox` |
| `analytics.py` | XIRR, allocation, dividends, buy programme, concentration |
| `sectors.py`   | ticker -> sector -> which metrics actually matter |
| `sync.py`      | delta cursor + prompt generation |
| `costs.py`     | token/cost ledger, pre-flight estimates |
| `backup.py`    | VACUUM INTO snapshot, manifest, prune, restore |
| `config.py`    | config.json loader; no hardcoded account numbers |
| `restore_cli.py` | interactive restore, never auto-picks |
| `server.py`    | JSON API + static UI |

## Sector-aware metrics

`sectors.py` is the opinionated part. P/E is omitted for REITs and MLPs (depreciation
and D&A distort earnings), P/B leads for banks, P/NAV leads for BDCs, and ETFs get
fund-level figures only. Editing `SECTOR_METRICS` changes what the UI shows.

## Cost policy

- `claude_code` — work done in the session you already pay for. Tokens logged for
  visibility, `cost_usd = 0` (no incremental charge).
- `claude_api`  — direct API call. Requires consent **before** running; logged with
  a dollar cost.

`pricing.json` starts with `"verified": false` and placeholder rates. The UI shows a
warning banner until you confirm rates at anthropic.com/pricing and set it true.

## Daily use

1. Open the app -> **Sync & cost** -> copy the daily prompt into Claude Code.
2. Claude Code writes to `sync/inbox/` and runs `python3 app/ingest.py inbox`.
3. Refresh. (Or press **Ingest inbox now** if files are already waiting.)

Weekly-ish, run the **Fundamentals** prompt to refresh company metrics.

## Endpoints

| Method | Path | Does |
|---|---|---|
| GET | `/api/overview` `/api/results` `/api/allocation` `/api/dividends` `/api/daily-buys` `/api/buy-program` `/api/contributions` `/api/fundamentals/<t>` `/api/health` `/api/costs` `/api/sectors` | read models |
| GET | `/api/backups` | backup status + list (manifest, mtime fallback) |
| GET | `/api/sync/prompt?kind=daily\|fundamentals\|bootstrap` | generated prompt + delta cursor |
| POST | `/api/sync/ingest` | ingest `sync/inbox/*.json` |
| POST | `/api/backup` | snapshot -> Drive folder, prune, log |
| POST | `/api/restore` | restore `{file}` (refuses while serving) |
| POST | `/api/costs/estimate` `/api/costs/log` | pre-flight estimate; ledger entry |

See `../BACKUP.md` for why backups use `VACUUM INTO` rather than `cp`.
