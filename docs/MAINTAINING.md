# Maintaining Trans

For whoever holds the merge button. Written down so the decisions are consistent whether
a PR arrives on a good week or a busy one.

## Branch protection, and why it is set this way

`main` is governed by **one** ruleset — deliberately one, because it was previously three
overlapping layers and a single missed setting in any of them blocked everything.

| Rule | Effect |
|---|---|
| `pull_request`, 1 approval | changes arrive by PR and need a review |
| `required_status_checks` | CI must pass on 3.11, 3.12 and 3.13 |
| `dismiss_stale_reviews_on_push` | a new commit invalidates an old approval |
| `required_review_thread_resolution` | conversations must be resolved before merge |
| `deletion`, `non_fast_forward` | `main` cannot be deleted or force-pushed |
| **bypass: Admin, always** | the maintainer can merge without a second reviewer |

The bypass exists because a solo maintainer cannot obtain an approval. It is not a licence
to skip review — it is what makes a one-person project possible. **Use it for your own
changes; never for someone else's.**

## Reviewing a pull request

Read in this order. Most PRs are decided by the first three.

**1. Does it touch personal data?** `check_clean.py` runs in CI, but read the diff anyway:
a screenshot with an account number, a test fixture built from a real tradebook, a
`config.json` committed by accident. This is the only class of mistake that cannot be
undone after a merge.

**2. Does a number move, and does the PR say so?** Any change to `analytics.py`,
`portfolio.py`, `ingest.py` or `xirr.py` can silently change what someone believes about
their own money. The PR must state which figure changes and by how much. If it does not
say, ask — do not work it out yourself and assume you got it right.

**3. Is there a test, and does it name the failure it guards?** A test that asserts the
new behaviour without saying which bug it prevents will be deleted by someone in a year
who thinks it is redundant.

**4. Does it hold the two hard rules?** No dependency in `app/` or the root; nothing that
places, modifies or cancels a broker order.

**5. Has the contributor pasted their test output?** Not because CI cannot be trusted, but
because it shows they ran it before pushing rather than using CI as their test runner.

### When to ask for changes rather than fixing it yourself

Fix it yourself for typos and one-line adjustments. Ask for changes when the contributor
needs to understand something — an importer without reconciliation, a test without a
reason, a figure that moved unexplained. A repo about transparency should be reviewed
transparently.

### Merging

**Squash** for most PRs: one logical change, one commit, message rewritten to say what
changed and why it mattered. **Merge commit** only when the individual commits each stand
alone and the history is worth keeping. Never rebase-merge onto `main` — it rewrites
authorship dates and confuses the record.

Delete the branch after merging. GitHub offers it; take it.

## Releases

There is no build and no package. A release is a **tag plus notes** saying what changed
for someone who already has a clone.

### Versioning

`MAJOR.MINOR.PATCH`, where the promise is about **the data**, not the API:

- **MAJOR** — the database schema changes in a way an existing `portfolio.db` cannot
  simply be opened with, or a figure is redefined such that the same data now reports a
  different number. Both need migration notes.
- **MINOR** — a new broker, a new tab, a new analysis. Existing databases keep working.
- **PATCH** — fixes and corrections that leave the schema and every definition alone.

A corrected figure is **MAJOR** even when the fix is obviously right. If someone's XIRR
reads differently after an upgrade, they must be told before it happens, not after they
notice.

### Cutting one

```bash
python3 run_tests.py                     # everything green
python3 scripts/check_clean.py           # clean
git checkout main && git pull

# a clean checkout, the way a new user gets it
git archive HEAD | tar -x -C /tmp/rel && cd /tmp/rel
./run.sh --demo && ./run.sh 8799         # then click through both markets

git tag -a v1.2.0 -m "…" && git push origin v1.2.0
gh release create v1.2.0 --title "…" --notes-file notes.md
```

The clean-checkout step is not optional. `./run.sh --demo` shipped broken for two releases
because everything was tested from a working directory that already had a database.

### Release notes

Write for someone deciding whether to upgrade today:

- **Numbers that changed**, first and plainly — which figure, why, and roughly how much.
  This is the section people actually need.
- **New capability** — brokers, tabs, analyses.
- **Fixes**, with the symptom rather than the cause: "a holding sold before it was bought
  showed as pure profit" beats "fixed cost basis in results()".
- **Anything requiring action** — re-import a file, run `ingest.py remap`, take a backup.

Say what a release does **not** fix when it is likely to be asked. India dividends are
still invisible; that belongs in the notes until it is not true.

### Cadence

Tag when something is worth telling people about, not on a schedule. A dormant month with
no release is fine. A fix to a wrong figure is worth a release on its own, the same day.

## Keeping the demo honest

`samples/demo.db` is generated. After any schema change:

```bash
python3 samples/build_demo.py && python3 run_tests.py
```

It is deliberately untidy — a re-entered position, a renamed ticker, a fund with no order
history, a demerged holding with no cost. Keep it that way. A demo where every number
behaves teaches people to trust numbers that should be questioned.

## What to say no to

- **Advice, signals, recommendations, price targets.** Trans computes and displays.
- **Anything that can place an order.** Read-only is a boundary, not a gap.
- **Dependencies in `app/`.** The stdlib-only property is the whole install story.
- **Telemetry**, of any kind, however anonymous.
- **A figure without a definition.** If it cannot be explained in a tooltip, it does not
  go in the header.
