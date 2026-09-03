# SPDX-License-Identifier: Apache-2.0
"""Temp databases for tests.

Every test gets its own SQLite file in a temp directory. Nothing here ever touches
portfolio.db — a test suite that can damage the user's data is worse than no suite.
"""
import os, sys, tempfile, shutil, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, ROOT)


class TempDB:
    """Context manager giving a fresh schema-initialised database.

        with TempDB() as db:
            db.buy("AAPL", "2024-01-02", 10, 100.0)
            rows, ov = db.results("us")
    """

    def __init__(self):
        self.dir = None
        self.path = None
        self._c = None

    def __enter__(self):
        import db as D
        import config as CFG
        # Isolate from the developer's own config.json. Without this, a ticker_aliases or
        # demergers entry on one machine changes what the tests assert, and a suite whose
        # result depends on who runs it is not a suite.
        self._saved_cfg = CFG._cache
        CFG._cache = {}
        self.dir = tempfile.mkdtemp(prefix="trans-test-")
        self.path = os.path.join(self.dir, "test.db")
        self._saved = D.DB_PATH
        D.DB_PATH = self.path
        self._c = D.init()
        self.D = D
        return self

    def __exit__(self, *exc):
        import config as CFG
        try:
            if self._c:
                self._c.close()
        finally:
            self.D.DB_PATH = self._saved
            CFG._cache = self._saved_cfg
            shutil.rmtree(self.dir, ignore_errors=True)
        return False

    # ---- writing ----------------------------------------------------------
    def tx(self, ticker, date, kind, qty, price, broker="robinhood",
           currency="USD", asset="equity", oid=None, amount=None):
        oid = oid or f"t{self._c.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]}"
        if amount is None:
            amount = -qty * price if kind == "buy" else qty * price
        self._c.execute(
            "INSERT OR IGNORE INTO transactions(order_id,date,ticker,type,quantity,price,"
            "amount,fees,asset,agent,broker,currency) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, date, ticker, kind, qty, price, amount, 0.0, asset, "user",
             broker, currency))
        self._c.commit()

    def buy(self, ticker, date, qty, price, **kw):
        self.tx(ticker, date, "buy", qty, price, **kw)

    def sell(self, ticker, date, qty, price, **kw):
        self.tx(ticker, date, "sell", qty, price, **kw)

    def dividend(self, ticker, date, amount, broker="robinhood", currency="USD"):
        n = self._c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        self._c.execute(
            "INSERT OR IGNORE INTO transactions(order_id,date,ticker,type,amount,fees,"
            "asset,agent,broker,currency) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (f"d{n}", date, ticker, "dividend", amount, 0.0, "equity", "drip",
             broker, currency))
        self._c.commit()

    def position(self, ticker, qty, price, broker="robinhood", currency="USD",
                 asset="equity", avg_cost=None, exchange=None, asof=None):
        self._c.execute(
            "INSERT OR REPLACE INTO positions(ticker,quantity,price,asset,asof,broker,"
            "currency,exchange,avg_cost) VALUES(?,?,?,?,?,?,?,?,?)",
            (ticker, qty, price, asset, asof or dt.date.today().isoformat(), broker,
             currency, exchange, avg_cost))
        self._c.commit()

    def name(self, ticker, value):
        self._c.execute(
            "INSERT OR REPLACE INTO fundamentals(ticker,asof,metric,value,text_value)"
            " VALUES(?,?,?,NULL,?)", (ticker, dt.date.today().isoformat(), "name", value))
        self._c.commit()

    # ---- reading ----------------------------------------------------------
    @property
    def conn(self):
        return self._c

    def results(self, market=None):
        import analytics as A
        return A.results(self._c, market=market)

    def count(self, table="transactions"):
        return self._c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def ago(days):
    return (dt.date.today() - dt.timedelta(days=days)).isoformat()
