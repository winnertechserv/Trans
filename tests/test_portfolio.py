# SPDX-License-Identifier: Apache-2.0
"""Holding timeline and per-ticker results.

Covers the distinction that caused the most confusion: elapsed span versus time actually
holding shares, and when a derived share count may not be trusted at all.
"""
import unittest, datetime as dt
from tests.fixtures import ROOT  # noqa: F401
import portfolio as P


def T(when, ticker, kind, qty, price):
    return P.Transaction(dt.date.fromisoformat(when), ticker, kind, qty, price,
                         (-qty * price if kind == "buy" else qty * price), 0.0)


class HoldingTimeline(unittest.TestCase):
    def test_continuous_hold_counts_every_day(self):
        txns = [T("2024-01-01", "X", "buy", 10, 100)]
        days, eps = P._holding_timeline(txns, 10, dt.date(2024, 1, 31))
        self.assertEqual(days, 30)
        self.assertEqual(eps, 1)

    def test_gap_between_episodes_is_not_counted(self):
        # Sold out in January, bought back in June: the five idle months must not count
        # as time held. This is why MAZDOCK read 41 months when it had held for 19.
        txns = [T("2024-01-01", "X", "buy", 10, 100),
                T("2024-01-31", "X", "sell", 10, 120),
                T("2024-06-30", "X", "buy", 5, 130)]
        days, eps = P._holding_timeline(txns, 5, dt.date(2024, 7, 30))
        self.assertEqual(days, 30 + 30)      # Jan only, plus a month since re-entry
        self.assertEqual(eps, 2)

    def test_two_buys_on_the_same_day_are_one_episode(self):
        txns = [T("2024-01-01", "X", "buy", 5, 100), T("2024-01-01", "X", "buy", 5, 101)]
        _, eps = P._holding_timeline(txns, 10, dt.date(2024, 1, 10))
        self.assertEqual(eps, 1)

    def test_selling_more_than_held_makes_the_count_untrustworthy(self):
        # A split leaves pre-split quantities in the order history, so sells exceed buys.
        # Guessing here put a "re-entered" flag on positions that were never sold.
        txns = [T("2024-01-01", "X", "buy", 50, 100), T("2024-06-01", "X", "sell", 100, 60)]
        days, eps = P._holding_timeline(txns, 0, dt.date(2024, 12, 31))
        self.assertIsNone(days)
        self.assertIsNone(eps)

    def test_final_count_disagreeing_with_broker_is_untrustworthy(self):
        txns = [T("2024-01-01", "X", "buy", 10, 100)]
        days, eps = P._holding_timeline(txns, 40, dt.date(2024, 12, 31))  # broker says 40
        self.assertIsNone(days)
        self.assertIsNone(eps)

    def test_closed_position_stops_counting_at_the_sale(self):
        txns = [T("2024-01-01", "X", "buy", 10, 100), T("2024-01-11", "X", "sell", 10, 110)]
        days, eps = P._holding_timeline(txns, 0, dt.date(2024, 12, 31))
        self.assertEqual(days, 10)
        self.assertEqual(eps, 1)


class TickerResultProperties(unittest.TestCase):
    def _mk(self, **kw):
        base = dict(ticker="X", xirr=0.1, note=None, invested=100.0, proceeds=0.0,
                    dividends=0.0, market_value=150.0, open_quantity=1.0,
                    first_activity=dt.date(2024, 1, 1), last_activity=dt.date(2024, 6, 1),
                    n_flows=2)
        base.update(kw)
        return P.TickerResult(**base)

    def test_net_profit_includes_proceeds_and_dividends(self):
        r = self._mk(invested=100.0, proceeds=40.0, dividends=5.0, market_value=80.0)
        self.assertAlmostEqual(r.net_profit, 25.0)

    def test_simple_return_is_none_without_investment(self):
        self.assertIsNone(self._mk(invested=0.0).simple_return)

    def test_re_entered_needs_more_than_one_episode(self):
        self.assertFalse(self._mk(episodes=1).re_entered)
        self.assertTrue(self._mk(episodes=3).re_entered)

    def test_re_entered_is_false_when_unknowable(self):
        # None means "cannot tell", which must not read as "yes".
        self.assertFalse(self._mk(episodes=None).re_entered)

    def test_holding_days_is_elapsed_span(self):
        self.assertEqual(self._mk().holding_days, 152)


class Analyse(unittest.TestCase):
    def test_terminal_value_uses_broker_quantity_not_derived(self):
        # Splits make derived quantities wrong; XIRR must still be right because it is
        # cash-flow driven and marks the position at the broker's own share count.
        txns = [T("2024-01-01", "X", "buy", 100, 10.0)]
        pos = {"X": P.Position("X", 200, 6.0)}          # 1:2 split, same total value
        per, ov = P.analyse(txns, pos, as_of=dt.date(2025, 1, 1))
        self.assertAlmostEqual(per[0].market_value, 1200.0)
        self.assertGreater(per[0].xirr, 0)

    def test_dividends_flow_into_the_rate(self):
        a = [T("2024-01-01", "X", "buy", 10, 100)]
        b = a + [P.Transaction(dt.date(2024, 6, 1), "X", "dividend", 0, 0, 50.0, 0.0)]
        pos = {"X": P.Position("X", 10, 100)}
        _, no_div = P.analyse(a, pos, as_of=dt.date(2025, 1, 1))
        _, with_div = P.analyse(b, pos, as_of=dt.date(2025, 1, 1))
        self.assertGreater(with_div.xirr, no_div.xirr)


if __name__ == "__main__":
    unittest.main()
