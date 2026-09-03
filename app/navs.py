#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Refresh mutual fund NAVs from AMFI.

Fund prices are the weakest thing in the database. Zerodha reports a live NAV only for
funds you still hold there; Paytm reports none at all, so those positions are marked at
the NAV of their last transaction and drift further out of date every week.

AMFI publishes every Indian scheme's NAV daily as one public text file — no key, no
account, nothing personal sent. This fetches it and marks the funds we hold.

    python3 app/navs.py              fetch and update
    python3 app/navs.py --dry-run    show what would change, write nothing

Matching is deliberately strict. A NAV attached to the wrong fund is worse than a stale
one, because a stale price is visibly stale and a wrong one is not. Where a fund carries
an ISIN it is matched on that alone. Where it does not — Paytm statements have no ISIN —
the scheme name must reduce to exactly one Direct/Growth scheme after dropping filler
words, and anything ambiguous is skipped and reported rather than guessed at. A resolved
ISIN is stored so the guess is made once and can be audited afterwards.

Equities are not covered: AMFI is funds only. Stock prices still come from the broker at
sync time.
"""
import os, re, sys, urllib.request, datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as D

URL = "https://www.amfiindia.com/spages/NAVAll.txt"
# Words that appear in almost every scheme name and so carry no identifying signal.
_FILLER = {"FUND", "PLAN", "OPTION", "SCHEME", "DIRECT", "GROWTH", "THE", "OF", "AND"}


def _tokens(s):
    return {t for t in re.findall(r"[A-Z0-9&]+", (s or "").upper()) if t not in _FILLER}


def fetch(url=URL, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "trans (github.com/winnertechserv/trans)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse(raw):
    """-> list of {isin, name, plan, opt, nav, date, tok}. Rows without an ISIN are
    dropped: they cannot be matched safely and are not worth guessing about."""
    out = []
    for line in raw.split("\n"):
        p = line.split(";")
        if len(p) < 8 or p[0].strip() == "Scheme Code":
            continue
        _, isin1, isin2, name, plan, opt, nav, date = [x.strip() for x in p[:8]]
        isin = isin1 if isin1 and isin1 != "-" else (isin2 if isin2 and isin2 != "-" else None)
        if not isin:
            continue
        try:
            nav_f = float(nav)
        except ValueError:
            continue
        out.append({"isin": isin, "name": name, "plan": plan, "opt": opt,
                    "nav": nav_f, "date": _date(date), "tok": _tokens(name)})
    return out


def _date(s):
    """'03-Sep-2026' -> '2026-09-03'. Returns None rather than a wrong date."""
    try:
        return dt.datetime.strptime(s, "%d-%b-%Y").date().isoformat()
    except (ValueError, TypeError):
        return None


def by_isin(schemes):
    return {s["isin"]: s for s in schemes}


def resolve_name(schemes, name):
    """-> (scheme, reason). Only a single unambiguous Direct/Growth match is accepted."""
    want = _tokens(name)
    if not want:
        return None, "no usable words in the name"
    cands = [s for s in schemes
             if s["tok"] == want
             and "DIRECT" in s["plan"].upper()
             and "GROWTH" in s["opt"].upper()
             and "IDCW" not in s["opt"].upper()]
    if len(cands) == 1:
        return cands[0], "matched on scheme name"
    if not cands:
        return None, "no Direct/Growth scheme matches that name"
    return None, f"ambiguous — {len(cands)} schemes match"


def update(c, schemes, dry_run=False):
    """Mark every mutual fund position we can identify. -> list of report rows."""
    isins = by_isin(schemes)
    names = {r["ticker"]: r["text_value"] for r in c.execute(
        "SELECT ticker,text_value FROM fundamentals WHERE metric='name'"
        " AND text_value IS NOT NULL")}
    # An ISIN resolved on a previous run, so a name is only ever matched once.
    known = {r["ticker"]: r["text_value"] for r in c.execute(
        "SELECT ticker,text_value FROM fundamentals WHERE metric='isin'"
        " AND text_value IS NOT NULL")}

    report = []
    for r in c.execute("SELECT ticker,quantity,price,asof,broker FROM positions"
                       " WHERE asset='mf' ORDER BY ticker"):
        tick = r["ticker"]
        label = names.get(tick, tick)
        isin = tick if re.fullmatch(r"INF[0-9A-Z]{9}", tick) else known.get(tick)
        why = "isin on the holding" if isin == tick else (
            "isin resolved earlier" if isin else None)

        s = isins.get(isin) if isin else None
        if s is None:
            s, why = resolve_name(schemes, label)
            if s is not None:
                isin = s["isin"]

        if s is None:
            report.append({"ticker": tick, "name": label, "status": "skipped",
                           "detail": why, "old": r["price"], "new": None})
            continue

        drift = (s["nav"] / r["price"] - 1) * 100 if r["price"] else None
        report.append({"ticker": tick, "name": label, "status": "updated",
                       "detail": why, "old": r["price"], "new": s["nav"],
                       "date": s["date"], "drift": drift, "isin": isin,
                       "matched": f"{s['name']} {s['plan']} {s['opt']}".strip()})
        if dry_run:
            continue
        c.execute("INSERT INTO quotes(ticker,date,price,prev_close) VALUES(?,?,?,NULL)"
                  " ON CONFLICT(ticker,date) DO UPDATE SET price=excluded.price",
                  (tick, s["date"], s["nav"]))
        if isin and tick != isin:
            c.execute("INSERT INTO fundamentals(ticker,asof,metric,value,text_value)"
                      " VALUES(?,?,?,NULL,?) ON CONFLICT(ticker,asof,metric)"
                      " DO UPDATE SET text_value=excluded.text_value",
                      (tick, dt.date.today().isoformat(), "isin", isin))
    if not dry_run:
        c.commit()
    return report


def main():
    dry = "--dry-run" in sys.argv
    print(f"fetching {URL}")
    try:
        raw = fetch()
    except Exception as e:
        print(f"  could not reach AMFI: {type(e).__name__}: {e}")
        print("  fund prices are unchanged; nothing was written.")
        return 1
    schemes = parse(raw)
    print(f"  {len(schemes)} schemes\n")

    c = D.connect()
    rows = update(c, schemes, dry_run=dry)
    if not rows:
        print("no mutual fund positions to price.")
        return 0
    for r in rows:
        if r["status"] == "updated":
            d = f"{r['drift']:+.1f}%" if r["drift"] is not None else "  n/a"
            print(f"  {r['name'][:38]:<40} {r['old']:>9,.2f} -> {r['new']:>9,.2f}"
                  f"  {d:>7}  as of {r['date']}")
            if r["detail"] == "matched on scheme name":
                print(f"      matched by name to: {r['matched'][:66]}")
        else:
            print(f"  {r['name'][:38]:<40} skipped — {r['detail']}")
    n = sum(1 for r in rows if r["status"] == "updated")
    print(f"\n{'would update' if dry else 'updated'} {n} of {len(rows)} fund(s)")
    if dry:
        print("dry run — nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
