"""Sync orchestration.

ARCHITECTURAL CONSTRAINT (why it works this way):
The Robinhood MCP server is reachable only from inside a Claude Code session — the
OAuth token lives in Claude Code's MCP client, not on disk in a form this app can
use. So the web app CANNOT call Robinhood itself. Every broker fetch must originate
in a Claude Code session.

The app therefore emits a precise, copy-pasteable prompt; Claude Code runs it and
writes envelope JSON into sync/inbox/; the app ingests on the next poll or button
press. No manual copying of data — only the one-line prompt is copied, and even that
is skippable if you just say "sync" in an open session.
"""
import os, sys, json, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as D, config as CFG

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX = os.path.join(ROOT, "sync", "inbox")
# account number comes from config.json (gitignored) — never hardcode it here
def ACCOUNT(): return CFG.account_number()

ENVELOPE_DOC = """Envelope format written to sync/inbox/<name>.json:
  {"kind": "<orders_equity|orders_crypto|positions|fundamentals|quotes>",
   "fetched_at": "<ISO8601>",
   "data": [ ...raw MCP records, or {ticker,metric,value} for fundamentals... ]}
Records are deduped on order_id, so overlapping fetches are safe."""

def cursor(c):
    """Latest data we already hold — the delta boundary."""
    eq = c.execute("SELECT MAX(date) d FROM transactions WHERE asset='equity'").fetchone()["d"]
    cr = c.execute("SELECT MAX(date) d FROM transactions WHERE asset='crypto'").fetchone()["d"]
    return {"equity_through": eq, "crypto_through": cr,
            "positions_asof": c.execute("SELECT MAX(asof) a FROM positions").fetchone()["a"]}

def _crypto_pairs(c):
    rows = c.execute("SELECT ticker FROM positions WHERE asset='crypto' ORDER BY ticker").fetchall()
    return "[" + ", ".join(f'"{r["ticker"]}-USD"' for r in rows) + "]" if rows else "[]"

def daily_prompt(c):
    cur = cursor(c)
    crypto_pairs = _crypto_pairs(c)
    eq_from = cur["equity_through"] or "2022-01-01"
    cr_from = cur["crypto_through"] or "2022-01-01"
    return f"""Sync my Robinhood portfolio into the local app. Do all of this, then stop.

1. get_equity_orders(account_number="{ACCOUNT()}", state="filled", created_at_gte="{eq_from}")
   — paginate until exhausted (follow the `next` cursor).
2. get_crypto_orders(rhs_account_number="{ACCOUNT()}", state="filled", created_at_gte="{cr_from}")
   — paginate until exhausted.
3. get_equity_positions(account_number="{ACCOUNT()}") and
   get_crypto_positions(rhs_account_number="{ACCOUNT()}").
4. get_equity_quotes for every held equity ticker (<=16 per call) and
   get_crypto_quotes({crypto_pairs}) for the crypto held.

Write each result to {INBOX}/ as its own file using this envelope:
{{"kind":"orders_equity"|"orders_crypto"|"positions"|"quotes","fetched_at":"<ISO>","data":[...]}}
 - orders_*: the raw order objects exactly as returned.
 - positions: [{{"ticker","quantity","price"}}] with price from the quote (mark for crypto).
 - quotes:    [{{"ticker","price","prev_close"}}].

Then run: python3 {ROOT}/app/ingest.py inbox
Report rows added per kind. Do not call the Anthropic API for any of this."""

def fundamentals_prompt(c, tickers=None):
    import sectors as S
    if tickers is None:
        tickers = [r["ticker"] for r in c.execute(
            "SELECT ticker FROM positions WHERE asset='equity' ORDER BY ticker")]
    lines, fund_l, crypto_l = [], [], []
    for t in tickers:
        sec = S.sector_of(t)
        if sec == "crypto":
            crypto_l.append(t); continue
        if sec.endswith("_etf"):
            fund_l.append(t)
        ms = S.metrics_for(t)
        lines.append(f"  {t:6} ({S.label(sec)}): {', '.join(ms[:8])}")
    funds = "/".join(fund_l)
    crypto = "/".join(crypto_l)
    return f"""Refresh fundamentals for my holdings. Sector-specific metric sets below.

Pull with (all free, no Anthropic API):
  - get_equity_fundamentals(symbols=[...])  -> pe, pb, market_cap, dividend_yield, high_52, low_52
  - get_financials(symbols=[...], period="quarterly", limit=8)
        -> revenue, net_income, gross_margin, net_margin, revenue_growth (YoY from the series)
  - get_sec_filing_index + get_sec_filing_facts for balance-sheet concepts where needed:
        Assets, Liabilities, StockholdersEquity, LongTermDebtNoncurrent,
        CashAndCashEquivalentsAtCarryingValue, InventoryNet,
        ResearchAndDevelopmentExpense, PaymentsToAcquirePropertyPlantAndEquipment,
        NetCashProvidedByUsedInOperatingActivities, OperatingIncomeLoss
     Derive: debt_to_equity, current_ratio, roe, roa, capex_to_revenue, rnd_to_revenue,
             inventory_days, fcf_margin, operating_margin, book_value_per_share.

Metrics that matter per ticker:
{chr(10).join(lines)}

For funds ({funds or "none held"}) skip company metrics — record only expense_ratio and
dividend_yield if available. Crypto is out of scope for this prompt.

Write to {INBOX}/fundamentals.json as:
{{"kind":"fundamentals","fetched_at":"<ISO>",
  "data":[{{"ticker":"<TICKER>","metric":"gross_margin","value":0.73}}, ...]}}
Values as plain numbers; ratios as decimals (0.73 not "73%").

Then run: python3 {ROOT}/app/ingest.py inbox"""

def bootstrap_prompt(c):
    """Full-history pull for a brand-new database."""
    crypto_pairs = _crypto_pairs(c) or "[]"
    return f"""Build my portfolio database from scratch. Pull EVERYTHING, then stop.

1. get_accounts() to confirm the account number (expected {ACCOUNT()}).
2. get_equity_orders(account_number="{ACCOUNT()}", state="filled") — walk the FULL
   history. Follow the `next` cursor to exhaustion. The cursor is base64 of
   `p=<ISO timestamp>`, so you may forge cursors at date boundaries and fetch pages in
   parallel; afterwards MERGE page ranges and verify coverage is contiguous with no
   gaps before continuing. Orders dedupe on id, so overlapping fetches are safe.
3. get_crypto_orders(rhs_account_number="{ACCOUNT()}", state="filled") — same, to exhaustion.
4. get_equity_positions + get_crypto_positions.
5. get_equity_quotes for every held equity ticker (<=16 per call) and
   get_crypto_quotes([...]) for whatever crypto the account holds.

Write each result into {INBOX}/ as its own envelope file:
{{"kind":"orders_equity"|"orders_crypto"|"positions"|"quotes","fetched_at":"<ISO>","data":[...]}}
 - orders_*: raw order objects exactly as returned.
 - positions: [{{"ticker","quantity","price"}}] (price = current mark).
 - quotes:    [{{"ticker","price","prev_close"}}].

Then run: python3 {ROOT}/app/ingest.py inbox

Finally report: total orders, date range, and whether coverage was contiguous.
Do not call the Anthropic API for any of this."""

def write_prompt_files(c):
    d = os.path.join(ROOT, "sync", "prompts"); os.makedirs(d, exist_ok=True)
    out = {"daily": daily_prompt(c), "fundamentals": fundamentals_prompt(c),
           "bootstrap": bootstrap_prompt(c)}
    for k, v in out.items():
        open(os.path.join(d, f"{k}.txt"), "w").write(v)
    return out

if __name__ == "__main__":
    c = D.connect()
    print("cursor:", json.dumps(cursor(c)))
    p = write_prompt_files(c)
    print("\n--- daily prompt ---\n" + p["daily"][:600] + "\n...")
