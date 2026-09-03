# Contributing to Trans

Thanks for wanting to help. This file covers the things that are specific to this project;
the usual open-source etiquette applies otherwise.

## Two rules that are not negotiable

**1. Never commit real broker data.** Not a tradebook, not a statement, not a database,
not a screenshot with an account number in it. A pre-commit hook refuses these:

```bash
git config core.hooksPath .githooks     # ./run.sh does this for you on first start
```

Hooks are not carried by a clone, which is why `run.sh` enables it. If you skip `run.sh`,
run the line above. The guard is `scripts/check_clean.py` and you can run it any time.

**2. `app/` and the repo root are standard-library only.** No `pip install`, ever. This is
the property that lets someone run Trans with nothing but Python, and it is worth more
than any convenience a dependency would buy. The single exception is `tools/ta_runner.py`,
which executes inside TradingAgents' own virtualenv and never runs in-process.

## Getting set up

```bash
git clone https://github.com/winnertechserv/trans && cd trans
./run.sh --demo      # invented portfolio across two markets
./run.sh             # http://127.0.0.1:8787
```

**Develop against the demo, not your own holdings.** It deliberately contains the awkward
cases — a re-entered position, a renamed ticker, a fund with no order history, a demerged
holding with no cost — so most bugs reproduce there. `docs/DEMO.md` explains each.

Rebuild it after a schema change: `python3 samples/build_demo.py`.

## Before you open a PR

Run these and **paste the output of the first two into the pull request**. The template
asks for it, and a PR without it goes back — not out of ceremony, but because "I ran the
tests" and "here is what they said" are different claims.

```bash
python3 run_tests.py                                        # every test
python3 scripts/check_clean.py                              # must say "clean"
python3 -m py_compile app/*.py *.py scripts/*.py samples/*.py tools/*.py
```

`run_tests.py` takes a filter while you iterate — `python3 run_tests.py paytm` runs only
`tests/test_paytm.py`. Run the whole suite before pushing.

If you touched `app/static/index.html`, check the JavaScript parses. There is no build
step and no bundler — it is one file, deliberately.

```bash
python3 -c "import re;open('/tmp/app.js','w').write(chr(10).join(re.findall(r'<script>(.*?)</script>',open('app/static/index.html').read(),re.S)))"
node --check /tmp/app.js
```

### What a good pull request contains

1. **What changed, and which number moved.** "Fixed XIRR" tells a reviewer nothing.
   "CEMPRO read +91,882 at 3603% and now reads -51,475 at -45.8%, because its purchases
   were filed under the pre-rename symbol" tells them everything.
2. **Pasted output** of `run_tests.py` and `check_clean.py`.
3. **A test for the thing you changed**, if behaviour changed.
4. **How you checked it by hand** for UI changes — which screens, which market.

### Writing tests

`tests/` uses `unittest` from the standard library. `tests/fixtures.py` hands you a
throwaway database:

```python
from tests.fixtures import TempDB, ago

with TempDB() as db:
    db.buy("AAPL", ago(400), 10, 100.0)
    db.position("AAPL", 10, 150.0, avg_cost=100.0)
    rows, ov = db.results("us")
```

Each test gets its own SQLite file in a temp directory and its own empty config, so
nothing can reach `portfolio.db` and results do not depend on whose machine runs them.

**Test the failure you are fixing, not the feature in general.** Nearly every test here
encodes a specific bug that reached a user — a split that made a holding look re-entered,
a trade id reused across two years, a regex that read "2073" out of a unit count. Each
says so in a comment, so the next person knows why it cannot be tidied away. Yours
should too.

For anything touching money, assert the identity that must hold whatever you changed:

```python
self.assertAlmostEqual(ov["realized"] + ov["unrealized"] + ov["dividends"],
                       ov["net_profit"], places=4)
```

That one assertion caught more errors during development than everything else combined.

## Adding a broker

This is the most useful contribution anyone can make, and the codebase is shaped for it.

- A parser, plus a handler registered in `HANDLERS` in `app/ingest.py`.
- If it is a new country, an entry in `markets.MARKETS`. `brokers` is a **list** — a market
  can span several brokers, as India spans Zerodha and Paytm. Reads use
  `markets.brokers_of()`; sync prompts target `markets.broker_of()`.
- `docs/IMPORTING.md` has the full walkthrough and describes the traps already found.

**Validate against the source's own totals wherever it offers them.** `app/paytm.py`
does this: every statement carries its own purchase and withdrawal figures, a file whose
parsed rows do not reconcile is skipped whole rather than imported partially. That check
caught three real parsing failures during development, and a partial import is worse than
a refused one because it silently inflates realised profit.

## Style

Match what is there. Comments explain **why**, especially where a number was once wrong —
those comments are load-bearing, and several exist because the obvious implementation
produced a wrong figure. If you fix a data trap, leave a note saying what it was.

Commit messages: say what changed and why it mattered. `git log` is the record of how
each of these traps was found.

## What is out of scope

- Anything that places, modifies or cancels an order. Trans is read-only against every
  broker and that is a deliberate boundary, not an unimplemented feature.
- Dependencies in `app/`. See rule 2.
- Advice, signals, or recommendations. Trans computes and displays; it does not tell
  anyone what to buy.
