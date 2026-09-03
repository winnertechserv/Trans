# The demo portfolio

A small, entirely invented portfolio you can install in one command, so the app has
something to show before you connect a broker.

```bash
./run.sh --demo          # install it
./run.sh                 # then open http://127.0.0.1:8787
./run.sh --clear-demo    # remove it and start empty
```

Or directly:

```bash
python3 app/samples.py load     # install
python3 app/samples.py status   # is the demo installed, or your own data?
python3 app/samples.py clear    # remove, leaving an empty database
```

## What is in it

27 transactions and 10 positions across both markets, plus one real research report.

| Market | Holdings |
|---|---|
| **US** | AAPL, MSFT, NVDA, VOO, COST held; PFE sold at a loss |
| **India** | DEMOCEM, DEMOPOW, a mutual fund, a demerged stock; an ETF sold at a loss |

The US tickers are real, widely held large-caps — a demo built from invented symbols
teaches nothing. Everything else is fictional and no figure in it is anyone's real
position.

**The Research tab has a genuine TradingAgents report on MSFT**, thirteen sections of it,
so you can see what that output looks like without spending anything to generate one.

## It is deliberately not tidy

A demo where every number behaves would teach the wrong lesson. This one carries the
cases that make the app's numbers worth reading in the first place:

| What you will see | Why it is there |
|---|---|
| **NVDA marked `re-entered`** | sold out completely, bought back later. XIRR is set mostly by the first episode, which is why Simple sits next to it |
| **OLDNAME with buys, NEWNAME with sells** | a renamed company. The two halves do not meet, so NEWNAME shows sales against zero cost — see below |
| **DEMOSPIN marked `no cost`** | shares from a demerger. Nothing was paid for them here, so their whole value counts as gain |
| **A fund with no XIRR** | holdings but no order history, exactly like a Zerodha mutual fund before its tradebook is imported |
| **`XIRR 1Y` populated** | dates are stored relative to today, so the demo never goes stale |

### Fixing the rename, as an exercise

`NEWNAME` shows a sale with nothing invested, because its purchases sit under `OLDNAME`.
That is the single most common problem with Indian brokerage data, and the fix is two
steps. In `config.json`:

```json
"ticker_aliases": { "OLDNAME": "NEWNAME" }
```

then

```bash
python3 app/ingest.py remap
```

Reload and `NEWNAME` has a cost basis and an XIRR. Re-importing a file would **not** have
fixed it — the dedupe key is the raw symbol, so re-imports stay no-ops and a rename added
later never reaches rows already stored. [docs/IMPORTING.md](IMPORTING.md) explains why.

## Switching between the demo and your own data

`load` refuses to overwrite a database that holds real data, and tells you to take a
backup first. `clear` refuses to delete anything that is not the demo — the demo is
stamped in the database itself, not guessed from its contents, so pointing `clear` at your
own portfolio does nothing.

If you do load the demo over real data with `--force`, the previous database is kept as
`portfolio.db.pre-demo`.

## Rebuilding it

`samples/demo.db` is generated, not hand-made:

```bash
python3 samples/build_demo.py
```

The script is short and readable, which is the point — a checked-in binary nobody can
audit is a poor thing to ship in a repo about transparency. Rebuild after a schema change.

## The CSV samples

`samples/*.csv` are for the standalone library, not the app — they show the input schema
`portfolio.py` accepts:

```bash
python3 cli.py -t samples/all.csv -p samples/positions.csv
```

See the Library section of the [README](../README.md).
