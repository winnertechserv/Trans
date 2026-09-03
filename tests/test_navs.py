# SPDX-License-Identifier: Apache-2.0
"""AMFI NAV matching.

The rule under test throughout: a NAV attached to the wrong fund is worse than a stale
one, because a stale price is visibly stale and a wrong one is not. Everything here
checks that ambiguity is refused rather than resolved.
"""
import unittest
from tests.fixtures import TempDB, ROOT  # noqa: F401
import navs

# Real AMFI shape: code;isin;isin2;name;plan;option;nav;date
FEED = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date

135762;INF846K01K35;-;Axis Small Cap Fund;Direct Plan;Growth Option;137.9200;03-Sep-2026
135763;INF846K01K42;-;Axis Small Cap Fund;Direct Plan;IDCW Option;44.1000;03-Sep-2026
135764;INF846K01K50;-;Axis Small Cap Fund;Regular Plan;Growth Option;120.1000;03-Sep-2026
120503;INF879O01027;-;Parag Parikh Flexi Cap Fund;Direct Plan;Growth;90.5000;02-Sep-2026
100001;-;-;Some Fund Without An ISIN;Direct Plan;Growth Option;10.0000;03-Sep-2026
100002;INF000000001;-;Broken NAV Fund;Direct Plan;Growth Option;not-a-number;03-Sep-2026
"""


class Parsing(unittest.TestCase):
    def setUp(self):
        self.schemes = navs.parse(FEED)

    def test_rows_without_an_isin_are_dropped(self):
        self.assertNotIn("Some Fund Without An ISIN", [s["name"] for s in self.schemes])

    def test_unparseable_nav_is_dropped_not_zeroed(self):
        self.assertNotIn("Broken NAV Fund", [s["name"] for s in self.schemes])

    def test_date_is_normalised(self):
        s = navs.by_isin(self.schemes)["INF846K01K35"]
        self.assertEqual(s["date"], "2026-09-03")

    def test_a_bad_date_yields_none_rather_than_a_wrong_one(self):
        self.assertIsNone(navs._date("not a date"))
        self.assertIsNone(navs._date(None))

    def test_header_and_blank_lines_survive(self):
        self.assertEqual(len(self.schemes), 4)


class NameMatching(unittest.TestCase):
    def setUp(self):
        self.schemes = navs.parse(FEED)

    def test_matches_across_different_spellings(self):
        # Paytm writes "Direct-Growth"; AMFI writes "Direct Plan / Growth Option".
        s, why = navs.resolve_name(self.schemes, "Axis Small Cap Fund Direct-Growth")
        self.assertIsNotNone(s)
        self.assertEqual(s["isin"], "INF846K01K35")

    def test_ignores_the_regular_plan(self):
        s, _ = navs.resolve_name(self.schemes, "Axis Small Cap Fund Direct-Growth")
        self.assertIn("Direct", s["plan"])

    def test_ignores_the_idcw_option(self):
        # IDCW pays out, so its NAV is lower. Marking a growth holding at it would
        # understate the position permanently.
        s, _ = navs.resolve_name(self.schemes, "Axis Small Cap Fund Direct-Growth")
        self.assertNotIn("IDCW", s["opt"].upper())

    def test_word_order_does_not_matter(self):
        # "HDFC Index Fund - BSE Sensex Plan" vs AMFI's "HDFC BSE Sensex Index Fund".
        s, _ = navs.resolve_name(self.schemes, "Small Cap Axis Fund Direct Growth")
        self.assertIsNotNone(s)

    def test_a_name_that_matches_nothing_is_refused(self):
        s, why = navs.resolve_name(self.schemes, "Nonexistent Wonder Fund Direct-Growth")
        self.assertIsNone(s)
        self.assertIn("no Direct/Growth scheme", why)

    def test_an_extra_word_prevents_a_match_rather_than_forcing_one(self):
        # Token sets must be equal. "Axis Small Cap Momentum Fund" is a different fund.
        s, _ = navs.resolve_name(self.schemes, "Axis Small Cap Momentum Fund Direct-Growth")
        self.assertIsNone(s)

    def test_an_empty_name_is_refused(self):
        s, why = navs.resolve_name(self.schemes, "")
        self.assertIsNone(s)

    def test_ambiguity_is_reported_not_resolved(self):
        feed = FEED + ("999;INF999999999;-;Axis Small Cap Fund;Direct Plan;"
                       "Growth Option;99.0000;03-Sep-2026\n")
        s, why = navs.resolve_name(navs.parse(feed), "Axis Small Cap Fund Direct-Growth")
        self.assertIsNone(s)
        self.assertIn("ambiguous", why)


class Updating(unittest.TestCase):
    def test_a_holding_keyed_by_isin_matches_directly(self):
        with TempDB() as db:
            db.position("INF846K01K35", 10, 100.0, broker="zerodha", currency="INR",
                        asset="mf", asof="2026-08-01")
            rep = navs.update(db.conn, navs.parse(FEED))
            self.assertEqual(rep[0]["status"], "updated")
            self.assertEqual(rep[0]["detail"], "isin on the holding")
            self.assertAlmostEqual(rep[0]["new"], 137.92)

    def test_a_paytm_holding_matches_on_its_stored_name(self):
        with TempDB() as db:
            db.position("PM123-AXIS0000", 10, 100.0, broker="paytm", currency="INR",
                        asset="mf", asof="2026-08-01")
            db.name("PM123-AXIS0000", "Axis Small Cap Fund Direct-Growth")
            rep = navs.update(db.conn, navs.parse(FEED))
            self.assertEqual(rep[0]["status"], "updated")
            self.assertEqual(rep[0]["detail"], "matched on scheme name")

    def test_an_unmatched_fund_is_skipped_and_reported(self):
        with TempDB() as db:
            db.position("PM999-NOPE0000", 10, 100.0, broker="paytm", currency="INR",
                        asset="mf", asof="2026-08-01")
            db.name("PM999-NOPE0000", "Totally Unknown Fund Direct-Growth")
            rep = navs.update(db.conn, navs.parse(FEED))
            self.assertEqual(rep[0]["status"], "skipped")
            self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0], 0)

    def test_dry_run_writes_nothing(self):
        with TempDB() as db:
            db.position("INF846K01K35", 10, 100.0, broker="zerodha", currency="INR",
                        asset="mf", asof="2026-08-01")
            navs.update(db.conn, navs.parse(FEED), dry_run=True)
            self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0], 0)

    def test_equities_are_left_alone(self):
        with TempDB() as db:
            db.position("RELIANCE", 10, 100.0, broker="zerodha", currency="INR",
                        asset="equity", asof="2026-08-01")
            self.assertEqual(navs.update(db.conn, navs.parse(FEED)), [])


class PricingUsesTheFreshest(unittest.TestCase):
    def test_a_newer_quote_marks_the_position(self):
        with TempDB() as db:
            db.position("INF846K01K35", 10, 100.0, broker="zerodha", currency="INR",
                        asset="mf", asof="2026-08-01")
            navs.update(db.conn, navs.parse(FEED))
            _, ov = db.results("in")
            self.assertAlmostEqual(ov["market_value"], 1379.20)   # 10 x AMFI NAV

    def test_a_stale_quote_never_overrides_a_fresher_broker_price(self):
        # The broker synced today; the quote is from last month. The broker wins.
        with TempDB() as db:
            db.position("INF846K01K35", 10, 200.0, broker="zerodha", currency="INR",
                        asset="mf", asof="2026-12-31")
            navs.update(db.conn, navs.parse(FEED))
            _, ov = db.results("in")
            self.assertAlmostEqual(ov["market_value"], 2000.0)


class PriceStaleness(unittest.TestCase):
    def test_reports_the_oldest_not_the_newest(self):
        # One fund priced today must not make a month-old fund look current.
        import analytics as A
        with TempDB() as db:
            db.position("A", 1, 10.0, asset="mf", asof="2026-01-01")
            db.position("B", 1, 10.0, asset="mf", asof="2026-09-01")
            rows = A.price_asof(db.conn, "us")
            mf = next(r for r in rows if r["asset"] == "mf")
            self.assertEqual(mf["oldest"], "2026-01-01")
            self.assertEqual(mf["newest"], "2026-09-01")

    def test_worst_is_surfaced_on_health(self):
        import analytics as A
        with TempDB() as db:
            db.buy("A", "2026-09-01", 1, 10.0)
            db.position("A", 1, 10.0, asof="2026-01-01")
            h = A.health(db.conn, "us")
            self.assertGreater(h["price_days_stale"], 200)


if __name__ == "__main__":
    unittest.main()
