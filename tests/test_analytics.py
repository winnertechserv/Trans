# SPDX-License-Identifier: Apache-2.0
"""Read models: cost basis, the realised/unrealised split, scoped XIRRs, the trade log.

The identity `realised + unrealised + dividends == net profit` is asserted repeatedly and
deliberately. Every time cost basis changed during development, that identity is what
caught the error.
"""
import unittest
from tests.fixtures import TempDB, ago


class CostBasis(unittest.TestCase):
    def test_broker_average_wins_when_present(self):
        with TempDB() as db:
            db.buy("A", ago(400), 10, 100.0, broker="zerodha", currency="INR")
            db.position("A", 10, 150.0, broker="zerodha", currency="INR", avg_cost=90.0)
            import analytics as A
            basis, _, _ = A.cost_basis(db.conn, "in")
            self.assertAlmostEqual(basis["A"], 900.0)   # 10 x 90, not 10 x 100

    def test_falls_back_to_weighted_average_from_the_stream(self):
        with TempDB() as db:
            db.buy("A", ago(400), 10, 100.0)
            db.buy("A", ago(200), 10, 200.0)
            db.position("A", 20, 180.0, avg_cost=None)   # Robinhood reports no average
            import analytics as A
            basis, _, _ = A.cost_basis(db.conn, "us")
            self.assertAlmostEqual(basis["A"], 3000.0)

    def test_demerged_child_carries_no_cost(self):
        with TempDB() as db:
            db.position("VAML", 120, 400.0, broker="zerodha", currency="INR", avg_cost=20.0)
            import analytics as A
            basis, _, _ = A.cost_basis(db.conn, "in")
            self.assertEqual(basis["VAML"], 0.0)

    def test_split_broken_stream_does_not_poison_the_basis(self):
        with TempDB() as db:
            db.buy("A", ago(500), 50, 100.0, broker="zerodha", currency="INR")
            db.sell("A", ago(100), 100, 60.0, broker="zerodha", currency="INR")
            db.position("A", 20, 70.0, broker="zerodha", currency="INR", avg_cost=55.0)
            import analytics as A
            basis, _, broken = A.cost_basis(db.conn, "in")
            self.assertIn("A", broken)
            self.assertAlmostEqual(basis["A"], 1100.0)   # broker average, not the stream


class RealisedUnrealisedIdentity(unittest.TestCase):
    def _identity(self, ov):
        return ov["realized"] + ov["unrealized"] + ov["dividends"]

    def test_holds_for_an_open_position(self):
        with TempDB() as db:
            db.buy("A", ago(400), 10, 100.0)
            db.position("A", 10, 150.0, avg_cost=100.0)
            _, ov = db.results("us")
            self.assertAlmostEqual(self._identity(ov), ov["net_profit"], places=4)

    def test_holds_after_a_partial_sale(self):
        with TempDB() as db:
            db.buy("A", ago(400), 20, 100.0)
            db.sell("A", ago(100), 10, 130.0)
            db.position("A", 10, 150.0, avg_cost=100.0)
            _, ov = db.results("us")
            self.assertAlmostEqual(self._identity(ov), ov["net_profit"], places=4)

    def test_holds_with_dividends_and_a_closed_position(self):
        with TempDB() as db:
            db.buy("A", ago(400), 10, 100.0)
            db.dividend("A", ago(200), 25.0)
            db.buy("B", ago(300), 5, 50.0)
            db.sell("B", ago(50), 5, 80.0)
            db.position("A", 10, 150.0, avg_cost=100.0)
            _, ov = db.results("us")
            self.assertAlmostEqual(self._identity(ov), ov["net_profit"], places=4)

    def test_holds_when_a_demerged_child_has_no_cost(self):
        with TempDB() as db:
            db.buy("VEDL", ago(800), 120, 289.0, broker="zerodha", currency="INR")
            db.position("VEDL", 120, 270.0, broker="zerodha", currency="INR", avg_cost=289.0)
            db.position("VAML", 120, 437.0, broker="zerodha", currency="INR", avg_cost=20.0)
            _, ov = db.results("in")
            self.assertAlmostEqual(self._identity(ov), ov["net_profit"], places=4)


class ScopedXirr(unittest.TestCase):
    def test_open_and_closed_are_computed_separately(self):
        with TempDB() as db:
            db.buy("OPEN", ago(500), 10, 100.0)
            db.position("OPEN", 10, 200.0, avg_cost=100.0)
            db.buy("SHUT", ago(500), 10, 100.0)
            db.sell("SHUT", ago(100), 10, 90.0)          # a loser, fully exited
            _, ov = db.results("us")
            self.assertGreater(ov["xirr_open"], 0)
            self.assertLess(ov["xirr_closed"], 0)
            self.assertEqual(ov["n_open"], 1)
            self.assertEqual(ov["n_closed"], 1)

    def test_net_sits_between_the_two(self):
        with TempDB() as db:
            db.buy("OPEN", ago(500), 10, 100.0)
            db.position("OPEN", 10, 200.0, avg_cost=100.0)
            db.buy("SHUT", ago(500), 10, 100.0)
            db.sell("SHUT", ago(100), 10, 90.0)
            _, ov = db.results("us")
            self.assertLess(ov["xirr"], ov["xirr_open"])
            self.assertGreater(ov["xirr"], ov["xirr_closed"])

    def test_one_year_slice_takes_positions_opened_in_the_window(self):
        with TempDB() as db:
            db.buy("OLD", ago(900), 10, 100.0)
            db.position("OLD", 10, 120.0, avg_cost=100.0)
            db.buy("NEW", ago(120), 10, 100.0)
            db.position("NEW", 10, 150.0, avg_cost=100.0)
            _, ov = db.results("us")
            self.assertEqual(ov["n_1y"], 1)

    def test_a_sale_whose_purchase_predates_the_window_is_excluded(self):
        # Counting every flow in the window instead would bring proceeds with no cost and
        # report a spectacular year.
        with TempDB() as db:
            db.buy("OLD", ago(900), 10, 100.0)
            db.sell("OLD", ago(30), 10, 500.0)
            _, ov = db.results("us")
            self.assertEqual(ov["n_1y"], 0)
            self.assertIsNone(ov["xirr_1y"])


class MarketSeparation(unittest.TestCase):
    def test_markets_are_never_blended(self):
        with TempDB() as db:
            db.buy("US1", ago(400), 10, 100.0)
            db.position("US1", 10, 150.0, avg_cost=100.0)
            db.buy("IN1", ago(400), 10, 100.0, broker="zerodha", currency="INR")
            db.position("IN1", 10, 300.0, broker="zerodha", currency="INR", avg_cost=100.0)
            _, us = db.results("us")
            _, ind = db.results("in")
            self.assertAlmostEqual(us["market_value"], 1500.0)
            self.assertAlmostEqual(ind["market_value"], 3000.0)

    def test_india_reads_both_of_its_brokers(self):
        with TempDB() as db:
            db.buy("Z", ago(400), 10, 100.0, broker="zerodha", currency="INR")
            db.position("Z", 10, 150.0, broker="zerodha", currency="INR", avg_cost=100.0)
            db.buy("P", ago(400), 10, 100.0, broker="paytm", currency="INR", asset="mf")
            db.position("P", 10, 200.0, broker="paytm", currency="INR", asset="mf",
                        avg_cost=100.0)
            rows, ov = db.results("in")
            self.assertEqual({r["ticker"] for r in rows if r["market_value"] > 0}, {"Z", "P"})
            self.assertAlmostEqual(ov["market_value"], 3500.0)


class NoCostFlag(unittest.TestCase):
    def test_sales_with_no_purchase_are_flagged(self):
        with TempDB() as db:
            db.sell("SPIN", ago(100), 10, 50.0, broker="zerodha", currency="INR")
            rows, _ = db.results("in")
            r = next(x for x in rows if x["ticker"] == "SPIN")
            self.assertTrue(r["no_cost"])
            self.assertIn("no purchase on record", r["note"])

    def test_an_ordinary_holding_is_not_flagged(self):
        with TempDB() as db:
            db.buy("A", ago(400), 10, 100.0)
            db.position("A", 10, 150.0, avg_cost=100.0)
            rows, _ = db.results("us")
            self.assertFalse(next(x for x in rows if x["ticker"] == "A").get("no_cost"))


class Unreconciled(unittest.TestCase):
    def test_a_fund_short_of_units_is_reported(self):
        # A missing financial year of statements looks exactly like this.
        with TempDB() as db:
            db.buy("F", ago(400), 48.0, 100.0, broker="zerodha", currency="INR", asset="mf")
            db.position("F", 225.0, 120.0, broker="zerodha", currency="INR", asset="mf",
                        avg_cost=90.0)
            _, ov = db.results("in")
            self.assertIn("F", ov["unreconciled"])

    def test_equities_are_not_reported_since_splits_make_them_drift(self):
        with TempDB() as db:
            db.buy("E", ago(400), 48.0, 100.0, broker="zerodha", currency="INR")
            db.position("E", 225.0, 120.0, broker="zerodha", currency="INR", avg_cost=90.0)
            _, ov = db.results("in")
            self.assertNotIn("E", ov["unreconciled"])


class TradeLog(unittest.TestCase):
    def test_a_sale_is_matched_fifo_to_its_purchases(self):
        with TempDB() as db:
            db.buy("A", "2024-01-01", 10, 100.0)
            db.buy("A", "2024-06-01", 10, 200.0)
            db.sell("A", "2024-09-01", 15, 300.0)
            import analytics as A
            d = A.trades(db.conn, "A", market="us")
            sale = next(r for r in d["rows"] if r["type"] == "sell")
            self.assertEqual(len(sale["matched"]), 2)
            self.assertAlmostEqual(sale["matched"][0]["quantity"], 10)   # oldest first
            self.assertAlmostEqual(sale["matched"][1]["quantity"], 5)
            # 15 sold at 300 = 4500; cost 10x100 + 5x200 = 2000
            self.assertAlmostEqual(sale["realized"], 2500.0)

    def test_running_quantity_tracks_through_the_log(self):
        with TempDB() as db:
            db.buy("A", "2024-01-01", 10, 100.0)
            db.sell("A", "2024-02-01", 4, 120.0)
            import analytics as A
            d = A.trades(db.conn, "A", market="us")
            self.assertEqual([r["running"] for r in d["rows"]], [10, 6])

    def test_unmatched_sale_withholds_the_realised_figure(self):
        # Rather than compute profit against a cost that is not there.
        with TempDB() as db:
            db.buy("A", "2024-01-01", 10, 100.0, broker="zerodha", currency="INR")
            db.sell("A", "2024-06-01", 25, 120.0, broker="zerodha", currency="INR")
            import analytics as A
            d = A.trades(db.conn, "A", market="in")
            sale = next(r for r in d["rows"] if r["type"] == "sell")
            self.assertAlmostEqual(sale["unmatched"], 15.0)
            self.assertIsNone(sale["realized"])

    def test_open_lots_are_reported_oldest_first(self):
        with TempDB() as db:
            db.buy("A", "2024-01-01", 10, 100.0)
            db.buy("A", "2024-06-01", 5, 200.0)
            db.sell("A", "2024-07-01", 3, 250.0)
            import analytics as A
            d = A.trades(db.conn, "A", market="us")
            self.assertEqual([l["quantity"] for l in d["open_lots"]], [7.0, 5.0])


if __name__ == "__main__":
    unittest.main()
