# SPDX-License-Identifier: Apache-2.0
"""Ingest is idempotent, or it is nothing.

Re-running a sync, re-uploading a tradebook, or handing over overlapping exports must add
only what is new. The dedupe key is the subject of most of these tests because getting it
wrong silently double-counts a portfolio.
"""
import unittest, os, csv, tempfile, shutil
from tests.fixtures import TempDB

HEADER = ("symbol,isin,trade_date,exchange,segment,series,trade_type,auction,"
          "quantity,price,trade_id,order_id,order_execution_time")


def tradebook(path, rows, segment="EQ"):
    with open(path, "w", newline="") as fh:
        fh.write(HEADER + "\n")
        for r in rows:
            sym, isin, date, side, qty, px, tid = r
            fh.write(f"{sym},{isin},{date},NSE,{segment},EQ,{side},false,"
                     f"{qty},{px},{tid},{tid},{date}T00:00:00\n")
    return path


class EquityTradebook(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _file(self, name, rows, segment="EQ"):
        return tradebook(os.path.join(self.dir, name), rows, segment)

    def test_reupload_of_the_same_file_adds_nothing(self):
        with TempDB() as db:
            import ingest as I
            f = self._file("tb.csv", [("INFY", "INE009A01021", "2024-01-02", "buy", 1, 1500, "1")])
            n1, _ = I.ingest_zerodha_tradebook(db.conn, [f])
            n2, _ = I.ingest_zerodha_tradebook(db.conn, [f])
            self.assertEqual((n1, n2), (1, 0))
            self.assertEqual(db.count(), 1)

    def test_overlapping_exports_add_only_what_is_new(self):
        with TempDB() as db:
            import ingest as I
            a = self._file("a.csv", [("INFY", "X", "2024-01-02", "buy", 1, 1500, "1"),
                                     ("INFY", "X", "2024-02-02", "buy", 1, 1600, "2")])
            b = self._file("b.csv", [("INFY", "X", "2024-02-02", "buy", 1, 1600, "2"),
                                     ("INFY", "X", "2024-03-02", "buy", 1, 1700, "3")])
            I.ingest_zerodha_tradebook(db.conn, [a])
            n, _ = I.ingest_zerodha_tradebook(db.conn, [b])
            self.assertEqual(n, 1)
            self.assertEqual(db.count(), 3)

    def test_trade_id_alone_is_not_unique_enough(self):
        # A real export reused id 26030407 across two years and two tickers.
        with TempDB() as db:
            import ingest as I
            f = self._file("tb.csv", [("HEROMOTOCO", "X", "2022-04-07", "buy", 1, 100, "26030407"),
                                      ("FILATEX", "Y", "2021-04-07", "buy", 1, 50, "26030407")])
            n, _ = I.ingest_zerodha_tradebook(db.conn, [f])
            self.assertEqual(n, 2)

    def test_dedupe_survives_a_rename_added_afterwards(self):
        # The key uses the RAW symbol precisely so that adding an alias later cannot
        # re-import the same trades under the new name.
        with TempDB() as db:
            import ingest as I, config as CFG
            f = self._file("tb.csv", [("OLDSYM", "X", "2024-01-02", "buy", 5, 100, "7")])
            I.ingest_zerodha_tradebook(db.conn, [f])
            CFG._cache = {"ticker_aliases": {"OLDSYM": "NEWSYM"}}
            try:
                n, _ = I.ingest_zerodha_tradebook(db.conn, [f])
            finally:
                CFG._cache = {}
            self.assertEqual(n, 0)
            self.assertEqual(db.count(), 1)

    def test_series_codes_are_normalised_on_ingest(self):
        with TempDB() as db:
            import ingest as I
            f = self._file("tb.csv", [("MTARTECH-BE", "X", "2024-01-02", "buy", 5, 100, "9")])
            I.ingest_zerodha_tradebook(db.conn, [f])
            self.assertEqual(
                db.conn.execute("SELECT ticker FROM transactions").fetchone()[0], "MTARTECH")

    def test_rows_without_a_usable_quantity_are_skipped(self):
        with TempDB() as db:
            import ingest as I
            f = self._file("tb.csv", [("INFY", "X", "2024-01-02", "buy", 0, 1500, "1")])
            n, _ = I.ingest_zerodha_tradebook(db.conn, [f])
            self.assertEqual(n, 0)


class MutualFundTradebook(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_mf_rows_key_on_isin_not_the_fund_name(self):
        # The same ISIN is spelled differently across Zerodha's own sources, so keying on
        # the name would split one fund's history in two.
        with TempDB() as db:
            import ingest as I
            p = os.path.join(self.dir, "mf.csv")
            tradebook(p, [("QUANT ACTIVE FUND - DIRECT PLAN", "INF966L01614",
                           "2024-01-02", "buy", 10.5, 400, "11")], segment="MF")
            I.ingest_zerodha_tradebook(db.conn, [p])
            row = db.conn.execute("SELECT ticker,asset FROM transactions").fetchone()
            self.assertEqual(row["ticker"], "INF966L01614")
            self.assertEqual(row["asset"], "mf")

    def test_the_same_fund_under_a_different_name_still_dedupes(self):
        with TempDB() as db:
            import ingest as I
            a = os.path.join(self.dir, "a.csv"); b = os.path.join(self.dir, "b.csv")
            tradebook(a, [("QUANT ACTIVE FUND", "INF966L01614", "2024-01-02", "buy",
                           10.5, 400, "11")], segment="MF")
            tradebook(b, [("QUANT MULTI CAP FUND", "INF966L01614", "2024-01-02", "buy",
                           10.5, 400, "11")], segment="MF")
            I.ingest_zerodha_tradebook(db.conn, [a])
            n, _ = I.ingest_zerodha_tradebook(db.conn, [b])
            self.assertEqual(n, 0)


class Remap(unittest.TestCase):
    def test_remap_reaches_rows_a_reupload_cannot(self):
        with TempDB() as db:
            import ingest as I, config as CFG
            db.buy("OLDSYM", "2024-01-02", 5, 100.0, broker="zerodha", currency="INR")
            CFG._cache = {"ticker_aliases": {"OLDSYM": "NEWSYM"}}
            try:
                moved = I.remap_symbols(db.conn)
            finally:
                CFG._cache = {}
            self.assertEqual(len(moved), 1)
            self.assertEqual(
                db.conn.execute("SELECT ticker FROM transactions").fetchone()[0], "NEWSYM")

    def test_remap_is_safe_to_run_twice(self):
        with TempDB() as db:
            import ingest as I, config as CFG
            db.buy("OLDSYM", "2024-01-02", 5, 100.0, broker="zerodha", currency="INR")
            CFG._cache = {"ticker_aliases": {"OLDSYM": "NEWSYM"}}
            try:
                I.remap_symbols(db.conn)
                self.assertEqual(I.remap_symbols(db.conn), [])
            finally:
                CFG._cache = {}


class Folders(unittest.TestCase):
    def test_ingest_creates_its_own_inbox_and_archive(self):
        # git does not track empty directories, so these cannot be assumed present.
        import ingest as I
        self.assertTrue(os.path.isdir(I.INBOX))
        self.assertTrue(os.path.isdir(I.ARCHIVE))


if __name__ == "__main__":
    unittest.main()
