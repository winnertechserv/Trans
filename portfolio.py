"""Turn a trade + dividend history into per-ticker and overall XIRR.

Input is deliberately broker-agnostic: a flat transaction list plus a
snapshot of current holdings. Anything that can produce those two tables
(Robinhood MCP, a CSV export, another broker) plugs in unchanged.

XIRR needs no cost-basis method - it is purely cash-flow driven, so FIFO
vs average cost never enters into it. Reinvested dividends also handle
themselves: they show up as a dividend inflow plus a buy outflow.
"""
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Literal

from xirr import CashFlow, XirrError, xirr_or_none

TxnType = Literal["buy", "sell", "dividend", "deposit", "withdrawal"]

_BUY = {"buy", "b", "purchase", "bought"}
_SELL = {"sell", "s", "sale", "sold"}
_DIV = {"dividend", "div", "dividends", "cash_dividend"}
_DEP = {"deposit", "transfer_in", "ach_in"}
_WDR = {"withdrawal", "withdraw", "transfer_out", "ach_out"}


def _parse_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        raise ValueError("empty date")
    # Trim ISO timestamps / trailing Z
    s = s.replace("Z", "").split("T")[0].split(" ")[0]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date: {v!r}")


def _num(v, default: float = 0.0) -> float:
    if v is None or v == "":
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("$", "").replace(",", "")
    if s.startswith("(") and s.endswith(")"):  # (123.45) == -123.45
        s = "-" + s[1:-1]
    if not s:
        return default
    return float(s)


def _norm_type(v) -> TxnType:
    s = str(v).strip().lower().replace(" ", "_")
    if s in _BUY:
        return "buy"
    if s in _SELL:
        return "sell"
    if s in _DIV:
        return "dividend"
    if s in _DEP:
        return "deposit"
    if s in _WDR:
        return "withdrawal"
    raise ValueError(f"unrecognised transaction type: {v!r}")


@dataclass
class Transaction:
    when: date
    ticker: str
    type: TxnType
    quantity: float = 0.0
    price: float = 0.0
    amount: float = 0.0  # signed cash impact; derived if not supplied
    fees: float = 0.0

    @classmethod
    def from_row(cls, row: dict) -> "Transaction":
        low = {str(k).strip().lower(): v for k, v in row.items()}

        def pick(*names, default=None):
            for n in names:
                if n in low and low[n] not in (None, ""):
                    return low[n]
            return default

        ttype = _norm_type(pick("type", "action", "transaction_type", "side"))
        when = _parse_date(pick("date", "when", "settled_at", "executed_at", "created_at"))
        ticker = str(pick("ticker", "symbol", "instrument", default="") or "").strip().upper()
        qty = _num(pick("quantity", "qty", "shares", "units"))
        price = _num(pick("price", "unit_price", "avg_price", "average_price"))
        fees = _num(pick("fees", "fee", "commission"))
        raw_amt = pick("amount", "net_amount", "value", "total")

        if raw_amt is not None:
            amount = _num(raw_amt)
            # Normalise sign by type; tolerate exports that are all-positive.
            mag = abs(amount)
            if ttype in ("buy", "withdrawal"):
                amount = -mag
            elif ttype in ("sell", "dividend", "deposit"):
                amount = mag
        else:
            gross = qty * price
            if ttype == "buy":
                amount = -(gross + fees)
            elif ttype == "sell":
                amount = gross - fees
            else:
                raise ValueError(f"{ttype} row needs an explicit amount: {row!r}")

        if ttype in ("buy", "sell", "dividend") and not ticker:
            raise ValueError(f"{ttype} row needs a ticker: {row!r}")

        return cls(when, ticker, ttype, qty, price, amount, fees)


@dataclass
class Position:
    ticker: str
    quantity: float
    price: float  # current mark

    @property
    def market_value(self) -> float:
        return self.quantity * self.price


@dataclass
class TickerResult:
    ticker: str
    xirr: float | None
    note: str | None
    invested: float          # total cash paid in (positive number)
    proceeds: float          # total cash from sales
    dividends: float
    market_value: float      # current value of remaining shares
    open_quantity: float
    first_activity: date | None
    last_activity: date | None
    n_flows: int
    days_held: int | None = None   # days actually owning shares, not elapsed span
    episodes: int | None = None    # times built up from nothing; None if unknowable

    @property
    def net_profit(self) -> float:
        return self.proceeds + self.dividends + self.market_value - self.invested

    @property
    def simple_return(self) -> float | None:
        if self.invested <= 0:
            return None
        return self.net_profit / self.invested

    @property
    def is_open(self) -> bool:
        return abs(self.open_quantity) > 1e-9

    @property
    def re_entered(self) -> bool:
        """Sold out completely and bought back in at least once. XIRR weights flows by
        time, exponentially, so for these the rate is set by whichever episode came
        first and is close to blind to the capital at risk today."""
        return bool(self.episodes and self.episodes > 1)

    @property
    def holding_days(self) -> int | None:
        if not self.first_activity or not self.last_activity:
            return None
        return (self.last_activity - self.first_activity).days


def load_transactions(path: str | Path) -> list[Transaction]:
    path = Path(path)
    text = path.read_text()
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("transactions") or data.get("data") or []
        rows = list(data)
    else:
        rows = list(csv.DictReader(text.splitlines()))
    out: list[Transaction] = []
    for i, row in enumerate(rows, start=2):
        try:
            out.append(Transaction.from_row(row))
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"{path.name} line {i}: {e}") from e
    return out


def load_positions(path: str | Path) -> dict[str, Position]:
    path = Path(path)
    text = path.read_text()
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("positions") or data.get("data") or []
        rows = list(data)
    else:
        rows = list(csv.DictReader(text.splitlines()))

    out: dict[str, Position] = {}
    for row in rows:
        low = {str(k).strip().lower(): v for k, v in row.items()}

        def pick(*names, default=None):
            for n in names:
                if n in low and low[n] not in (None, ""):
                    return low[n]
            return default

        t = str(pick("ticker", "symbol", "instrument", default="") or "").strip().upper()
        if not t:
            continue
        out[t] = Position(
            ticker=t,
            quantity=_num(pick("quantity", "qty", "shares", "units")),
            price=_num(pick("price", "current_price", "mark", "last_price", "market_price")),
        )
    return out


def _holding_timeline(txns, open_qty, as_of):
    """Days actually holding shares, and how many separate times the position was built
    from nothing.

    Elapsed first-to-last overstates the holding period for anything you exit and
    re-enter. MAZDOCK spans 41 months but was flat for 21 of them across two gaps; an
    annualised rate quoted against the elapsed span reads as one long steady hold when
    it was three short trades.

    Returns (None, None) when the derived share count cannot be trusted, which happens
    for anything that split or changed ADR ratio: order history keeps pre-split
    quantities while sells are post-split, so the running count drifts and can go
    negative. BEL sells 165 shares it never appears to have bought and lands at -158,
    which read as two exits and a re-entry when it is one steady accumulation. Two
    signals catch it — a negative excursion, and a final derived count that disagrees
    with what the broker says is held. Guessing here would put a wrong "re-entered" flag
    on a position that was never sold, so the honest answer is to say nothing.
    """
    days = 0
    episodes = 0
    qty = 0.0
    trustworthy = True
    prev = txns[0].when
    for t in txns:
        if qty > 1e-9:
            days += (t.when - prev).days
        if t.type == "buy":
            if qty <= 1e-9:
                episodes += 1
            qty += t.quantity
        elif t.type == "sell":
            qty -= t.quantity
        if qty < -1e-9:
            trustworthy = False
        prev = t.when
    if abs(open_qty) > 1e-9:
        days += (as_of - prev).days
    if abs(qty - open_qty) > 1e-6:
        trustworthy = False
    if not trustworthy:
        return None, None
    return days, max(episodes, 1)


def analyse(
    transactions: Iterable[Transaction],
    positions: dict[str, Position] | None = None,
    *,
    as_of: date | None = None,
) -> tuple[list[TickerResult], TickerResult]:
    """Returns (per-ticker results sorted by market value desc, overall)."""
    positions = positions or {}
    as_of = as_of or date.today()

    by_ticker: dict[str, list[Transaction]] = defaultdict(list)
    for t in transactions:
        if t.type in ("buy", "sell", "dividend"):
            by_ticker[t.ticker].append(t)

    results: list[TickerResult] = []
    all_flows: list[CashFlow] = []
    agg = {"invested": 0.0, "proceeds": 0.0, "dividends": 0.0, "market_value": 0.0}
    first_all: date | None = None
    last_all: date | None = None

    for ticker in sorted(by_ticker):
        txns = sorted(by_ticker[ticker], key=lambda x: x.when)
        flows = [
            CashFlow(t.when, t.amount, ticker=ticker, kind=t.type) for t in txns
        ]

        invested = sum(-t.amount for t in txns if t.type == "buy")
        proceeds = sum(t.amount for t in txns if t.type == "sell")
        dividends = sum(t.amount for t in txns if t.type == "dividend")

        bought = sum(t.quantity for t in txns if t.type == "buy")
        sold = sum(t.quantity for t in txns if t.type == "sell")
        pos = positions.get(ticker)
        open_qty = pos.quantity if pos is not None else (bought - sold)
        mv = pos.market_value if pos is not None else 0.0

        if abs(open_qty) > 1e-9 and mv:
            flows.append(CashFlow(as_of, mv, ticker=ticker, kind="terminal"))

        rate, note = xirr_or_none(flows)

        first = txns[0].when
        last = max(txns[-1].when, as_of if (abs(open_qty) > 1e-9 and mv) else txns[-1].when)
        first_all = first if first_all is None else min(first_all, first)
        last_all = last if last_all is None else max(last_all, last)

        days_held, episodes = _holding_timeline(txns, open_qty, as_of)

        results.append(TickerResult(
            ticker=ticker, xirr=rate, note=note,
            invested=invested, proceeds=proceeds, dividends=dividends,
            market_value=mv, open_quantity=open_qty,
            first_activity=first, last_activity=last, n_flows=len(flows),
            days_held=days_held, episodes=episodes,
        ))

        all_flows.extend(f for f in flows if f.kind != "terminal")
        agg["invested"] += invested
        agg["proceeds"] += proceeds
        agg["dividends"] += dividends
        agg["market_value"] += mv

    if agg["market_value"]:
        all_flows.append(CashFlow(as_of, agg["market_value"], kind="terminal"))

    overall_rate, overall_note = xirr_or_none(all_flows)
    overall = TickerResult(
        ticker="** OVERALL **", xirr=overall_rate, note=overall_note,
        invested=agg["invested"], proceeds=agg["proceeds"],
        dividends=agg["dividends"], market_value=agg["market_value"],
        open_quantity=1.0 if agg["market_value"] else 0.0,
        first_activity=first_all, last_activity=last_all, n_flows=len(all_flows),
    )

    results.sort(key=lambda r: (r.market_value, r.invested), reverse=True)
    return results, overall
