# Importing your data

Trans reads from three brokers by two different routes. Which route you get is decided by
the broker, not by preference: some expose an API that Claude Code can call, and some only
let you download a file.

| Broker | Market | Route | What it gives | What it cannot give |
|---|---|---|---|---|
| **Robinhood** | US | MCP (live) | Full order history, positions, fundamentals | Cash dividends taken as cash |
| **Zerodha (Kite)** | India | MCP (live) + file | Holdings, mutual funds, today's trades — plus full history from a tradebook export | Dividends; order history older than today |
| **Paytm Money** | India | file only | Mutual fund transactions, full history | Current NAV; anything about stocks |

Everything lands in the same two tables — `transactions` and `positions` — so once
imported there is no difference in how it is analysed.

---

## The inbox

Every file import works the same way: **drop the file in `sync/inbox/` and run**

```bash
python3 app/ingest.py inbox
```

Ingest picks files up by name, loads them, and moves them to `sync/archive/`. Both folders
are gitignored, because the files carry account codes and, in Paytm's case, your PAN and
address.

**Re-importing is always safe.** Every importer deduplicates, so overlapping exports, the
same file twice, or a full re-export after a gap all add only what is genuinely new. When
something looks wrong, re-running the import is the correct fix — never edit the database
by hand.

---

## Robinhood (US) — live, no files

Connect the MCP once, then say `sync` in Claude Code. There is nothing to download.

```
claude mcp add --transport http robinhood https://agent.robinhood.com/mcp/trading
```

Restart Claude Code afterwards and run `/mcp` to authenticate. The tool list is fetched
when the session starts, so a session opened before the handshake will not see the tools
however many times you retry.

**Dividends are reconstructed from DRIP orders.** There is no dividend endpoint, so a
reinvestment is recorded as a dividend inflow plus the reinvestment buy. Dividends you
took as cash are invisible — not missing from the sync, but never visible to it.

---

## Zerodha (India)

### Live: holdings and mutual funds

```
claude mcp add --transport http kite https://mcp.kite.trade/mcp
```

Then `sync`. This fetches demat holdings, **mutual fund holdings**, today's trades and any
intraday positions. Mutual funds sit outside the demat and are returned by a separate call
(`get_mf_holdings`), so a sync that skips it loses them entirely.

Kite serves **one day** of order history. Everything before today has to come from a file.

### File: equity tradebook

Console → Reports → Tradebook → download as CSV, one file per financial year.

```
sync/inbox/tradebook-XXXXXX-EQ.csv
sync/inbox/tradebook-XXXXXX-EQ (1).csv     ← as many years as you have
```

This is the **EQ segment only**. Bonds and Sovereign Gold Bonds bought in the primary
market never appear in it, and neither do shares received from a demerger. Those fall back
to the broker's average cost and honestly report no XIRR — correct, not a gap to fill.

### File: mutual fund tradebook

Console → Reports → Tradebook → **Mutual funds** → CSV. Same shape as the equity export,
with `segment` set to `MF` and the ISIN in its own column.

```
sync/inbox/tradebook-XXXXXX-MF.csv
```

Import these even though the MCP already reports your fund holdings: the API gives units
and average cost, the tradebook gives dated cash flows, and only the second produces an
XIRR or shows funds you have fully exited.

---

## Paytm Money (India) — mutual funds, files only

Paytm has no API we can use. The route is the per-financial-year transaction statement.

1. Paytm Money → Reports → Transactions → pick a financial year → download.
2. Repeat for every year you have.
3. Drop them all in `sync/inbox/`.
4. Export the PDF password to the environment and import:

```bash
export PAYTM_PDF_PASSWORD='<your PDF password>'
python3 app/ingest.py inbox
```

The statements are AES-256 encrypted and usually open with your PAN. The password is read
from the environment and never written to `config.json`, for the same reason the Anthropic
key is not: a secret in a JSON file gets copied, backed up and pasted into chats.

**Prerequisite:** `pdftotext`, from poppler. It is a system tool, not a pip package, which
is how Trans stays installable with nothing but Python.

```bash
brew install poppler          # macOS
sudo apt install poppler-utils  # Debian/Ubuntu
```

If it is missing, the import says so and skips the file rather than failing silently. You
can also convert the statements yourself and drop the `.txt` files in instead.

**Every statement is checked against its own totals.** Each carries a Fresh Purchase and a
Withdrawal figure, and a file whose parsed rows do not reconcile is skipped whole rather
than imported partially — a missing purchase silently inflates realised profit, which is
worse than a missing year you can see.

**What Paytm cannot give you:** there is no current NAV anywhere in the statement, so fund
positions are marked at the NAV of their most recent transaction, with the date shown. For
anything on a monthly SIP that is days old. For a fund left alone for years, the staleness
is visible rather than hidden.

---

## Indian data: five things that are genuinely different

These are not quirks of Trans. They are properties of Indian brokerage data that any
honest tool has to deal with, and each one produced a wrong number before it was handled.

### 1. Symbols need normalising on both sides

Holdings carry the NSE series code and the tradebook does not — `MTARTECH-BE` against
`MTARTECH` — so the two never met and a holding with eighteen trades showed no history.
Everything now goes through `markets.canonical_symbol()`.

### 2. Renamed companies keep the old symbol on old trades

GE T&D India became GE Vernova T&D India, so the buys sit under `GET&D` and the sells
under `GVT&D`: one position split in two, each half with an incomplete cost basis. Eight
such renames ship in `markets.RENAMES`. Add your own to `config.json`:

```json
"ticker_aliases": { "OLDSYMBOL": "NEWSYMBOL" }
```

then run `python3 app/ingest.py remap`. Re-importing the CSV will not do it — the dedupe
key is the raw symbol precisely so re-imports stay no-ops, which means a rename added
later never reaches rows already stored.

**How to spot one:** a holding showing sales with zero invested, or an orphan symbol whose
leftover share count exactly equals what another symbol sold without ever buying. Check
the name and the dates too. Matching on quantity alone will happily pair unrelated
companies that happen to be a few shares apart.

### 3. Splits leave pre-split quantities in the order history

Order history keeps the share counts as they were, while sales are post-split, so derived
quantities drift and some tickers appear to sell more than they ever bought. This does
**not** affect XIRR, which is cash-flow driven and takes the terminal value from the
broker's current share count. It does mean derived share counts are marked unreliable —
`Held` shows `~` and the elapsed span instead of true holding days.

### 4. Demerged shares arrive with no cost

A demerger should split the parent's cost basis between parent and children. Zerodha
instead gives the children their own average and leaves the parent's untouched, counting
the same money twice. Trans puts the whole cost on the parent and treats the children as
free, so their entire value counts as gain. They carry a **no cost** chip saying so.

```json
"demergers": { "CHILD": "PARENT" }
```

Do **not** extend this to SGBs, bonds or merger remnants. Those were bought with real
money that simply is not in the equity tradebook.

### 5. Fund NAVs go stale, and nothing refreshes them by itself

Prices are not live. `positions.price` is whatever the broker reported at the last sync,
and there is no scheduled job. Zerodha reports a NAV for funds you still hold there;
**Paytm reports none at all**, so those are marked at the NAV of their most recent
transaction and drift a little further out of date every week.

```bash
python3 app/navs.py            # fetch AMFI's daily NAV file and mark every fund
python3 app/navs.py --dry-run  # show what would change, write nothing
```

AMFI publishes every Indian scheme's NAV as one public text file — no key, no account,
nothing personal sent. Funds carrying an ISIN match on it. Paytm funds have none, so the
scheme name must reduce to exactly one Direct/Growth scheme after filler words are
dropped; anything ambiguous is skipped and reported rather than guessed at, because a NAV
attached to the wrong fund is worse than a stale one — a stale price is visibly stale and
a wrong one is not. Resolved ISINs are stored so the match is made once and can be
audited.

The header shows how old the marks are alongside the transaction date, and warns past a
week. The two ages are unrelated: a trade this morning tells you nothing about whether
prices were refreshed.

**Equities are not covered.** AMFI is funds only; stock prices still come from the broker
at sync time.

### 6. Dividends are invisible on the India side

Indian equities pay cash into your bank account. There is no reinvestment order to infer
from, the way DRIP works on the US side, and Kite exposes no dividend endpoint. India
therefore shows **₹0 dividends** — they were never visible to the sync rather than lost by
it. Zerodha Console can export a dividend statement; there is no importer for it yet.

---

## Checking an import worked

```bash
python3 app/ingest.py inbox     # prints rows added per file
./run.sh                        # then read the header
```

The overview banners any mutual fund holding more units than its imported order history
accounts for, which is what a missing financial year looks like. Beyond that, the numbers
that should reconcile are:

- **Realized + Unrealized + Dividends = Net gain/loss**, exactly.
- **Total invested − Total sold ≈ money you actually put in**, with the difference being
  profit you re-deployed rather than withdrew.

If a fund is short units, export the missing year and drop it in. Re-importing everything
is safe.

---

## Adding a broker

`app/ingest.py` holds one function per source and a `HANDLERS` map from envelope kind to
handler. A new file-based broker needs a parser and a handler; a new market needs an entry
in `markets.MARKETS`, where `brokers` is a list because a country is not one broker —
India is Zerodha and Paytm together.

Reads span every broker in a market (`markets.brokers_of()`); sync prompts target one
(`markets.broker_of()`), since each broker is fetched its own way.
