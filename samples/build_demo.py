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

    # Carry the real MSFT research report across when one exists locally, so the Research
    # tab has something to show. Public-company analysis, nothing personal in it.
    src = os.path.join(ROOT, "portfolio.db")
    n_notes = 0
    if os.path.exists(src):
        s = sqlite3.connect(src); s.row_factory = sqlite3.Row
        try:
            for r in s.execute("SELECT ticker,created_at,kind,content,source"
                               " FROM ai_notes WHERE ticker='MSFT'"):
                c.execute("INSERT INTO ai_notes(ticker,created_at,kind,content,source)"
                          " VALUES(?,?,?,?,?)",
                          (r["ticker"], r["created_at"], r["kind"], r["content"], r["source"]))
                n_notes += 1
        except sqlite3.Error:
            pass
        s.close()

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
