"""Read from SQLite, compute every number the dashboard shows.
Reuses the existing xirr.py / portfolio.py rather than reimplementing the solver.
"""
import os, sys, datetime as dt, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as D, sectors as S, markets as M
from portfolio import Transaction, Position, analyse
import portfolio as P

def _brokers(market):
    return tuple(M.brokers_of(market)) if market else ()


def _bw(br):
    """Leading WHERE clause restricting to a market's brokers."""
    return (" WHERE broker IN (%s)" % ",".join("?" * len(br))) if br else ""


def _ba(br):
    """The same as an additional AND."""
    return (" AND broker IN (%s)" % ",".join("?" * len(br))) if br else ""


def _txns(c, market=None):
    out = []
    br = _brokers(market)
    q = ("SELECT date,ticker,type,quantity,price,amount,fees FROM transactions"
         + _bw(br))
    for r in c.execute(q, br):
        out.append(Transaction.from_row({
            "date": r["date"], "ticker": r["ticker"], "type": r["type"],
            "quantity": r["quantity"] or "", "price": r["price"] or "",
            "amount": r["amount"] if r["amount"] is not None else "", "fees": r["fees"] or 0}))
    return out

def _positions(c, market=None):
    br = _brokers(market)
    q = "SELECT ticker,quantity,price FROM positions" + _bw(br)
    return {r["ticker"]: Position(r["ticker"], r["quantity"], r["price"])
            for r in c.execute(q, br)}

def cost_basis(c, market=None):
    """{ticker: cost of the shares still held}, plus the tickers where it is a guess.

    Two sources, because neither broker gives both halves. Zerodha reports an average
    cost per holding and it is split-adjusted, so it is authoritative. Robinhood reports
    none, but its order history is complete and no US ticker ever sells more shares than
    it bought, so weighted-average cost off the transaction stream is exact there.

    Splits are why the stream cannot be trusted on the India side: order history keeps
    pre-split quantities while sells are post-split, so 39 of 356 tickers sell more
    shares than they appear to own and their per-share average is nonsense. Those are
    exactly the ones Zerodha's own average covers.
    """
    br = _brokers(market)
    have = {}
    q = ("SELECT ticker,quantity,avg_cost FROM positions WHERE avg_cost IS NOT NULL"
         " AND avg_cost>0" + _ba(br))
    for r in c.execute(q, br):
        have[r["ticker"]] = r["quantity"] * r["avg_cost"]

    held = {r["ticker"]: r["quantity"] for r in c.execute(
        "SELECT ticker,quantity FROM positions" + _bw(br),
        br)}
    run = collections.defaultdict(lambda: [0.0, 0.0])   # ticker -> [qty, cost]
    broken = set()
    tq = ("SELECT ticker,type,quantity,price FROM transactions WHERE type IN ('buy','sell')"
          + _ba(br) + " ORDER BY date,id")
    for r in c.execute(tq, br):
        st = run[r["ticker"]]
        q_, p_ = r["quantity"] or 0, r["price"] or 0
        if r["type"] == "buy":
            st[0] += q_; st[1] += q_ * p_
        else:
            if q_ > st[0] + 1e-9:
                broken.add(r["ticker"]); st[0] = 0.0; st[1] = 0.0; continue
            avg = st[1] / st[0] if st[0] else 0.0
            st[0] -= q_; st[1] -= q_ * avg

    out, guessed = {}, []
    for t, qty in held.items():
        if abs(qty) < 1e-9:
            continue
        if t in have:
            out[t] = have[t]
        elif t not in broken and run[t][0] > 1e-9:
            out[t] = run[t][1]
        else:
            out[t] = None          # no broker average and no usable stream
            guessed.append(t)
    return out, guessed, broken


def company_names(c):
    """{ticker: company name}. Stored as a plain `name` metric on fundamentals, because
    that table already carries text values and is refreshed by the same sync — a separate
    table would need its own prompt, its own ingest handler and its own staleness. Empty
    until a fundamentals sync has run, and the UI falls back to the ticker."""
    return {r["ticker"]: r["text_value"] for r in c.execute(
        "SELECT ticker,text_value FROM fundamentals WHERE metric='name'"
        " AND text_value IS NOT NULL AND asof=(SELECT MAX(asof) FROM fundamentals f2"
        "  WHERE f2.ticker=fundamentals.ticker AND f2.metric='name')")}


# A mutual fund has no industry sector, and neither does a gold bond. Leaving them as
# "Unclassified" buried them among 55 equities under an ISIN nobody recognises, which is
# how two funds worth Rs1,66,898 became invisible in a table that was displaying them
# perfectly well. The asset type IS the useful category for these.
ASSET_LABEL = {"mf": "Mutual fund", "sgb": "Sovereign gold bond", "bond": "Bond"}


def asset_types(c, market=None):
    """{ticker: asset}. Positions win; transactions fill in anything already closed.

    Rows built from transaction history carry no asset of their own, so without this a
    mutual fund looks like an equity the moment it gains an order history — which is
    exactly what happened when the MF tradebook landed, putting ISINs back into pickers
    that assume a Yahoo symbol."""
    br = _brokers(market)
    out = {}
    for q in ("SELECT DISTINCT ticker,asset FROM transactions",
              "SELECT ticker,asset FROM positions"):
        for r in c.execute(q + _bw(br), br):
            if r["asset"]:
                out[r["ticker"]] = r["asset"]
    return out


def _no_history_note(asset, has_txns):
    if not has_txns:
        return "no transaction history — import a tradebook for XIRR"
    if asset == "mf":
        return ("mutual fund units — Kite exposes holdings only, with no order history, "
                "so cost basis is the broker average and there is no XIRR")
    if asset in ("sgb", "bond"):
        return ("bought in the primary market, not through the equity tradebook — "
                "cost basis is the broker average, so there is no XIRR")
    return ("no purchase on record (demerger or corporate action) — cost basis is the "
            "broker average, so there is no XIRR")


def results(c, as_of=None, market=None):
    per, overall = analyse(_txns(c, market), _positions(c, market),
                           as_of=as_of or dt.date.today())
    overall_extra = [0.0, 0.0]   # invested, value from holdings-only rows
    rows = []
    for t in per:
        rows.append({
            "ticker": t.ticker, "xirr": t.xirr, "note": t.note,
            "invested": t.invested, "proceeds": t.proceeds, "dividends": t.dividends,
            "market_value": t.market_value, "net_profit": t.net_profit,
            "simple_return": t.simple_return, "open": t.is_open,
            "quantity": t.open_quantity, "n_flows": t.n_flows,
            "first": t.first_activity.isoformat() if t.first_activity else None,
            "last": t.last_activity.isoformat() if t.last_activity else None,
            "holding_days": t.holding_days, "days_held": t.days_held,
            "episodes": t.episodes, "re_entered": t.re_entered,
            "sector": S.sector_of(t.ticker), "sector_label": S.label(S.sector_of(t.ticker)),
        })
    # A market can have holdings but no transaction history — Zerodha's API only serves
    # one day of orders, so until a tradebook CSV is imported there are no cash flows.
    # Fall back to the broker's own average cost so value and P/L are still real; XIRR
    # genuinely cannot be computed without dated flows, and says so rather than showing 0.
    seen = {r["ticker"] for r in rows}
    br = _brokers(market)
    # Once a tradebook is loaded, "import a tradebook" stops being the right explanation.
    # What is left are holdings the equity tradebook structurally cannot contain: bonds
    # and SGBs bought in the primary market, and shares received from a demerger, which
    # arrive with a carved-out cost basis and no purchase of their own.
    has_txns = bool(rows)
    q = ("SELECT ticker,quantity,price,avg_cost,asset,exchange FROM positions"
         + _bw(br))
    for p in c.execute(q, br):
        if p["ticker"] in seen:
            continue
        inv = (p["avg_cost"] or 0) * p["quantity"]
        val = (p["price"] or 0) * p["quantity"]
        rows.append({
            "ticker": p["ticker"], "xirr": None,
            "note": _no_history_note(p["asset"], has_txns),
            "invested": inv, "proceeds": 0.0, "dividends": 0.0, "market_value": val,
            "net_profit": val - inv, "simple_return": (val / inv - 1) if inv else None,
            "open": True, "quantity": p["quantity"], "n_flows": 0,
            "first": None, "last": None, "holding_days": None, "days_held": None,
            "episodes": None, "re_entered": False,
            "asset": p["asset"], "exchange": p["exchange"],
            "sector": S.sector_of(p["ticker"]),
            "sector_label": S.label(S.sector_of(p["ticker"])),
        })
        ov_extra_inv = inv; ov_extra_val = val
        overall_extra[0] += inv; overall_extra[1] += val

    nm = company_names(c)
    basis, no_basis, _broken = cost_basis(c, market)
    short = _unreconciled(c, market)
    assets = asset_types(c, market)
    tot = sum(r["market_value"] for r in rows) or 1
    for r in rows:
        r["weight"] = r["market_value"] / tot
        r["name"] = nm.get(r["ticker"])
        b = basis.get(r["ticker"])
        r["cost_basis"] = b
        r["short_units"] = short.get(r["ticker"])
        r.setdefault("asset", assets.get(r["ticker"], "equity"))
        if r["asset"] in ASSET_LABEL:
            r["sector"] = r["asset"]
            r["sector_label"] = ASSET_LABEL[r["asset"]]
        r["unrealized"] = (r["market_value"] - b) if b is not None else None
    # Realised vs unrealised. Cost of the shares still held comes from cost_basis(); the
    # cost of everything already sold is then whatever is left of lifetime spend, so
    # realised = sold - (invested - still held). The two halves plus dividends reconstruct
    # net profit exactly, which is the check that this is not double counting.
    held_cost = sum(v for v in basis.values() if v is not None)
    xirr_open, open_note = _xirr_open(c, market, as_of or dt.date.today())

    inv_t = overall.invested + overall_extra[0]
    val_t = overall.market_value + overall_extra[1]
    net_t = overall.proceeds + overall.dividends + val_t - inv_t
    realized_t = overall.proceeds - inv_t + held_cost
    ov = {"xirr": overall.xirr, "invested": inv_t, "proceeds": overall.proceeds,
          "dividends": overall.dividends, "market_value": val_t,
          "net_profit": net_t,
          "cost_basis": held_cost,                 # what the shares still held cost
          "unrealized": val_t - held_cost,         # paper gain on those
          "realized": realized_t,                  # booked, from everything sold
          "xirr_open": xirr_open, "xirr_open_note": open_note,
          "no_basis": no_basis, "unreconciled": sorted(short),
          "simple_return": (net_t / inv_t) if inv_t else None,
          "holdings_only": overall_extra[0] > 0 and overall.n_flows == 0,
          "first": overall.first_activity.isoformat() if overall.first_activity else None,
          "last": overall.last_activity.isoformat() if overall.last_activity else None,
          "n_flows": overall.n_flows}
    return rows, ov

def _unreconciled(c, market):
    """Holdings whose order history buys fewer units than the broker says are held.

    Only checked for mutual funds, because they never split: units in equal units out, so
    a shortfall means purchase rows are genuinely absent — a missing year of the Console
    export, typically, since Zerodha files those per financial year. That matters beyond
    a cosmetic gap: realised profit is derived as sold - (invested - still held), so an
    understated `invested` inflates realised by exactly the missing purchase value even
    though the fund has never been sold.

    Equities are deliberately excluded: their derived counts drift for split-adjusted
    tickers as a matter of course, and flagging those would be noise.
    """
    br = _brokers(market)
    held = {r["ticker"]: r["quantity"] for r in c.execute(
        "SELECT ticker,quantity FROM positions WHERE asset='mf'"
        + _ba(br), br)}
    if not held:
        return {}
    got = collections.defaultdict(float)
    for r in c.execute(
            "SELECT ticker,type,quantity FROM transactions WHERE asset='mf'"
            + _ba(br), br):
        got[r["ticker"]] += r["quantity"] if r["type"] == "buy" else -r["quantity"]
    out = {}
    for t, qty in held.items():
        gap = qty - got.get(t, 0.0)
        if gap > 1e-6 and got.get(t, 0.0) > 0:      # has history, but not enough of it
            out[t] = round(gap, 4)
    return out


def _xirr_open(c, market, as_of):
    """XIRR over positions still held — the rate on money actually still in the market.

    Closed round-trips are excluded entirely, which is the point: the headline XIRR is
    dominated by whatever was traded years ago, and it answers a different question from
    "how are the things I own doing".
    """
    br = _brokers(market)
    open_t = {r["ticker"] for r in c.execute(
        "SELECT ticker FROM positions WHERE quantity>0" + _ba(br),
        br)}
    if not open_t:
        return None, "no open positions"
    txns = [t for t in _txns(c, market) if t.ticker in open_t]
    if not txns:
        return None, "open positions have no transaction history"
    per, ov = P.analyse(txns, _positions(c, market), as_of=as_of)
    return ov.xirr, ov.note


def daily_buys(c, days=30, market=None):
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    by_day = collections.OrderedDict()
    br = _brokers(market)
    for r in c.execute("SELECT date,ticker,type,quantity,price,agent,asset FROM transactions"
                       " WHERE type='buy' AND date>=?" + _ba(br)
                       + " ORDER BY date DESC,ticker", (since, *br)):
        amt = (r["quantity"] or 0) * (r["price"] or 0)
        d = by_day.setdefault(r["date"], {"date": r["date"], "total": 0.0, "n": 0, "items": []})
        d["total"] += amt; d["n"] += 1
        d["items"].append({"ticker": r["ticker"], "amount": round(amt, 2),
                           "quantity": r["quantity"], "price": r["price"],
                           "agent": r["agent"], "asset": r["asset"]})
    return list(by_day.values())

def buy_program(c, days=30, market=None):
    """What the recurring engine is actually buying, per ticker per day."""
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    agg = collections.defaultdict(lambda: {"amount": 0.0, "n": 0})
    ndays = set()
    br = _brokers(market)
    for r in c.execute("SELECT date,ticker,quantity,price,agent FROM transactions"
                       " WHERE type='buy' AND date>=?" + _ba(br),
                       (since, *br)):
        a = agg[r["ticker"]]; a["amount"] += (r["quantity"] or 0) * (r["price"] or 0); a["n"] += 1
        a["agent"] = r["agent"]; ndays.add(r["date"])
    tot = sum(v["amount"] for v in agg.values()) or 1
    out = [{"ticker": k, "amount": round(v["amount"], 2), "n": v["n"],
            "per_buy": round(v["amount"] / v["n"], 2), "share": v["amount"] / tot,
            "agent": v.get("agent"), "sector_label": S.label(S.sector_of(k))}
           for k, v in agg.items()]
    out.sort(key=lambda x: -x["amount"])
    return {"days": len(ndays), "total": round(tot, 2),
            "per_day": round(tot / max(len(ndays), 1), 2), "tickers": out}

def dividends(c, market=None):
    by_year = collections.defaultdict(lambda: {"amount": 0.0, "n": 0})
    by_ticker = collections.defaultdict(lambda: {"amount": 0.0, "n": 0})
    by_month = collections.defaultdict(float)
    total = 0.0
    br = _brokers(market)
    for r in c.execute("SELECT date,ticker,amount FROM transactions WHERE type='dividend'"
                       + _ba(br), br):
        a = r["amount"] or 0; total += a
        by_year[r["date"][:4]]["amount"] += a; by_year[r["date"][:4]]["n"] += 1
        by_ticker[r["ticker"]]["amount"] += a; by_ticker[r["ticker"]]["n"] += 1
        by_month[r["date"][:7]] += a
    return {
      "total": round(total, 2),
      "by_year": [{"year": k, **{kk: round(vv, 2) if kk == "amount" else vv for kk, vv in v.items()}}
                  for k, v in sorted(by_year.items())],
      "by_ticker": sorted([{"ticker": k, "amount": round(v["amount"], 2), "n": v["n"]}
                           for k, v in by_ticker.items()], key=lambda x: -x["amount"]),
      "by_month": [{"month": k, "amount": round(v, 2)} for k, v in sorted(by_month.items())],
    }

def allocation(c, market=None):
    rows, ov = results(c, market=market)
    open_rows = [r for r in rows if r["market_value"] > 0]
    tot = sum(r["market_value"] for r in open_rows) or 1
    by_sector = collections.defaultdict(float)
    for r in open_rows: by_sector[r["sector_label"]] += r["market_value"]
    ranked = sorted(open_rows, key=lambda r: -r["market_value"])
    cum, conc = 0.0, {}
    for i, r in enumerate(ranked, 1):
        cum += r["market_value"]
        if i in (1, 3, 5, 10): conc[f"top{i}"] = cum / tot
    tail = [r for r in ranked if r["market_value"] < tot * 0.01]
    return {
      "total": round(tot, 2),
      "positions": [{"ticker": r["ticker"], "value": round(r["market_value"], 2),
                     "weight": r["market_value"] / tot, "sector_label": r["sector_label"]} for r in ranked],
      "by_sector": sorted([{"sector": k, "value": round(v, 2), "weight": v / tot}
                           for k, v in by_sector.items()], key=lambda x: -x["value"]),
      "concentration": conc,
      "tail": {"count": len(tail), "value": round(sum(r["market_value"] for r in tail), 2),
               "weight": sum(r["market_value"] for r in tail) / tot,
               "tickers": [r["ticker"] for r in tail]},
    }

def contributions(c, market=None):
    """Bought, sold and booked P/L per month.

    Realised P/L uses weighted-average cost walked forward over the order stream: on a
    sale, profit is proceeds less the running average cost of the shares going out. That
    is the standard method and it is exact wherever the stream is complete.

    It is NOT computed for tickers that sell more shares than they appear to own, which
    is what a split looks like in order history — the per-share average there is
    meaningless and would invent a loss. Those tickers still contribute to Bought and
    Sold, so the Booked column deliberately sums to less than the headline realised
    figure, and the caller is told how many were skipped rather than the gap being
    silent.
    """
    br = _brokers(market)
    rows = list(c.execute(
        "SELECT date,ticker,type,quantity,price,asset FROM transactions"
        " WHERE type IN ('buy','sell')" + _ba(br)
        + " ORDER BY date,id", br))

    broken = set()
    run = collections.defaultdict(lambda: [0.0, 0.0])
    for r in rows:                                   # first pass: find the unusable ones
        st = run[r["ticker"]]
        q = r["quantity"] or 0
        if r["type"] == "buy":
            st[0] += q; st[1] += q * (r["price"] or 0)
        elif q > st[0] + 1e-9:
            broken.add(r["ticker"]); st[0] = 0.0; st[1] = 0.0
        else:
            avg = st[1] / st[0] if st[0] else 0.0
            st[0] -= q; st[1] -= q * avg

    def _z():
        return {"bought": 0.0, "sold": 0.0, "realized": 0.0}
    by_m = collections.defaultdict(lambda: collections.defaultdict(_z))
    run = collections.defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        m = r["date"][:7]
        a = r["asset"] or "equity"
        q, px = r["quantity"] or 0, r["price"] or 0
        st = run[r["ticker"]]
        if r["type"] == "buy":
            by_m[m][a]["bought"] += q * px
            st[0] += q; st[1] += q * px
        else:
            by_m[m][a]["sold"] += q * px
            if r["ticker"] not in broken and q <= st[0] + 1e-9:
                avg = st[1] / st[0] if st[0] else 0.0
                by_m[m][a]["realized"] += q * px - q * avg
                st[0] -= q; st[1] -= q * avg

    # Broken out by asset because a total hides whichever half you stopped trading:
    # all but Rs500 of this portfolio's mutual fund activity predates the default
    # two-year window, so it looked absent from a figure it was fully inside.
    ser = []
    for k, per in sorted(by_m.items()):
        tot = _z()
        for a in per.values():
            for f in tot:
                tot[f] += a[f]
        ser.append({"month": k, "amount": round(tot["bought"], 2),
                    "bought": round(tot["bought"], 2), "sold": round(tot["sold"], 2),
                    "realized": round(tot["realized"], 2),
                    "by_asset": {a: {f: round(v[f], 2) for f in v} for a, v in per.items()}})
    last12 = sum(x["bought"] for x in ser[-12:])
    seen = sorted({a for x in ser for a in x["by_asset"]})
    return {"series": ser, "trailing_12m": round(last12, 2), "run_rate": round(last12, 2),
            "realized_total": round(sum(x["realized"] for x in ser), 2),
            "realized_skipped": sorted(broken), "assets": seen}

def fundamentals(c, ticker):
    sec = S.sector_of(ticker)
    name = company_names(c).get(ticker)
    wanted = S.metrics_for(ticker)
    latest = {}
    for r in c.execute("SELECT metric,value,text_value,asof FROM fundamentals WHERE ticker=?"
                       " ORDER BY asof", (ticker,)):
        latest[r["metric"]] = {"value": r["value"], "text": r["text_value"], "asof": r["asof"]}
    # derive what can be computed from what we already hold
    def v(k):
        x = latest.get(k); return x["value"] if x and x["value"] is not None else None
    mc, rev, ni = v("market_cap"), v("revenue"), v("net_income")
    if mc and rev and "ps" not in latest:
        latest["ps"] = {"value": mc / rev, "text": None, "asof": latest["market_cap"]["asof"]}
    if mc and v("pb") and "book_value_per_share" not in latest:
        pass  # needs share count; left to the SEC-facts pull
    out = []
    for m in wanted:
        meta = S.METRIC_META.get(m, (m, True, ""))
        v = latest.get(m)
        out.append({"metric": m, "label": meta[0], "fmt": meta[2],
                    "higher_better": meta[1],
                    "value": v["value"] if v else None,
                    "text": v["text"] if v else None,
                    "asof": v["asof"] if v else None,
                    "missing": v is None})
    return {"ticker": ticker, "name": name, "sector": sec, "sector_label": S.label(sec),
            "metrics": out,
            "coverage": sum(1 for m in out if not m["missing"]) / max(len(out), 1)}

def costs(c):
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM token_ledger ORDER BY id DESC LIMIT 200")]
    tot = c.execute("SELECT COALESCE(SUM(cost_usd),0) s, COALESCE(SUM(input_tokens),0) i,"
                    " COALESCE(SUM(output_tokens),0) o FROM token_ledger").fetchone()
    by_src = [dict(r) for r in c.execute(
        "SELECT source, COUNT(*) n, COALESCE(SUM(cost_usd),0) cost,"
        " COALESCE(SUM(input_tokens+output_tokens),0) tokens FROM token_ledger GROUP BY source")]
    return {"entries": rows, "total_cost": round(tot["s"], 6),
            "total_input": tot["i"], "total_output": tot["o"], "by_source": by_src}

def health(c, market=None):
    br = _brokers(market)
    last = c.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 10").fetchone()
    runs = [dict(r) for r in c.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 10")]
    maxd = c.execute("SELECT MAX(date) d FROM transactions"
                     + _bw(br), br).fetchone()["d"]
    stale = (dt.date.today() - dt.date.fromisoformat(maxd)).days if maxd else None
    return {"last_transaction_date": maxd, "days_stale": stale, "runs": runs,
            "n_transactions": c.execute("SELECT COUNT(*) n FROM transactions"
                + _bw(br), br).fetchone()["n"],
            "n_positions": c.execute("SELECT COUNT(*) n FROM positions"
                + _bw(br), br).fetchone()["n"],
            "n_fundamentals": c.execute("SELECT COUNT(DISTINCT ticker) n FROM fundamentals").fetchone()["n"]}

if __name__ == "__main__":
    c = D.connect()
    rows, ov = results(c)
    print(f"overall XIRR {ov['xirr']*100:.2f}%  MV ${ov['market_value']:,.0f}  div ${ov['dividends']:,.2f}")
    print("tickers:", len(rows))
    bp = buy_program(c, 30)
    print(f"buy program: ${bp['per_day']}/day over {bp['days']} days, {len(bp['tickers'])} tickers")
    print("dividends total:", dividends(c)["total"])
    a = allocation(c); print("top10:", f"{a['concentration']['top10']*100:.1f}%", "tail:", a["tail"]["count"])
