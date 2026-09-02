"""Validation of the XIRR solver against analytically-known values."""

from __future__ import annotations

import math
from datetime import date

from xirr import CashFlow, XirrError, npv, xirr

PASS = "PASS"
FAIL = "FAIL"
_results: list[tuple[str, str, str]] = []


def check(name: str, got: float, want: float, tol: float = 1e-7) -> None:
    ok = math.isfinite(got) and abs(got - want) < tol
    _results.append((
        PASS if ok else FAIL,
        name,
        f"got {got:.10f}  want {want:.10f}  diff {abs(got - want):.2e}",
    ))


def check_raises(name: str, flows, exc=XirrError) -> None:
    try:
        got = xirr(flows)
    except exc as e:
        _results.append((PASS, name, f"raised: {e}"))
    except Exception as e:  # noqa: BLE001
        _results.append((FAIL, name, f"wrong exception {type(e).__name__}: {e}"))
    else:
        _results.append((FAIL, name, f"expected {exc.__name__}, got {got}"))


def cf(y, m, d, amt):
    return CashFlow(date(y, m, d), amt)


# 1. Exactly 365 days, +10%. (2021 is not a leap year.)
check(
    "365 days, 10% gain",
    xirr([cf(2021, 1, 1, -1000), cf(2022, 1, 1, 1100)]),
    0.10,
    tol=1e-6,
)

# 2. Two years (730d), +20% total -> sqrt(1.2) - 1 annualised.
check(
    "two years, 20% total",
    xirr([cf(2020, 1, 1, -1000), cf(2021, 12, 31, 1200)]),
    math.sqrt(1.2) - 1.0,
    tol=1e-6,
)

# 3. Flat.
check(
    "flat, zero return",
    xirr([cf(2020, 1, 1, -1000), cf(2021, 1, 1, 1000)]),
    0.0,
    tol=1e-6,
)

# 4. 50% loss over one year.
check(
    "365 days, 50% loss",
    xirr([cf(2021, 1, 1, -1000), cf(2022, 1, 1, 500)]),
    -0.5,
    tol=1e-6,
)

# 5. Severe loss - exercises the clamped domain near r = -1.
check(
    "365 days, 99% loss",
    xirr([cf(2021, 1, 1, -1000), cf(2022, 1, 1, 10)]),
    -0.99,
    tol=1e-6,
)

# 5b. Leap year: 2020-01-01 -> 2021-01-01 is 366 days, so on a 365-day
# basis the annualised rate is slightly BELOW the raw 10% period return.
# Excel/Sheets XIRR behaves identically. Locking this in deliberately.
_leap = [cf(2020, 1, 1, -1000), cf(2021, 1, 1, 1100)]
_r_leap = xirr(_leap)
_results.append((
    PASS if abs((1 + _r_leap) ** (366 / 365) - 1.1) < 1e-9 else FAIL,
    "leap-year span uses 366/365",
    f"rate {_r_leap:.8f} -> (1+r)^(366/365) = {(1 + _r_leap) ** (366 / 365):.10f}",
))

# 6. Microsoft's documented XIRR example -> 0.373362535
excel_case = [
    cf(2008, 1, 1, -10000),
    cf(2008, 3, 1, 2750),
    cf(2008, 10, 30, 4250),
    cf(2009, 2, 15, 3250),
    cf(2009, 4, 1, 2750),
]
check("Excel reference example", xirr(excel_case), 0.373362535, tol=1e-6)

# 7. Root check: NPV at the solved rate must be ~0.
r = xirr(excel_case)
resid = npv(r, excel_case)
_results.append((
    PASS if abs(resid) < 1e-6 else FAIL,
    "NPV at solved rate is zero",
    f"residual {resid:.3e}",
))

# 8. Dividend-style flows: buy, collect income, sell.
divcase = [
    cf(2022, 1, 10, -5000),
    cf(2022, 4, 10, 25),
    cf(2022, 7, 10, 25),
    cf(2022, 10, 10, 25),
    cf(2023, 1, 10, 25),
    cf(2023, 1, 10, 5600),
]
r8 = xirr(divcase)
_results.append((
    PASS if abs(npv(r8, divcase)) < 1e-6 else FAIL,
    "buy + dividends + sell solves",
    f"rate {r8:.6%}  residual {npv(r8, divcase):.2e}",
))

# 9. Dollar-cost averaging into a still-open position (terminal value flow).
dca = [cf(2021, m, 1, -500) for m in range(1, 13)]
dca.append(cf(2024, 1, 1, 9000))
r9 = xirr(dca)
_results.append((
    PASS if abs(npv(r9, dca)) < 1e-6 else FAIL,
    "DCA with terminal value",
    f"rate {r9:.6%}  residual {npv(r9, dca):.2e}",
))

# 10-13. Undefined cases must raise, not silently return garbage.
check_raises("all negative flows", [cf(2020, 1, 1, -100), cf(2021, 1, 1, -100)])
check_raises("all positive flows", [cf(2020, 1, 1, 100), cf(2021, 1, 1, 100)])
check_raises("single flow", [cf(2020, 1, 1, -100)])
check_raises(
    "same-day flows",
    [cf(2020, 1, 1, -100), cf(2020, 1, 1, 150)],
)

# 14. Order independence - shuffled input must give the same answer.
import random  # noqa: E402

shuffled = excel_case[:]
random.Random(42).shuffle(shuffled)
check("order independence", xirr(shuffled), 0.373362535, tol=1e-6)

# 15. Large multi-year portfolio, many flows - stability/perf smoke test.
big = []
for i in range(200):
    big.append(CashFlow(date(2015, 1, 1) + __import__("datetime").timedelta(days=i * 15), -250.0))
big.append(CashFlow(date(2024, 6, 1), 78000.0))
r15 = xirr(big)
_results.append((
    PASS if abs(npv(r15, big)) < 1e-5 else FAIL,
    "201 flows over 9 years",
    f"rate {r15:.6%}  residual {npv(r15, big):.2e}",
))


width = max(len(n) for _, n, _ in _results)
failed = 0
print()
for status, name, detail in _results:
    if status == FAIL:
        failed += 1
    print(f"  [{status}] {name.ljust(width)}   {detail}")

total = len(_results)
print(f"\n  {total - failed}/{total} passed"
      + ("" if not failed else f"  ({failed} FAILED)"))
raise SystemExit(1 if failed else 0)
