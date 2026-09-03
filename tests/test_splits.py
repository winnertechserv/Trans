# SPDX-License-Identifier: Apache-2.0
"""Inferring a split ratio from what the order history cannot explain.

The rule throughout: propose, never assume. A wrong ratio silently rewrites realised
profit, and unlike a missing figure a wrong one does not announce itself.
"""
import unittest
from tests.fixtures import TempDB, ROOT  # noqa: F401
import splits as S


def rows(*t):
    return [{"date": d, "type": k, "quantity": q, "price": p} for d, k, q, p in t]


class Replay(unittest.TestCase):
    def test_a_clean_history_needs_no_split(self):
        r = rows(("2024-01-01", "buy", 10, 100.0), ("2024-06-01", "sell", 4, 150.0))
        ok, q, cost = S.replay(r, [])
        self.assertTrue(ok)
        self.assertAlmostEqual(q, 6)
        self.assertAlmostEqual(cost, 600.0)

    def test_selling_more_than_bought_fails_without_a_split(self):
        r = rows(("2024-01-01", "buy", 10, 100.0), ("2024-06-01", "sell", 20, 80.0))
        ok, _, _ = S.replay(r, [])
        self.assertFalse(ok)

    def test_the_right_split_makes_it_work(self):
        r = rows(("2024-01-01", "buy", 10, 100.0), ("2024-06-01", "sell", 20, 80.0))
        ok, q, _ = S.replay(r, [{"date": "2024-03-01", "ratio": 2}])
        self.assertTrue(ok)
        self.assertAlmostEqual(q, 0)

    def test_a_split_does_not_change_the_money_paid(self):
        r = rows(("2024-01-01", "buy", 10, 100.0))
        _, q, cost = S.replay(r, [{"date": "2024-03-01", "ratio": 2}])
        self.assertAlmostEqual(q, 20)
        self.assertAlmostEqual(cost, 1000.0)      # same rupees, twice the shares

    def test_shares_bought_after_the_split_are_untouched(self):
        r = rows(("2024-01-01", "buy", 10, 100.0), ("2024-06-01", "buy", 10, 50.0))
        _, q, _ = S.replay(r, [{"date": "2024-03-01", "ratio": 2}])
        self.assertAlmostEqual(q, 30)             # 20 adjusted + 10 bought after


class Inference(unittest.TestCase):
    def test_a_single_ratio_is_found_when_the_evidence_is_clear(self):
        # bought 10, split 2x, sold 15, holding 5 at half the original price
        r = rows(("2024-01-01", "buy", 10, 100.0), ("2024-06-01", "sell", 15, 80.0))
        ratios, dates = S.infer(r, held_qty=5, broker_avg=50.0)
        self.assertEqual(ratios, [2])
        self.assertTrue(dates)

    def test_a_ratio_that_contradicts_the_brokers_average_is_rejected(self):
        # The share count would close at 2x, but the broker says the average is
        # unchanged — so 2x cannot be right, and nothing is proposed.
        r = rows(("2024-01-01", "buy", 10, 100.0), ("2024-06-01", "sell", 15, 80.0))
        ratios, _ = S.infer(r, held_qty=5, broker_avg=100.0)
        self.assertEqual(ratios, [])

    def test_a_holding_that_never_bought_anything_is_not_a_split(self):
        # Demerged shares arrive with no purchase. That is a different problem.
        r = rows(("2024-06-01", "sell", 10, 80.0))
        self.assertEqual(S.infer(r, 0, None), ([], []))

    def test_ambiguity_returns_every_candidate_rather_than_choosing(self):
        r = rows(("2024-01-01", "buy", 1, 100.0), ("2024-06-01", "sell", 2, 80.0))
        ratios, _ = S.infer(r, held_qty=0, broker_avg=None)
        self.assertIn(2, ratios)


class Unmatched(unittest.TestCase):
    def test_finds_only_the_holdings_that_cannot_be_matched(self):
        with TempDB() as db:
            db.buy("FINE", "2024-01-01", 10, 100.0)
            db.sell("FINE", "2024-06-01", 4, 150.0)
            db.buy("SPLIT", "2024-01-01", 10, 100.0)
            db.sell("SPLIT", "2024-06-01", 20, 80.0)
            self.assertEqual(S.unmatched(db.conn), ["SPLIT"])


class AppliedToBookedProfit(unittest.TestCase):
    def test_a_confirmed_split_brings_the_holding_back(self):
        import analytics as A, config as CFG
        with TempDB() as db:
            db.buy("X", "2024-01-01", 10, 100.0)
            db.sell("X", "2024-06-01", 20, 80.0)
            d = A.contributions(db.conn, market="us")
            self.assertIn("X", d["realized_skipped"])        # skipped without one

            CFG._cache = {"splits": {"X": [{"date": "2024-03-01", "ratio": 2}]}}
            try:
                d = A.contributions(db.conn, market="us")
            finally:
                CFG._cache = {}
            self.assertNotIn("X", d["realized_skipped"])
            # 20 sold at 80 = 1600, against 1000 paid
            self.assertAlmostEqual(sum(x["realized"] for x in d["series"]), 600.0)

    def test_the_trade_log_agrees_with_the_booked_column(self):
        import analytics as A, config as CFG
        with TempDB() as db:
            db.buy("X", "2024-01-01", 10, 100.0)
            db.sell("X", "2024-06-01", 20, 80.0)
            CFG._cache = {"splits": {"X": [{"date": "2024-03-01", "ratio": 2}]}}
            try:
                d = A.contributions(db.conn, market="us")
                tl = A.trades(db.conn, "X", market="us")
            finally:
                CFG._cache = {}
            log = sum(r["realized"] for r in tl["rows"]
                      if r["type"] == "sell" and r["realized"] is not None)
            self.assertAlmostEqual(log, sum(x["realized"] for x in d["series"]))


if __name__ == "__main__":
    unittest.main()
