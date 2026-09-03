# SPDX-License-Identifier: Apache-2.0
"""The Paytm statement parser.

Built from text laid out the way `pdftotext -layout` produces it. Each case here is a
layout that appeared in a real statement and broke an earlier version of the parser.
"""
import unittest
from tests.fixtures import ROOT  # noqa: F401
import paytm


HEAD = """
Transaction Summary (for FY 2024-25)
  Investment Activity
  Fresh Purchase                                                        + %s
  Withdrawal                                                            - %s
  Investment Transaction Summary
  Date*    Mutual Fund Scheme Name        Folio No   Type   Units   NAV   Amount   Status
"""


def statement(body, buy="0.00", sell="0.00"):
    return (HEAD % (f"₹{buy}", f"₹{sell}")) + body


# The common case: date, name and type on one line, money on the next.
SHORT_NAME = """
  02 May       Demo Flexi Cap Fund Direct-Growth                 Purchase -
                                          10613012      114.976   ₹87.3790   ₹10,047.00   Confirmed
  2024         (Equity - Flexi Cap)                              SIP
"""

# A long name wraps: it starts above the date line and finishes on the money line.
WRAPPED_NAME = """
               Demo Very Long Flexi Cap Fund Direct-
  20 Mar                                                         Purchase -
               Growth                     10613012      56.951    ₹87.7908   ₹5,000.00    Confirmed
  2025                                                           SIP
  """

# A withdrawal puts the type between folio and units.
WITHDRAWAL = """
  06 Feb       Demo Digital Fund Direct-Growth
               8133649    Withdraw         10,941.229   ₹53.3874   ₹584,117.94  Confirmed
  2025
"""


class Layouts(unittest.TestCase):
    def test_short_name_on_one_line(self):
        rows, meta = paytm.parse_text(statement(SHORT_NAME, "10047.00"))
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["date"], "2024-05-02")
        self.assertEqual(r["folio"], "10613012")
        self.assertEqual(r["type"], "buy")
        self.assertAlmostEqual(r["units"], 114.976)
        self.assertAlmostEqual(r["amount"], 10047.00)
        self.assertIn("Demo Flexi Cap Fund", r["scheme"])

    def test_wrapped_name_is_reassembled_not_duplicated(self):
        rows, _ = paytm.parse_text(statement(WRAPPED_NAME, "5000.00"))
        self.assertEqual(len(rows), 1)
        scheme = rows[0]["scheme"]
        self.assertIn("Demo Very Long Flexi Cap Fund", scheme)
        # "Growth" must appear once. Concatenating both fragments blindly produced
        # "Axis Small Cap Fund Direct-Growth Axis Small Cap Fund".
        self.assertEqual(scheme.count("Flexi Cap"), 1)

    def test_withdrawal_is_read_as_a_sale(self):
        rows, _ = paytm.parse_text(statement(WITHDRAWAL, "0.00", "584117.94"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["type"], "sell")
        self.assertAlmostEqual(rows[0]["amount"], 584117.94)


class DateHandling(unittest.TestCase):
    def test_year_comes_from_the_financial_year_not_from_a_number_in_the_row(self):
        # r"\b20\d\d\b" happily matches "2073" inside a unit count of 2073.456, which
        # produced transactions dated 2073.
        body = """
  11 Mar       Demo Fund Direct-Growth                           Purchase -
                                          10613012      2073.456  ₹83.2603   ₹5,074.00    Confirmed
  2025         (Equity - Flexi Cap)                              SIP
"""
        rows, meta = paytm.parse_text(statement(body, "5074.00"))
        self.assertEqual(rows[0]["date"][:4], "2025")     # Mar in FY2024-25
        self.assertLessEqual(meta["year_corrected"], 1)

    def test_april_belongs_to_the_opening_year_january_to_the_next(self):
        body = """
  05 Apr       Demo Fund Direct-Growth                           Purchase -
                                          10613012      10.0      ₹100.00    ₹1,000.00    Confirmed
  2024                                                           SIP
  15 Jan       Demo Fund Direct-Growth                           Purchase -
                                          10613012      10.0      ₹100.00    ₹1,000.00    Confirmed
  2025                                                           SIP
"""
        rows, _ = paytm.parse_text(statement(body, "2000.00"))
        got = sorted(r["date"] for r in rows)
        self.assertEqual(got, ["2024-04-05", "2025-01-15"])

    def test_every_parsed_date_falls_inside_the_statements_year(self):
        rows, meta = paytm.parse_text(statement(SHORT_NAME, "10047.00"))
        lo, hi = "2024-04-01", "2025-03-31"
        for r in rows:
            self.assertTrue(lo <= r["date"] <= hi, r["date"])


class Reconciliation(unittest.TestCase):
    def test_check_passes_when_totals_agree(self):
        rows, meta = paytm.parse_text(statement(SHORT_NAME, "10047.00"))
        ok, detail = paytm.check(rows, meta)
        self.assertTrue(ok)
        self.assertAlmostEqual(detail["buy"], 10047.00)

    def test_check_fails_when_a_row_is_missing(self):
        # The statement claims more than the rows add up to. This is what a page-break
        # parsing failure looks like, and importing it anyway inflates realised profit.
        rows, meta = paytm.parse_text(statement(SHORT_NAME, "99999.00"))
        ok, _ = paytm.check(rows, meta)
        self.assertFalse(ok)

    def test_unconfirmed_rows_do_not_count_towards_the_total(self):
        body = SHORT_NAME.replace("Confirmed", "Failed")
        rows, meta = paytm.parse_text(statement(body, "0.00"))
        ok, detail = paytm.check(rows, meta)
        self.assertEqual(detail["rows"], 0)
        self.assertTrue(ok)


class SchemeNames(unittest.TestCase):
    def test_variants_of_one_name_are_folded_together(self):
        rows = [{"folio": "1", "scheme": "Demo Fund Direct-Growth"},
                {"folio": "1", "scheme": "Demo Fund Direct Growth"},
                {"folio": "1", "scheme": "Demo Fund Direct-Growth"}]
        paytm.canonicalise(rows)
        self.assertEqual(len({r["scheme"] for r in rows}), 1)

    def test_a_truncated_name_folds_into_its_longer_form(self):
        rows = [{"folio": "1", "scheme": "Invesco India ELSS Tax Saver Fund"},
                {"folio": "1", "scheme": "Invesco India ELSS Tax Saver Fund Direct-Growth"}]
        paytm.canonicalise(rows)
        self.assertEqual(len({r["scheme"] for r in rows}), 1)

    def test_different_schemes_on_one_folio_stay_separate(self):
        # One SBI folio carried three different schemes; merging them by folio hid two.
        rows = [{"folio": "1", "scheme": "SBI Banking & Financial Services Fund"},
                {"folio": "1", "scheme": "SBI Magnum Gilt Fund"},
                {"folio": "1", "scheme": "SBI Money Market Fund"}]
        paytm.canonicalise(rows)
        self.assertEqual(len({r["scheme"] for r in rows}), 3)

    def test_folios_do_not_bleed_into_each_other(self):
        rows = [{"folio": "1", "scheme": "Demo Fund"},
                {"folio": "2", "scheme": "Demo Fund Direct-Growth"}]
        paytm.canonicalise(rows)
        self.assertEqual(rows[0]["scheme"], "Demo Fund")


if __name__ == "__main__":
    unittest.main()
