# SPDX-License-Identifier: Apache-2.0
"""Symbol handling — where the India data went wrong most often."""
import unittest
from tests.fixtures import ROOT  # noqa: F401  (sets sys.path)
import markets as MK


class BaseSymbol(unittest.TestCase):
    def test_strips_nse_series_codes(self):
        # Holdings carry the series code, the tradebook does not. Until this was applied
        # to both sides, MTARTECH's 18 trades and MTARTECH-BE's position never met.
        self.assertEqual(MK.base_symbol("MTARTECH-BE"), "MTARTECH")
        self.assertEqual(MK.base_symbol("IDEA-BZ"), "IDEA")
        self.assertEqual(MK.base_symbol("XYZ-SM"), "XYZ")

    def test_leaves_hyphenated_names_alone(self):
        # BAJAJ-AUTO is a name, not a series code. Stripping it would invent a ticker.
        self.assertEqual(MK.base_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO")
        self.assertEqual(MK.base_symbol("M&M"), "M&M")

    def test_is_idempotent(self):
        once = MK.base_symbol("MTARTECH-BE")
        self.assertEqual(MK.base_symbol(once), once)

    def test_strips_the_debt_and_gold_bond_series(self):
        # -NC (debt) and -GB (sovereign gold bonds) were missing from the list, so a
        # later sync brought a bond and two SGBs back as new tickers, separate from the
        # holdings already on file.
        self.assertEqual(MK.base_symbol("863IRFC29-NC"), "863IRFC29")
        self.assertEqual(MK.base_symbol("SGBMAY29I-GB"), "SGBMAY29I")
        self.assertEqual(MK.base_symbol("SGBN28VIII-GB"), "SGBN28VIII")

    def test_handles_empty(self):
        self.assertEqual(MK.base_symbol(""), "")
        self.assertEqual(MK.base_symbol(None), "")


class CanonicalSymbol(unittest.TestCase):
    def test_applies_a_shipped_rename(self):
        self.assertEqual(MK.canonical_symbol("GET&D"), "GVT&D")
        self.assertEqual(MK.canonical_symbol("SUVENPHAR"), "COHANCE")

    def test_config_alias_overrides(self):
        self.assertEqual(MK.canonical_symbol("FOO", {"FOO": "BAR"}), "BAR")

    def test_series_code_stripped_before_rename_lookup(self):
        self.assertEqual(MK.canonical_symbol("GET&D-BE"), "GVT&D")

    def test_unknown_symbol_passes_through_uppercased(self):
        self.assertEqual(MK.canonical_symbol("reliance"), "RELIANCE")

    def test_renames_do_not_chain_into_a_cycle(self):
        # A rename whose target is itself a rename key would loop forever if applied
        # repeatedly. Assert the shipped table has no such pair.
        for old, new in MK.RENAMES.items():
            self.assertNotIn(new, MK.RENAMES,
                             f"{old} -> {new} -> {MK.RENAMES.get(new)} chains")


class Demergers(unittest.TestCase):
    def test_vedanta_children_map_to_parent(self):
        for child in ("VAML", "VISL", "VEDPOWER", "VOGL"):
            self.assertEqual(MK.demerged_from(child), "VEDL")

    def test_ordinary_holding_is_not_a_demerger(self):
        self.assertIsNone(MK.demerged_from("RELIANCE"))

    def test_config_can_add_one(self):
        self.assertEqual(MK.demerged_from("KID", {"KID": "PARENT"}), "PARENT")

    def test_no_child_is_also_a_parent(self):
        # Zeroing a child's basis moves it to the parent. If a parent were itself a
        # child, cost would vanish entirely.
        for child, parent in MK.DEMERGERS.items():
            self.assertNotIn(parent, MK.DEMERGERS)


class MarketsAndBrokers(unittest.TestCase):
    def test_india_spans_two_brokers(self):
        self.assertEqual(sorted(MK.brokers_of("in")), ["paytm", "zerodha"])

    def test_us_has_one(self):
        self.assertEqual(MK.brokers_of("us"), ["robinhood"])

    def test_primary_broker_is_for_sync_prompts(self):
        self.assertEqual(MK.broker_of("in"), "zerodha")
        self.assertIn(MK.broker_of("in"), MK.brokers_of("in"))

    def test_every_broker_maps_back_to_exactly_one_market(self):
        for m in MK.all_markets():
            for b in MK.brokers_of(m["key"]):
                self.assertEqual(MK.BROKER_TO_MARKET[b], m["key"])

    def test_unknown_market_falls_back_rather_than_raising(self):
        self.assertEqual(MK.get("nope")["key"], MK.DEFAULT)


class YahooSymbols(unittest.TestCase):
    def test_nse_and_bse_suffixes(self):
        self.assertEqual(MK.yahoo_symbol("DIXON", "in", "NSE"), "DIXON.NS")
        self.assertEqual(MK.yahoo_symbol("DIXON", "in", "BSE"), "DIXON.BO")

    def test_series_code_removed_for_yahoo(self):
        # MTARTECH-BE.NS is not found; MTARTECH.NS is.
        self.assertEqual(MK.yahoo_symbol("MTARTECH-BE", "in", "NSE"), "MTARTECH.NS")

    def test_us_symbols_are_unsuffixed(self):
        self.assertEqual(MK.yahoo_symbol("MSFT", "us"), "MSFT")


if __name__ == "__main__":
    unittest.main()
