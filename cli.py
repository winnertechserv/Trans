#!/usr/bin/env python3
"""XIRR report for a trade + dividend history.

    python3 cli.py --transactions transactions.csv --positions positions.csv
    python3 cli.py -t txns.csv -p pos.csv --json out.json
"""
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from portfolio import TickerResult, analyse, load_positions, load_transactions


def _money(v: float) -> str:
    return f"{v:>14,.2f}"


def _pct(v: float | None) -> str:
    if v is None:
        return "        n/a"
    return f"{v * 100:>+10.2f}%"


def render(results: list[TickerResult], overall: TickerResult, as_of: date) -> str:
    lines: list[str] = []
    w = max([len(r.ticker) for r in results] + [len(overall.ticker), 6])

    header = (
        f"{'TICKER'.ljust(w)}  {'XIRR':>11}  {'INVESTED':>14}  {'PROCEEDS':>14}  "
        f"{'DIVIDENDS':>14}  {'MKT VALUE':>14}  {'NET P/L':>14}  {'SIMPLE':>11}  STATUS"
    )
    lines.append("")
    lines.append(f"  Portfolio XIRR report - as of {as_of.isoformat()}")
    lines.append("")
    lines.append("  " + header)
    lines.append("  " + "-" * len(header))

    for r in results:
        status = "open" if r.is_open else "closed"
        if r.xirr is None and r.note:
            status += "  (" + r.note.split(";")[0][:38] + ")"
        lines.append(
            "  "
            + f"{r.ticker.ljust(w)}  {_pct(r.xirr)}  {_money(r.invested)}  "
            f"{_money(r.proceeds)}  {_money(r.dividends)}  {_money(r.market_value)}  "
            f"{_money(r.net_profit)}  {_pct(r.simple_return)}  {status}"
        )

    lines.append("  " + "-" * len(header))
    lines.append(
        "  "
        + f"{overall.ticker.ljust(w)}  {_pct(overall.xirr)}  {_money(overall.invested)}  "
        f"{_money(overall.proceeds)}  {_money(overall.dividends)}  "
        f"{_money(overall.market_value)}  {_money(overall.net_profit)}  "
        f"{_pct(overall.simple_return)}"
    )
    lines.append("")

    span = overall.holding_days
    if span:
        lines.append(f"  Span: {overall.first_activity} -> {overall.last_activity}  ({span} days, {span / 365:.2f} yrs)")
    lines.append(f"  Tickers: {len(results)}   Cash flows: {overall.n_flows}")
    if overall.xirr is not None and overall.simple_return is not None:
        lines.append(
            f"  Overall XIRR {overall.xirr * 100:.2f}%/yr vs simple return "
            f"{overall.simple_return * 100:.2f}% over the full span."
        )
    lines.append("")
    lines.append("  XIRR is money-weighted and annualised on a 365-day basis (matches Excel XIRR).")
    lines.append("  Open positions are closed out at current market value as a terminal inflow.")
    lines.append("")
    return "\n".join(lines)


def to_dict(r: TickerResult) -> dict:
    return {
        "ticker": r.ticker,
        "xirr": r.xirr,
        "xirr_pct": None if r.xirr is None else round(r.xirr * 100, 4),
        "note": r.note,
        "invested": round(r.invested, 2),
        "proceeds": round(r.proceeds, 2),
        "dividends": round(r.dividends, 2),
        "market_value": round(r.market_value, 2),
        "net_profit": round(r.net_profit, 2),
        "simple_return_pct": None if r.simple_return is None else round(r.simple_return * 100, 4),
        "open_quantity": r.open_quantity,
        "is_open": r.is_open,
        "first_activity": r.first_activity.isoformat() if r.first_activity else None,
        "last_activity": r.last_activity.isoformat() if r.last_activity else None,
        "n_flows": r.n_flows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Per-ticker and overall XIRR from a trade history.")
    ap.add_argument("-t", "--transactions", required=True, help="CSV or JSON of trades + dividends")
    ap.add_argument("-p", "--positions", help="CSV or JSON of current holdings (ticker, quantity, price)")
    ap.add_argument("--as-of", help="Valuation date (YYYY-MM-DD); defaults to today")
    ap.add_argument("--json", dest="json_out", help="Also write results to this JSON file")
    ap.add_argument("--min-invested", type=float, default=0.0, help="Hide tickers below this invested amount")
    args = ap.parse_args(argv)

    as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of else date.today()

    try:
        txns = load_transactions(args.transactions)
        positions = load_positions(args.positions) if args.positions else {}
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if not txns:
        print("error: no transactions found", file=sys.stderr)
        return 2

    results, overall = analyse(txns, positions, as_of=as_of)
    if args.min_invested:
        results = [r for r in results if r.invested >= args.min_invested]

    print(render(results, overall, as_of))

    if args.json_out:
        payload = {
            "as_of": as_of.isoformat(),
            "overall": to_dict(overall),
            "tickers": [to_dict(r) for r in results],
        }
        Path(args.json_out).write_text(json.dumps(payload, indent=2))
        print(f"  wrote {args.json_out}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
