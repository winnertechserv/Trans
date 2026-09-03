## What this changes

<!-- And why it mattered. If a number moves, say which one, from what to what. -->

## Which figure changes, if any

<!-- e.g. "PCBL XIRR: 6154% -> 5.0%, because its pre-rename purchases now attach" -->

## Evidence

<details>
<summary><code>python3 run_tests.py</code></summary>

```
paste the output here
```
</details>

<details>
<summary><code>python3 scripts/check_clean.py</code></summary>

```
paste the output here
```
</details>

## Checked by hand

<!-- For UI changes: which screens, which market, what you clicked. -->

## Checklist

- [ ] `run_tests.py` passes and the output is pasted above
- [ ] `check_clean.py` says **clean** — no real broker data anywhere
- [ ] Developed against the demo (`./run.sh --demo`), not real holdings
- [ ] No new dependency in `app/` or the repo root — standard library only
- [ ] A test covers the behaviour I changed, and its comment says which failure it guards

## If this adds or changes a data importer

- [ ] Validated against the source's own totals where it publishes them, and a file that
      does not reconcile is skipped whole rather than imported partially
- [ ] `docs/IMPORTING.md` updated
