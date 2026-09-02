"""Markets are kept strictly separate — never blended, never converted.

Two brokers in two currencies. Summing them would need an FX rate, and currency
movement would then land inside reported returns, which is real but is not stock
performance. So there is deliberately no "All" view: you look at one market at a time,
each in its own currency.
"""

MARKETS = {
    "us": {"key": "us", "label": "US", "flag": "🇺🇸", "broker": "robinhood",
           "currency": "USD", "symbol": "$", "locale": "en-US",
           "benchmark": "SPY", "suffix": ""},
    "in": {"key": "in", "label": "India", "flag": "🇮🇳", "broker": "zerodha",
           "currency": "INR", "symbol": "₹", "locale": "en-IN",
           "benchmark": "^NSEI", "suffix": ".NS"},
}
DEFAULT = "us"
BROKER_TO_MARKET = {m["broker"]: k for k, m in MARKETS.items()}


def get(key):
    return MARKETS.get((key or DEFAULT).lower(), MARKETS[DEFAULT])


def broker_of(key):
    return get(key)["broker"]


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
    "GET&D": "GVT&D",   # GE T&D India -> GE Vernova T&D India (2025)
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
