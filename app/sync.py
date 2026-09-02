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
import db as D, config as CFG, markets as MK

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX = os.path.join(ROOT, "sync", "inbox")
# account number comes from config.json (gitignored) — never hardcode it here
def ACCOUNT(): return CFG.account_number()

ENVELOPE_DOC = """Envelope format written to sync/inbox/<name>.json:
  {"kind": "<orders_equity|orders_crypto|positions|fundamentals|quotes>",
   "fetched_at": "<ISO8601>",
   "data": [ ...raw MCP records, or {ticker,metric,value} for fundamentals... ]}
Records are deduped on order_id, so overlapping fetches are safe."""

def cursor(c, market=None):
    """Latest data we already hold for one market — the delta boundary."""
    br = MK.broker_of(market or MK.DEFAULT)
    eq = c.execute("SELECT MAX(date) d FROM transactions WHERE asset='equity' AND broker=?",
                   (br,)).fetchone()["d"]
    cr = c.execute("SELECT MAX(date) d FROM transactions WHERE asset='crypto' AND broker=?",
                   (br,)).fetchone()["d"]
    return {"market": market or MK.DEFAULT, "broker": br,
            "equity_through": eq, "crypto_through": cr,
            "positions_asof": c.execute("SELECT MAX(asof) a FROM positions WHERE broker=?",
                                        (br,)).fetchone()["a"]}

def _crypto_pairs(c):
    rows = c.execute("SELECT ticker FROM positions WHERE asset='crypto' AND broker='robinhood'"
                     " ORDER BY ticker").fetchall()
    return "[" + ", ".join(f'"{r["ticker"]}-USD"' for r in rows) + "]" if rows else "[]"

def _holdings(c, market):
    br = MK.broker_of(market)
    return [(r["ticker"], r["exchange"], r["asset"]) for r in c.execute(
        "SELECT ticker,exchange,asset FROM positions WHERE broker=? AND asset NOT IN"
        " ('crypto') ORDER BY ticker", (br,))]


def daily_prompt(c, market=None):
    market = market or MK.DEFAULT
    if market == "in":
        return kite_daily_prompt(c)
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

def fundamentals_prompt(c, tickers=None, market=None):
    market = market or MK.DEFAULT
    if market == "in":
        return in_fundamentals_prompt(c)
    import sectors as S
    held = _holdings(c, "us") if tickers is None else [(t, None, "equity") for t in tickers]
    lines, fund_l = [], []
    for t, _exch, _asset in held:
        sec = S.sector_of(t)
        if sec == "crypto":
            continue
        if sec.endswith("_etf"):
            fund_l.append(t)
        lines.append(f"  {t:6} ({S.label(sec)}): {', '.join(S.metrics_for(t)[:8])}")
    funds = "/".join(fund_l)
    return f"""Refresh fundamentals for my US holdings. Sector-specific metric sets below.

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
     Numerator and denominator must cover the SAME period — filing-year operating income
     over TTM revenue is wrong. Only rows with empty `axises` are consolidated totals.

Metrics that matter per ticker:
{chr(10).join(lines)}

For funds ({funds or "none held"}) skip company metrics — record only expense_ratio and
dividend_yield if available. Crypto is out of scope for this prompt.

Write to {INBOX}/fundamentals.json as:
{{"kind":"fundamentals","fetched_at":"<ISO>",
  "data":[{{"ticker":"<TICKER>","metric":"gross_margin","value":0.73}}, ...]}}
Values as plain numbers; ratios as decimals (0.73 not "73%").

Then run: python3 {ROOT}/app/ingest.py inbox"""


def in_fundamentals_prompt(c):
    """Indian holdings. The Robinhood MCP cannot serve them and Kite has no fundamentals
    endpoint, so this uses yfinance in the TradingAgents venv — the same source the
    research integration already relies on."""
    import sectors as S
    held = _holdings(c, "in")
    eq = [(t, e) for t, e, a in held if a == "equity"]
    other = [(t, a) for t, e, a in held if a != "equity"]
    lines = [f"  {t:12} -> {MK.yahoo_symbol(t, 'in', e)}" for t, e in eq]
    try:
        vp = CFG.tradingagents().get("venv_python", "python3")
    except Exception:
        vp = "python3"
    return f"""Refresh fundamentals for my {len(eq)} Indian holdings (NSE/BSE).

The Robinhood MCP cannot price these, and Kite exposes no fundamentals endpoint, so use
**yfinance** — the same source the research integration uses. Run it with the
TradingAgents interpreter, which already has yfinance installed:

  {vp}

For each symbol read `Ticker(sym).info` and map these fields (yfinance field on the
left, Trans metric name on the right):

    trailingPE          => pe
    priceToBook         => pb
    marketCap           => market_cap
    fiftyTwoWeekHigh    => high_52
    fiftyTwoWeekLow     => low_52
    profitMargins       => net_margin
    grossMargins        => gross_margin
    operatingMargins    => operating_margin
    revenueGrowth       => revenue_growth
    returnOnEquity      => roe
    totalRevenue        => revenue
    dividendYield       => dividend_yield   ** DIVIDE BY 100 **
    debtToEquity        => debt_to_equity   ** DIVIDE BY 100 **

Both marked fields are reported by yfinance as PERCENTAGES, not decimals. Verified:
IOC.NS returns dividendYield 6.04 against an implied 6.02% from dividendRate/price, and
MSFT returns 0.73 against 0.73%. Storing them raw would be a 100x error — DIXON would
read as a 7% yielder when the real figure is 0.07%.

Yahoo symbols for my holdings (NSE takes .NS, BSE takes .BO):
{chr(10).join(lines)}

Skip anything that returns no data rather than guessing, and skip these non-equity
holdings entirely: {', '.join(f'{t} ({a})' for t, a in other) or 'none'}.

Store the metrics under the PLAIN ticker (DIXON, not DIXON.BO) so they match the
positions table.

Write to {INBOX}/fundamentals_in.json as:
{{"kind":"fundamentals","fetched_at":"<ISO>",
  "data":[{{"ticker":"DIXON","metric":"pe","value":58.4}}, ...]}}
Ratios as decimals. Then run: python3 {ROOT}/app/ingest.py inbox"""


def kite_daily_prompt(c):
    """Zerodha. Its order book only lives for a day, so a sync captures today's trades
    and refreshes holdings — history before today has to come from a Console tradebook
    CSV export, which is a separate one-off import."""
    cur = cursor(c, "in")
    return f"""Sync my Zerodha (Kite) portfolio into Trans. Read-only — never place,
modify or cancel an order.

1. get_holdings() — the full holdings list.
2. get_trades() — today's executed trades (Kite keeps only one day; that is expected).
3. get_positions() — intraday/derivative positions, if any.

Write to {INBOX}/ as separate envelope files:
  {{"kind":"kite_holdings","fetched_at":"<ISO>","data":[ ...raw holdings objects... ]}}
  {{"kind":"kite_trades","fetched_at":"<ISO>","data":[ ...raw trade objects... ]}}

For holdings pass the raw objects through unchanged — the ingester reads
opening_quantity, because pledged or authorised shares report quantity 0 while still
being owned.

Then run: python3 {ROOT}/app/ingest.py inbox

We currently hold Zerodha data through {cur['equity_through'] or '(nothing yet)'}.
Do not call the Anthropic API for any of this."""


def bootstrap_prompt(c, market=None):
    if (market or MK.DEFAULT) == "in":
        return kite_bootstrap_prompt(c)
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

def kite_bootstrap_prompt(c):
    return f"""Zerodha history cannot be pulled from the API — Kite's order book "only
lives for a day in the system", so get_orders/get_trades return today only.

To build history, export it from Console yourself:

1. Open https://console.zerodha.com -> Reports -> Tradebook
2. Set the widest date range offered (about 3 years) and pick all segments
3. Download as CSV
4. Save it to {ROOT}/sync/inbox/zerodha_tradebook.csv

Then run: python3 {ROOT}/app/ingest.py inbox

The CSV carries symbol, trade date, exchange, segment, buy/sell, quantity, price and
trade id — everything needed for XIRR. Until it is imported the India tab shows holdings
and value but reports no XIRR, which is honest rather than broken.

Meanwhile, say "sync" to capture today's trades and refresh holdings."""


def write_prompt_files(c):
    d = os.path.join(ROOT, "sync", "prompts"); os.makedirs(d, exist_ok=True)
    out = {}
    for mkey in ("us", "in"):
        for name, fn in (("daily", daily_prompt), ("fundamentals", fundamentals_prompt),
                         ("bootstrap", bootstrap_prompt)):
            try:
                txt = fn(c, market=mkey)
            except Exception as e:
                txt = f"(could not generate: {e})"
            out[f"{mkey}:{name}"] = txt
            open(os.path.join(d, f"{mkey}_{name}.txt"), "w").write(txt)
    return out

if __name__ == "__main__":
    c = D.connect()
    print("cursor:", json.dumps(cursor(c)))
    p = write_prompt_files(c)
    print("\n--- daily prompt ---\n" + p["daily"][:600] + "\n...")
