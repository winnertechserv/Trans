## What this changes

<!-- and why it matters -->

## Checklist

- [ ] `python3 scripts/check_clean.py` says **clean** — no real broker data, anywhere
- [ ] `python3 test_xirr.py` passes
- [ ] Developed and tested against the demo (`./run.sh --demo`), not real holdings
- [ ] No new dependency in `app/` or the repo root (stdlib only)
- [ ] If this touches how a number is derived, I have said which figure changes and by
      how much

## If this adds or changes a data importer

- [ ] Validated against the source's own totals where it publishes them, and the import
      refuses a file that does not reconcile rather than importing it partially
- [ ] `docs/IMPORTING.md` updated
