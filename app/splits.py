#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Work out which corporate action multiplied a holding's share count.

Order history keeps the share counts as they were on the day. A split or bonus multiplies
what you hold without any transaction recording it, so the tradebook ends up showing more
shares sold than were ever bought. FIFO cannot match those sales against anything, so the
ticker is dropped from booked profit entirely — which is safe, and on this portfolio hid a
third of it.

A split leaves fingerprints, and this solves for the only ratio that fits all of them:

  * after adjusting, FIFO never runs short of shares;
  * the final share count equals what the broker says is held;
  * the remaining shares' average cost matches the broker's own average.

The last one is what makes this trustworthy rather than arithmetic that happens to close.
The broker's average is computed independently of anything here, so a wrong ratio has to
agree with a number it never saw.

    python3 app/splits.py            propose ratios, write nothing
    python3 app/splits.py --write    add the unambiguous ones to config.json

NOTHING is applied without --write, and even then only where exactly one ratio fits. A
wrong ratio silently rewrites realised profit, and unlike a missing figure a wrong one
does not announce itself.
"""
import os, sys, json, datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as D
import config as CFG

# Ratios seen in Indian corporate actions: 1:1 bonus is 2x, 1:2 bonus 1.5x, and so on.
RATIOS = [1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 20, 50, 100]
AVG_TOLERANCE = 0.05          # broker's average cost must agree within 5%


def _rows(c, ticker):
    return list(c.execute(
        "SELECT date,type,quantity,price FROM transactions WHERE ticker=?"
        " ORDER BY date,id", (ticker,)))


def replay(rows, events):
    """FIFO with `events` applied. -> (ok, held_quantity, remaining_cost).

    ok is False the moment a sale exceeds the shares on hand, which is the signal that
    the ratio being tried is wrong (or that there is no ratio).
    """
    lots, applied = [], 0
    for r in rows:
        while applied < len(events) and events[applied]["date"] <= r["date"]:
            f = events[applied]["ratio"]
            for lot in lots:
                lot[0] *= f
                lot[1] /= f
            applied += 1
        q, px = r["quantity"] or 0.0, r["price"] or 0.0
        if r["type"] == "buy":
            lots.append([q, px])
            continue
        if r["type"] != "sell":
            continue
        need = q
        while need > 1e-6 and lots:
            take = min(need, lots[0][0])
            lots[0][0] -= take
            need -= take
            if lots[0][0] <= 1e-6:
                lots.pop(0)
        if need > 1e-6:
            return False, 0.0, 0.0
    # A split can fall after the last trade in a ticker — you stop trading it, then it
    # splits. Those events never come up in the loop above, so apply them here or the
    # final share count will not match what the broker reports and the right ratio gets
    # rejected as wrong.
    while applied < len(events):
        f = events[applied]["ratio"]
        for lot in lots:
            lot[0] *= f
            lot[1] /= f
        applied += 1
    return True, sum(l[0] for l in lots), sum(l[0] * l[1] for l in lots)


def infer(rows, held_qty, broker_avg):
    """-> (ratios_that_fit, candidate_dates). One ratio means it is safe to use."""
    if not any(r["type"] == "buy" for r in rows):
        return [], []
    dates = sorted({r["date"] for r in rows})
    hits = []
    for i, sd in enumerate(dates):
        for ratio in RATIOS:
            ok, q, cost = replay(rows, [{"date": sd, "ratio": ratio}])
            if not ok or abs(q - held_qty) > 0.01:
                continue
            if held_qty > 0 and broker_avg:
                avg = cost / q
                if abs(avg - broker_avg) / broker_avg > AVG_TOLERANCE:
                    continue
            hits.append((sd, ratio))
    return sorted({r for _, r in hits}), sorted({d for d, _ in hits})


def unmatched(c):
    """Tickers whose sales exceed their purchases — the ones FIFO has to skip."""
    out = []
    for r in c.execute("SELECT DISTINCT ticker FROM transactions ORDER BY ticker"):
        t = r["ticker"]
        rows = _rows(c, t)
        ok, _, _ = replay(rows, [])
        if not ok:
            out.append(t)
    return out


def main():
    write = "--write" in sys.argv
    c = D.connect()
    pos = {r["ticker"]: (r["quantity"], r["avg_cost"]) for r in
           c.execute("SELECT ticker,quantity,avg_cost FROM positions")}
    known = CFG.splits()

    todo = [t for t in unmatched(c) if t not in known]
    if not todo:
        print("every holding's sales match its purchases — nothing to infer.")
        return 0

    print(f"{len(todo)} holding(s) sell more shares than they bought.\n"
          "That is what a split or bonus looks like in order history.\n")
    found, ambiguous, none = {}, [], []
    for t in todo:
        rows = _rows(c, t)
        q, avg = pos.get(t, (0.0, None))
        ratios, dates = infer(rows, q, avg)
        if len(ratios) == 1:
            # earliest date that works — a split cannot precede the shares it multiplied
            found[t] = {"date": dates[0], "ratio": ratios[0]}
            check = "and the broker's average cost agrees" if q > 0 else \
                    "share count closes exactly (nothing held, so no average to check)"
            print(f"  {t:<13} {ratios[0]:g}x on or before {dates[0]}   {check}")
        elif ratios:
            ambiguous.append((t, ratios))
        else:
            none.append(t)

    if ambiguous:
        print(f"\n  {len(ambiguous)} ambiguous — several ratios fit equally well, so"
              " picking one would be a guess:")
        for t, rs in ambiguous:
            print(f"     {t:<13} could be {', '.join(f'{r:g}x' for r in rs)}")
    if none:
        print(f"\n  {len(none)} unexplained by any single ratio — most likely two"
              " corporate actions,\n     a merger with an odd conversion, or purchases"
              " missing from the tradebook:")
        print("     " + ", ".join(none))

    if not found:
        print("\nnothing unambiguous to write.")
        return 0
    if not write:
        print(f"\n{len(found)} ratio(s) determined. Nothing written — re-run with --write"
              " to add them\nto config.json, or add them by hand if you would rather"
              " check each first.")
        return 0

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config.json")
    cfg = json.load(open(path)) if os.path.exists(path) else {}
    cfg.setdefault("splits", {})
    for t, ev in found.items():
        cfg["splits"][t] = [ev]
    with open(path, "w") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    CFG.load(reload=True)
    print(f"\nwrote {len(found)} split(s) to config.json.")
    print("Re-check them against the corporate action if a figure looks wrong; removing"
          " an entry\nputs the holding back to being skipped, which is the safe state.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
