#!/usr/bin/env python3
"""Build samples/demo.db — a small, entirely invented portfolio.

Kept as a script rather than a checked-in binary nobody can audit: you can read exactly
what goes in, and rebuild it after a schema change with one command.

It covers both markets and, deliberately, the awkward cases that make this app's numbers
worth reading — a position sold and bought back, a stock renamed mid-history, a mutual
fund with no order history, and shares from a demerger that cost nothing. A demo where
everything is tidy would teach the wrong lesson.

    python3 samples/build_demo.py
"""
import os, sys, sqlite3, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
OUT = os.path.join(ROOT, "samples", "demo.db")

# Dates are days-before-today, not calendar dates, so the demo never goes stale: XIRR 1Y
# keeps something to measure and "days held" stays sensible however long this sits in git.
# (days ago, ticker, type, qty, price)
US = [
    (1996, "AAPL", "buy", 20, 121.50), (1857, "AAPL", "buy", 10, 145.20),
    (1541, "AAPL", "buy", 8, 132.76),  (571,  "AAPL", "sell", 15, 188.85),
    (2185, "NVDA", "buy", 30, 49.80),  (1197, "NVDA", "sell", 30, 37.99),
    (1037, "NVDA", "buy", 12, 40.71),                      # re-entered after selling out
    (1919, "MSFT", "buy", 13, 247.40),
    (1688, "VOO",  "buy", 9, 421.10),  (1150, "VOO", "buy", 6, 412.33),
    (250,  "COST", "buy", 4, 905.10),  (120, "COST", "buy", 2, 941.60),   # opened this year
    (900,  "PFE",  "buy", 60, 28.40),  (300, "PFE",  "sell", 60, 24.15),  # closed at a loss
]
US_DIV = [(1660, "AAPL", 6.60), (1295, "AAPL", 7.15), (571, "MSFT", 9.10)]
# India: an equity renamed mid-history, one still held, and an ETF sold at a loss.
IN = [
    (1612, "OLDNAME", "buy", 40, 210.00),   # renamed to NEWNAME below
    (1381, "OLDNAME", "buy", 25, 244.50),
    (1085, "NEWNAME", "sell", 30, 388.10),
    (1903, "DEMOCEM", "buy", 60, 318.40),
    (1483, "DEMOCEM", "buy", 30, 356.90),
    (957,  "DEMOCEM", "sell", 25, 502.75),
    (2038, "DEMOETF", "buy", 100, 44.10),
    (1556, "DEMOETF", "sell", 100, 38.65),
    (190,  "DEMOPOW", "buy", 45, 372.00),   # opened this year, still held
    (95,   "DEMOPOW", "buy", 15, 410.50),
]
POSITIONS = [
    ("AAPL", 23, 232.80, "equity", "robinhood", "USD", None, 128.60),
    ("NVDA", 12, 178.20, "equity", "robinhood", "USD", None, 40.71),
    ("MSFT", 13, 509.40, "equity", "robinhood", "USD", None, 247.40),
    ("VOO",  15, 598.05, "equity", "robinhood", "USD", None, 417.59),
    ("COST", 6, 968.30, "equity", "robinhood", "USD", None, 917.27),
    ("NEWNAME", 35, 470.25, "equity", "zerodha", "INR", "NSE", 223.30),
    ("DEMOCEM", 65, 549.10, "equity", "zerodha", "INR", "NSE", 331.20),
    ("DEMOPOW", 60, 428.75, "equity", "zerodha", "INR", "NSE", 381.63),
    ("DEMOMF01", 812.44, 61.90, "mf", "zerodha", "INR", "MF", 43.15),   # no order history
    ("DEMOSPIN", 60, 88.40, "equity", "zerodha", "INR", "NSE", 12.05),  # demerger, no cost
]
NAMES = {"AAPL": "Apple Inc.", "NVDA": "NVIDIA Corporation",
         "MSFT": "Microsoft Corporation", "VOO": "Vanguard S&P 500 ETF",
         "NEWNAME": "Demo Industries Limited", "DEMOCEM": "Demo Cement Limited",
         "DEMOETF": "Demo Gold ETF", "DEMOMF01": "Demo Flexi Cap Fund - Direct Growth",
         "COST": "Costco Wholesale Corporation", "PFE": "Pfizer Inc.",
         "DEMOPOW": "Demo Power Limited",
         "DEMOSPIN": "Demo Spinco Limited"}
FUNDAMENTALS = [
    ("AAPL", "pe", 34.2), ("AAPL", "market_cap", 3.51e12), ("AAPL", "gross_margin", 0.462),
    ("NVDA", "pe", 51.8), ("NVDA", "market_cap", 4.34e12), ("NVDA", "gross_margin", 0.751),
    ("MSFT", "pe", 37.1), ("MSFT", "market_cap", 3.78e12), ("MSFT", "gross_margin", 0.679),
    ("VOO", "dividend_yield", 0.0121),
    ("NEWNAME", "pe", 41.6), ("NEWNAME", "pb", 7.2), ("NEWNAME", "net_margin", 0.114),
    ("DEMOCEM", "pe", 28.9), ("DEMOCEM", "pb", 3.4), ("DEMOCEM", "net_margin", 0.081),
]



# Sections of the fabricated report, in the shape app/analysis.py stores them: `ta:<kind>`
# rows in ai_notes. Written to exercise the renderer — the verdict strip reads Rating,
# Confidence and Horizon out of the decision section; the markdown pipeline handles the
# table, the numbered points and the bold labels — without asserting anything about a real
# security. Demo Power Limited does not exist.
REPORT = [
    ("ta:decision", """
**Rating**: Hold
**Confidence**: Medium
**Horizon**: 12 months

**Executive Summary**: Demo Power Limited has re-rated sharply since the position was
opened and now trades above the range this analysis considers supportable. The operating
story is intact; the price already reflects it. Holding is reasonable. Adding at these
levels is not, on the evidence below.

This is a fabricated report about a company that does not exist. It ships with the demo
so the Research tab has something to render, and so you can see the shape of the output
before spending anything to generate a real one.
"""),
    ("ta:fundamentals", """
**What the numbers say**

| Metric | Demo Power | Sector median | Read |
|---|---|---|---|
| P/E | 41.6x | 24.2x | Expensive |
| P/B | 7.2x | 3.1x | Expensive |
| Net margin | 11.4% | 8.9% | Better than peers |
| Revenue growth (YoY) | 18.3% | 9.7% | Clearly better |
| Debt / equity | 0.34x | 0.71x | Conservative |

The premium is real and partly earned: margins and growth both beat the sector, and the
balance sheet carries less debt than most of it. The question is not whether the business
is better. It is whether it is 70% better, which is what the multiple is asking.
"""),
    ("ta:market", """
**Price behaviour**

1) The stock is up roughly 15% against an entry three months ago, against a sector that
   is flat over the same window.
2) Volume on up-days has been running well above the trailing average, which usually
   means the move is being bought rather than drifting.
3) It sits near the top of its 52-week range, so there is no technical support close
   beneath the current price.

None of this says anything about value. It says the market has already noticed.
"""),
    ("ta:news", """
**Recent developments**

- A large order win was announced during the quarter, which accounts for much of the
  re-rating.
- Management guided margins slightly higher for the coming year.
- An input-cost pass-through remains unresolved and is the clearest near-term risk.

Order wins are lumpy. One of them is not a trend, and the guidance assumes the cost
pass-through lands.
"""),
    ("ta:bull", """
**The case for holding on**

The order book gives visibility that most of the sector does not have, and the balance
sheet means growth does not need financing. If margins hold at the guided level, today's
multiple compresses on its own within two years without the price falling.
"""),
    ("ta:bear", """
**The case against adding**

At 41.6x the multiple assumes the guidance lands and the order book converts. Both are
plausible; neither is certain. A single missed quarter re-rates a stock priced like this
much harder than one priced at the sector median, and there is no technical support
nearby to slow it.
"""),
    ("ta:risk_neutral", """
**Where the two meet**

Both cases agree on the business and disagree only on the price. That is the honest
summary, and it is why the rating is Hold rather than Buy or Sell: the position is worth
keeping, and the entry price for new money is worse than the one already paid.

**Position sizing**: this holding is a small share of the portfolio. Nothing here argues
for changing that in either direction.
"""),
    ("ta:plain", """
**In plain English**

You own this. It has gone up a lot, quickly.

The company is genuinely doing well — it grows faster than its competitors, keeps more of
each rupee it earns, and does not owe much. That part is not in doubt.

The catch is the price. You are paying about 42 times yearly profits, where similar
companies cost about 24 times. You are paying up front for growth that has not happened
yet. If it happens, fine. If one quarter disappoints, a stock priced this way falls
further than a cheaper one would.

So: keeping what you have is sensible. Buying more at this price is a different decision
from the one you made when you bought it, and this analysis does not support it.

*Fabricated example. Demo Power Limited is not a real company.*
"""),
]


def main():
    if os.path.exists(OUT):
        os.remove(OUT)
    import db as D
    D.DB_PATH = OUT
    c = D.init()
    today = dt.date.today().isoformat()

    def tx(oid, date, tick, typ, qty, px, asset, broker, cur, agent="user"):
        amt = -qty * px if typ == "buy" else qty * px
        c.execute("INSERT OR IGNORE INTO transactions(order_id,date,ticker,type,quantity,"
                  "price,amount,fees,asset,agent,broker,currency)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (oid, date, tick, typ, qty, px, amt, 0.0, asset, agent, broker, cur))

    ago = lambda n: (dt.date.today() - dt.timedelta(days=n)).isoformat()
    for i, (n, t, ty, q, p) in enumerate(US):
        tx(f"demo-us-{i}", ago(n), t, ty, q, p, "equity", "robinhood", "USD", "recurring")
    for i, (n, t, amt) in enumerate(US_DIV):
        c.execute("INSERT OR IGNORE INTO transactions(order_id,date,ticker,type,amount,"
                  "fees,asset,agent,broker,currency) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (f"demo-div-{i}", ago(n), t, "dividend", amt, 0.0, "equity", "drip",
                   "robinhood", "USD"))
    for i, (n, t, ty, q, p) in enumerate(IN):
        tx(f"demo-in-{i}", ago(n), t, ty, q, p, "equity", "zerodha", "INR")

    for tick, qty, px, asset, broker, cur, exch, avg in POSITIONS:
        c.execute("INSERT OR REPLACE INTO positions(ticker,quantity,price,asset,asof,"
                  "broker,currency,exchange,avg_cost) VALUES(?,?,?,?,?,?,?,?,?)",
                  (tick, qty, px, asset, today, broker, cur, exch, avg))
    for tick, name in NAMES.items():
        c.execute("INSERT OR REPLACE INTO fundamentals(ticker,asof,metric,value,text_value)"
                  " VALUES(?,?,?,NULL,?)", (tick, today, "name", name))
    for tick, metric, val in FUNDAMENTALS:
        c.execute("INSERT OR REPLACE INTO fundamentals(ticker,asof,metric,value,text_value)"
                  " VALUES(?,?,?,?,NULL)", (tick, today, metric, val))

    # A fabricated report, written by hand, on a company that does not exist.
    #
    # This used to copy a real TradingAgents run out of whatever portfolio.db happened to
    # be sitting next to it. That was wrong twice over: it published a dated model rating
    # on a named public company, and it meant nobody else could rebuild the demo — clone
    # the repo, run this script, and you got a demo with no report at all.
    n_notes = 0
    for kind, body in REPORT:
        c.execute("INSERT INTO ai_notes(ticker,created_at,kind,content,source)"
                  " VALUES(?,?,?,?,?)",
                  ("DEMOPOW", f"{today}T09:00:00", kind, body.strip(),
                   "demo/sample-report"))
        n_notes += 1

    c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('demo','1')")
    c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('demo_built',?)", (today,))
    c.commit()
    # Ship it in rollback-journal mode, not WAL. A WAL database grows -wal and -shm
    # sidecars the moment anyone reads it, which would litter a clean checkout with
    # untracked files the leak guard then refuses. The live database still uses WAL.
    c.execute("PRAGMA journal_mode=DELETE")
    n_t = c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    n_p = c.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    c.close()
    print(f"built {OUT}")
    print(f"  {n_t} transactions, {n_p} positions, {n_notes} research sections, "
          f"{os.path.getsize(OUT)/1024:.0f} KB")


if __name__ == "__main__":
    main()
