#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run everything. One command, stdlib only, no runner to install.

    python3 run_tests.py            all of it
    python3 run_tests.py markets    just tests/test_markets.py

Prints a copy-pasteable summary, because CONTRIBUTING asks contributors to paste the
result of this into their pull request.
"""
import os, sys, subprocess, unittest

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else None
    failures = 0

    # The XIRR solver has its own harness predating unittest; keep it, it is thorough.
    if not which or which in ("xirr", "solver"):
        print("=" * 62)
        print("xirr solver")
        print("=" * 62)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "test_xirr.py")],
                           capture_output=True, text=True)
        tail = [l for l in r.stdout.strip().split("\n") if l.strip()][-1:]
        print(r.stdout if r.returncode else "\n".join(tail))
        failures += r.returncode != 0

    if which in ("xirr", "solver"):
        return failures

    print("=" * 62)
    print("unit tests")
    print("=" * 62)
    loader = unittest.TestLoader()
    if which:
        suite = loader.loadTestsFromName(f"tests.test_{which}")
    else:
        suite = loader.discover(os.path.join(ROOT, "tests"), top_level_dir=ROOT)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    failures += (len(result.failures) + len(result.errors))

    print()
    print("=" * 62)
    if failures:
        print(f"FAILED — {failures} problem(s)")
    else:
        print(f"PASSED — {result.testsRun} unit tests, plus the XIRR solver suite")
    print("=" * 62)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
