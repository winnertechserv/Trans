"""Markets are kept strictly separate — never blended, never converted.

Two brokers in two currencies. Summing them would need an FX rate, and currency
movement would then land inside reported returns, which is real but is not stock
performance. So there is deliberately no "All" view: you look at one market at a time,
each in its own currency.
"""

MARKETS = {
    "us": {"key": "us", "label": "US", "flag": "🇺🇸", "broker": "robinhood",
           "brokers": ["robinhood"],
           "currency": "USD", "symbol": "$", "locale": "en-US",
           "benchmark": "SPY", "suffix": ""},
    "in": {"key": "in", "label": "India", "flag": "🇮🇳", "broker": "zerodha",
           "brokers": ["zerodha", "paytm"],
           "currency": "INR", "symbol": "₹", "locale": "en-IN",
           "benchmark": "^NSEI", "suffix": ".NS"},
}
DEFAULT = "us"
BROKER_TO_MARKET = {b: k for k, m in MARKETS.items()
                    for b in (m.get("brokers") or [m["broker"]])}


def get(key):
    return MARKETS.get((key or DEFAULT).lower(), MARKETS[DEFAULT])


def broker_of(key):
    """The market's primary broker — the one its sync prompts talk to."""
    return get(key)["broker"]


def brokers_of(key):
    """Every broker whose holdings belong to this market.

    A country is not one broker: Indian mutual funds sit at Paytm while the demat is at
    Zerodha, and both are rupees in the same portfolio. Reads span all of them; sync
    prompts still target one, since each broker is fetched its own way.
    """
    m = get(key)
    return list(m.get("brokers") or [m["broker"]])


def all_markets():
    return [MARKETS[k] for k in ("us", "in")]


# NSE appends a series code to symbols in some segments (BE = trade-to-trade, SM/ST =
# SME, and others). Zerodha reports these as part of the tradingsymbol, but Yahoo does
# not use them: MTARTECH-BE.NS is not found, MTARTECH.NS is.
NSE_SERIES_SUFFIXES = ("-BE", "-BZ", "-BL", "-SM", "-ST", "-IT", "-GS", "-RR")


def base_symbol(ticker):
    t = (ticker or "").upper()
    for suf in NSE_SERIES_SUFFIXES:
        if t.endswith(suf):
            return t[: -len(suf)]
    return t


# Tickers change when a company is renamed or demerged, and the broker's tradebook keeps
# whatever the symbol was on the trade date. Without a mapping the history splits in two
# and neither half has a complete cost basis: GE T&D India became GE Vernova T&D India in
# 2025, so GET&D holds the buys (+24) and GVT&D holds the rest (+10) of one 34-share
# position. Renames are facts about the market, not about one portfolio, so the ones we
# have confirmed live here; `ticker_aliases` in config.json extends this per user.
RENAMES = {
    # Each of these was confirmed three ways before being added: the old symbol's leftover
    # share count exactly equals what the new symbol sold without ever buying, the last
    # trade under the old name precedes the first under the new one, and the two names
    # describe the same company. Quantity alone is not enough — matching purely on it also
    # paired MINDAIND with GMMPFAUDLR and LTI with NESTLEIND, which is nonsense.
    "GET&D": "GVT&D",            # GE T&D India -> GE Vernova T&D India (2025)
    "ITDCEM": "CEMPRO",          # ITD Cementation India, 190 shares
    "SUVENPHAR": "COHANCE",      # Suven Pharmaceuticals -> Cohance Lifesciences, 81
    "MAHINDCIE": "CIEINDIA",     # Mahindra CIE -> CIE Automotive India, 37
    "MAGMA": "POONAWALLA",       # Magma Fincorp -> Poonawalla Fincorp, 20
    "ORIENTREF": "RHIM",         # Orient Refractories -> RHI Magnesita India, 11
    # These three are certain from the names; their share counts do not tie because a
    # split or bonus intervened. That does not matter to XIRR, which is driven by cash
    # flows, and it matters a great deal to the cost basis, which is otherwise missing
    # entirely. SETFGOLD's 2 units became SBIGETS's 200 on a 1:100 split — exactly 100x.
    "SETFGOLD": "SBIGETS",       # SBI Gold ETF, symbol changed; 1:100 split
    "PHILIPCARB": "PCBL",        # Phillips Carbon Black -> PCBL (2023); split
    "MOTHERSUMI": "MOTHERSON",   # Motherson Sumi -> Samvardhana Motherson; bonus
}


def canonical_symbol(ticker, aliases=None):
    """Base symbol with any rename applied — the form both trades and holdings use."""
    t = base_symbol(ticker)
    if aliases and t in aliases:
        return base_symbol(aliases[t])
    return RENAMES.get(t, t)


def yahoo_symbol(ticker, market, exchange=None):
    """Yahoo Finance symbol for a holding — used by research and fundamentals.
    NSE listings take .NS, BSE takes .BO; US symbols are unsuffixed."""
    if get(market)["key"] != "in":
        return ticker
    return f"{base_symbol(ticker)}.{'BO' if (exchange or '').upper() == 'BSE' else 'NS'}"


if __name__ == "__main__":
    for m in all_markets():
        print(f"  {m['flag']} {m['label']:6} broker={m['broker']:10} {m['currency']}")
    print("  yahoo:", yahoo_symbol("MSFT", "us"), yahoo_symbol("DIXON", "in", "BSE"),
          yahoo_symbol("ARVIND", "in", "NSE"))
    print("  canon:", canonical_symbol("MTARTECH-BE"), canonical_symbol("GET&D"),
          canonical_symbol("RELIANCE"))
