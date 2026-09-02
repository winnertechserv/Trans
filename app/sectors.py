"""Sector classification + which financial metrics actually matter per sector.

The point: P/E is meaningless for a REIT (depreciation swamps earnings) and for an
MLP (heavy D&A), P/B is the primary lens for a bank but noise for software, and an
ETF has no company fundamentals at all. So each sector declares its own metric set.
"""

# Ticker -> sector lives in config.json ("ticker_sectors"), not here: it is the user's
# holdings list, and this file is committed. config.example.json ships a generic sample.
# The SECTOR_METRICS rules below are general knowledge and stay in code.
def TICKER_SECTOR():
    try:
        import config as _c
        return {k.upper(): v for k, v in (_c.load().get("ticker_sectors") or {}).items()}
    except Exception:
        return {}

# metric key -> (label, higher_is_better, format)
METRIC_META = {
    "pe":("P/E", False, "x"), "forward_pe":("Fwd P/E", False, "x"),
    "pb":("P/B", False, "x"), "ps":("P/S", False, "x"),
    "peg":("PEG", False, "x"),
    "ev_ebitda":("EV/EBITDA", False, "x"),
    "price_to_ffo":("P/FFO", False, "x"),
    "price_to_nav":("P/NAV", False, "x"),
    "dividend_yield":("Div yield", True, "%"),
    "distribution_yield":("Distribution yield", True, "%"),
    "revenue":("Revenue (TTM)", True, "$"),
    "revenue_growth":("Revenue growth YoY", True, "%"),
    "gross_margin":("Gross margin", True, "%"),
    "operating_margin":("Operating margin", True, "%"),
    "net_margin":("Net margin", True, "%"),
    "fcf_margin":("FCF margin", True, "%"),
    "net_income":("Net income (TTM)", True, "$"),
    "roe":("ROE", True, "%"), "roa":("ROA", True, "%"),
    "roic":("ROIC", True, "%"),
    "debt":("Total debt", False, "$"),
    "debt_to_equity":("Debt / equity", False, "x"),
    "debt_to_ebitda":("Debt / EBITDA", False, "x"),
    "current_ratio":("Current ratio", True, "x"),
    "interest_coverage":("Interest coverage", True, "x"),
    "capex_to_revenue":("Capex / revenue", False, "%"),
    "rnd_to_revenue":("R&D / revenue", True, "%"),
    "inventory_days":("Inventory days", False, "d"),
    "rule_of_40":("Rule of 40", True, "%"),
    "market_cap":("Market cap", True, "$"),
    "expense_ratio":("Expense ratio", False, "%"),
    "nav_per_share":("NAV / share", True, "$"),
    "book_value_per_share":("Book value / share", True, "$"),
    "tier1":("Tier 1 capital", True, "%"),
    "efficiency_ratio":("Efficiency ratio", False, "%"),
    "backlog":("Backlog", True, "$"),
    "high_52":("52w high", True, "$"), "low_52":("52w low", True, "$"),
}

# ordered: most important first — the UI shows the first 6 prominently
SECTOR_METRICS = {
  "semiconductor": ["pe","gross_margin","revenue_growth","capex_to_revenue","rnd_to_revenue",
                    "inventory_days","fcf_margin","debt_to_equity","net_margin","market_cap"],
  "software":      ["ps","revenue_growth","gross_margin","fcf_margin","rule_of_40","operating_margin",
                    "pe","net_margin","debt_to_equity","market_cap"],
  "networking_hw": ["pe","gross_margin","revenue_growth","operating_margin","fcf_margin",
                    "inventory_days","debt_to_equity","market_cap"],
  "consumer_hw":   ["pe","gross_margin","revenue_growth","fcf_margin","roe",
                    "net_margin","debt_to_equity","market_cap"],
  "consumer_disc": ["pe","revenue_growth","gross_margin","operating_margin","inventory_days",
                    "fcf_margin","debt_to_equity","market_cap"],
  "consumer_staples":["pe","revenue_growth","gross_margin","operating_margin","inventory_days",
                    "roic","dividend_yield","market_cap"],
  "auto":          ["pe","gross_margin","revenue_growth","operating_margin","fcf_margin",
                    "debt_to_equity","market_cap"],
  # banks: P/B and ROE are primary; P/E secondary. Leverage ratios are the risk lens.
  "bank":          ["pb","roe","pe","efficiency_ratio","tier1","net_margin",
                    "dividend_yield","book_value_per_share","market_cap"],
  "asset_manager": ["pe","operating_margin","roe","revenue_growth","dividend_yield",
                    "net_margin","market_cap"],
  # BDC: price vs NAV is the whole game; yield + credit quality next.
  "bdc":           ["price_to_nav","dividend_yield","nav_per_share","debt_to_equity",
                    "net_income","market_cap"],
  # MLP: P/E is distorted by D&A. Distribution coverage + leverage matter.
  "midstream_mlp": ["distribution_yield","debt_to_ebitda","ev_ebitda","operating_margin",
                    "revenue_growth","interest_coverage","market_cap"],
  # REIT: P/FFO not P/E, for the same reason.
  "reit_etf":      ["price_to_ffo","dividend_yield","debt_to_ebitda","expense_ratio"],
  "defense":       ["pe","backlog","operating_margin","fcf_margin","dividend_yield",
                    "debt_to_equity","market_cap"],
  "industrial":    ["pe","operating_margin","roic","revenue_growth","fcf_margin",
                    "debt_to_equity","market_cap"],
  "broad_etf":     ["expense_ratio","dividend_yield","pe","market_cap"],
  "thematic_etf":  ["expense_ratio","dividend_yield","pe"],
  "commodity_etf": ["expense_ratio"],
  "crypto":        [],
}

SECTOR_LABEL = {
 "unknown":"Unclassified",
 "semiconductor":"Semiconductors","software":"Software","networking_hw":"Networking / Infra HW",
 "consumer_hw":"Consumer hardware","consumer_disc":"Consumer discretionary",
 "consumer_staples":"Consumer staples","auto":"Autos","bank":"Banks",
 "asset_manager":"Asset managers","bdc":"BDC / private credit","midstream_mlp":"Midstream MLP",
 "reit_etf":"REIT","defense":"Defense","industrial":"Industrials","broad_etf":"Broad index ETF",
 "thematic_etf":"Thematic ETF","commodity_etf":"Commodity ETF","crypto":"Crypto",
}

def sector_of(t): return TICKER_SECTOR().get(t.upper(), "unknown")
SECTOR_METRICS["unknown"] = ["pe","pb","revenue_growth","net_margin","dividend_yield","market_cap"]

def metrics_for(t): return SECTOR_METRICS.get(sector_of(t), SECTOR_METRICS["unknown"])
def label(s): return SECTOR_LABEL.get(s, s)

# which metrics can be pulled/derived from which Robinhood MCP tool
SOURCE_MAP = {
  "get_equity_fundamentals": ["pe","pb","market_cap","dividend_yield","high_52","low_52"],
  "get_financials":          ["revenue","net_income","gross_margin","net_margin","revenue_growth"],
  "get_sec_filing_facts":    ["debt","debt_to_equity","current_ratio","roe","roa","capex_to_revenue",
                              "rnd_to_revenue","inventory_days","fcf_margin","operating_margin",
                              "book_value_per_share","interest_coverage","roic","debt_to_ebitda"],
  "derived":                 ["ps","rule_of_40","price_to_nav","ev_ebitda","price_to_ffo"],
  "manual_or_ai":            ["expense_ratio","tier1","efficiency_ratio","backlog","nav_per_share",
                              "distribution_yield"],
}
if __name__ == "__main__":
    import collections
    by = collections.defaultdict(list)
    for t, s in TICKER_SECTOR().items(): by[s].append(t)
    for s in sorted(by):
        print(f"{label(s):26} {','.join(sorted(by[s])):40} -> {', '.join(SECTOR_METRICS.get(s,[])[:5])}")
