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

```bash
python3 test_xirr.py                                        # 16/16
python3 -m py_compile app/*.py *.py scripts/*.py samples/*.py tools/*.py
python3 scripts/check_clean.py                              # must say "clean"
```

If you touched `app/static/index.html`, check the JavaScript parses. There is no build
step and no bundler — it is one file, deliberately.

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
