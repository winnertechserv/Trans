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


def yahoo_symbol(ticker, market, exchange=None):
    """Yahoo Finance symbol for a holding — used by the research integration.
    NSE listings take .NS, BSE takes .BO; US symbols are unsuffixed."""
    if get(market)["key"] != "in":
        return ticker
    return f"{ticker}.{'BO' if (exchange or '').upper() == 'BSE' else 'NS'}"


if __name__ == "__main__":
    for m in all_markets():
        print(f"  {m['flag']} {m['label']:6} broker={m['broker']:10} {m['currency']}")
    print("  yahoo:", yahoo_symbol("MSFT", "us"), yahoo_symbol("DIXON", "in", "BSE"),
          yahoo_symbol("ARVIND", "in", "NSE"))
