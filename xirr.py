"""XIRR - money-weighted return for irregularly spaced cash flows.

Solves for the rate r satisfying:

    sum_i  CF_i / (1 + r) ** ((d_i - d_0) / 365)  ==  0

Sign convention: money leaving your pocket is negative (buys), money
arriving is positive (sells, dividends, and the terminal mark-to-market
value of whatever you still hold).

Uses a 365-day basis, matching Excel/Sheets XIRR.

Pure stdlib - no numpy, no pandas.
"""
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Sequence

DAYS_PER_YEAR = 365.0

# (1 + r) must stay strictly positive, so r is constrained to (-1, inf).
_MIN_RATE = -0.999999
_TOL = 1e-10
_MAX_ITER = 100


class XirrError(ValueError):
    """Raised when XIRR is mathematically undefined for the given flows."""


@dataclass(frozen=True)
class CashFlow:
    """One dated cash movement.

    amount < 0 -> money out (a purchase)
    amount > 0 -> money in  (a sale, a dividend, or terminal market value)
    """

    when: date
    amount: float
    ticker: str | None = None
    kind: str = ""  # buy | sell | dividend | terminal | deposit | withdrawal

    def __post_init__(self) -> None:
        if not isinstance(self.when, date):
            raise TypeError(f"when must be a date, got {type(self.when).__name__}")
        if not math.isfinite(self.amount):
            raise ValueError(f"amount must be finite, got {self.amount!r}")


def _years_between(d0: date, d1: date) -> float:
    return (d1 - d0).days / DAYS_PER_YEAR


def npv(rate: float, flows: Sequence[CashFlow], t0: date | None = None) -> float:
    """Net present value of `flows` discounted at `rate`."""
    if not flows:
        return 0.0
    base = 1.0 + rate
    if base <= 0.0:
        return math.nan
    origin = t0 if t0 is not None else min(f.when for f in flows)

    total = 0.0
    for cf in flows:
        t = _years_between(origin, cf.when)
        try:
            total += cf.amount / (base ** t)
        except (OverflowError, ZeroDivisionError):
            # Discount factor blew up; report a saturated value with the
            # correct sign so bracketing still behaves monotonically.
            return math.inf if cf.amount > 0 else -math.inf
    return total


def _dnpv(rate: float, flows: Sequence[CashFlow], t0: date) -> float:
    """d(NPV)/d(rate)."""
    base = 1.0 + rate
    if base <= 0.0:
        return math.nan
    total = 0.0
    for cf in flows:
        t = _years_between(t0, cf.when)
        try:
            total -= t * cf.amount / (base ** (t + 1.0))
        except (OverflowError, ZeroDivisionError):
            return math.nan
    return total


def _bisect(flows: Sequence[CashFlow], t0: date, tol: float) -> float | None:
    """Bracket a sign change on a coarse grid, then bisect to `tol`."""
    grid = (
        -0.999, -0.99, -0.95, -0.9, -0.8, -0.7, -0.6, -0.5, -0.4, -0.3,
        -0.2, -0.1, -0.05, 0.0, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0,
        2.0, 3.0, 5.0, 10.0, 25.0, 100.0, 1_000.0, 10_000.0,
    )

    prev_r: float | None = None
    prev_f: float | None = None

    for r in grid:
        f = npv(r, flows, t0)
        if not math.isfinite(f):
            prev_r, prev_f = None, None
            continue
        if f == 0.0:
            return r
        if prev_f is not None and prev_r is not None and (prev_f < 0.0) != (f < 0.0):
            lo, hi, f_lo = prev_r, r, prev_f
            for _ in range(300):
                mid = (lo + hi) / 2.0
                f_mid = npv(mid, flows, t0)
                if not math.isfinite(f_mid):
                    break
                if abs(f_mid) < tol or (hi - lo) < tol:
                    return mid
                if (f_lo < 0.0) != (f_mid < 0.0):
                    hi = mid
                else:
                    lo, f_lo = mid, f_mid
            return (lo + hi) / 2.0
        prev_r, prev_f = r, f

    return None


def xirr(
    flows: Iterable[CashFlow],
    *,
    guess: float = 0.1,
    tol: float = _TOL,
    max_iter: int = _MAX_ITER,
) -> float:
    """Annualised money-weighted return.

    Returns the rate as a decimal (0.1234 == 12.34% per year).
    Raises XirrError when no rate exists (e.g. all flows same sign, or
    every flow lands on the same day).
    """
    flows = sorted(flows, key=lambda c: c.when)

    if len(flows) < 2:
        raise XirrError("need at least two cash flows")

    has_neg = any(f.amount < 0 for f in flows)
    has_pos = any(f.amount > 0 for f in flows)
    if not (has_neg and has_pos):
        raise XirrError(
            "need at least one negative and one positive cash flow "
            "(an investment and a return)"
        )

    t0 = flows[0].when
    if all(f.when == t0 for f in flows):
        raise XirrError("all cash flows occur on the same date; return is undefined")

    # Newton-Raphson, clamped to the valid domain r > -1.
    rate = guess
    for _ in range(max_iter):
        f_val = npv(rate, flows, t0)
        if not math.isfinite(f_val):
            break
        if abs(f_val) < tol:
            return rate

        deriv = _dnpv(rate, flows, t0)
        if not math.isfinite(deriv) or deriv == 0.0:
            break

        step = f_val / deriv
        nxt = rate - step
        if not math.isfinite(nxt):
            break
        if nxt <= _MIN_RATE:
            # Damp back toward the domain edge instead of leaving it.
            nxt = (rate + _MIN_RATE) / 2.0
        if abs(nxt - rate) < tol:
            return nxt
        rate = nxt

    found = _bisect(flows, t0, tol)
    if found is None:
        raise XirrError("no rate found; cash flow pattern may have no real solution")
    return found


def xirr_or_none(flows: Iterable[CashFlow]) -> tuple[float | None, str | None]:
    """Non-raising variant. Returns (rate, None) or (None, reason)."""
    try:
        return xirr(flows), None
    except XirrError as exc:
        return None, str(exc)
