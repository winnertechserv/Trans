# Trans — backups

## What is backed up

**Only the database file.** One self-contained `.db` per snapshot — transactions,
positions, fundamentals, ledgers. No code, no CSVs, nothing else.

The split is deliberate:

| Goes to | What | Why |
|---|---|---|
| GitHub (private) | code | versioned, diffable, shareable |
| Google Drive | `portfolio-*.db` only | binary, personal, changes daily |

Not a delta — each snapshot stands alone, so restoring is one file.

## Why `VACUUM INTO`, not `cp`

The database runs in **WAL mode**. At any moment some committed data lives in
`portfolio.db-wal` rather than in `portfolio.db`. Copying the main file alone can capture
a torn state and silently lose recent writes — the kind of backup that looks fine until
the day you need it.

`VACUUM INTO` asks SQLite to write a fresh, internally consistent single file with the
WAL folded in. It is safe to run while the app is serving. Every snapshot is verified
with `PRAGMA integrity_check` before it is published; a failing snapshot is deleted, not
kept.

## What "backed up" actually means

Google Drive for Desktop uploads **asynchronously**. The app writes the file into the
synced folder and returns immediately — well before the upload completes. So the UI says
**"snapshot written and queued to Google Drive"**, never "backed up to the cloud".

Three failure modes the app **cannot** detect, because the local write genuinely succeeds:

- Drive signed out or the token revoked
- Sync paused (manually, or on a metered connection)
- Drive storage quota exceeded

In all three the file sits on disk forever and never uploads. The menu-bar Drive icon is
the only reliable indicator. The Backup card flags the newest snapshot's age, which is
your practical early warning.

## Layout in Drive

```
<drive_backup_dir>/
  portfolio-20260901T203413.db      # sortable, UTC-naive local timestamp
  portfolio-20260901T203525.db
  _backup_manifest.json
```

`_backup_manifest.json` lives **in Drive, not in the database** — on a new machine there
is no local DB to read a log from. Each entry:

```json
{"file":"portfolio-20260901T203413.db","created_at":"2026-09-01T20:34:13",
 "bytes":2129920,"sha256":"6680913…","n_transactions":10820,
 "last_transaction_date":"2026-09-01"}
```

If the manifest is missing or disagrees with what is on disk, listing falls back to
**filename + modified time**. Restore therefore works even with no manifest at all.

## Where snapshots go, on any machine

The folder is `<drive root>/trans`. The Drive root is auto-detected, so a fresh clone
usually needs no configuration:

| Platform | Detected |
|---|---|
| macOS | `~/Library/CloudStorage/GoogleDrive-<acct>/My Drive` (current), `~/Google Drive` (legacy) |
| Linux | `~/Insync/<acct>/Google Drive`, `~/GoogleDrive`, `~/gdrive`, `~/google-drive`, `~/Drive`, `/mnt/gdrive`, `/mnt/google-drive`, `/media/gdrive` |

Linux has **no official Google Drive client**. Mount Drive with
[rclone](https://rclone.org/drive/), google-drive-ocamlfuse, or Insync — if it lands in
one of the paths above it is found automatically; otherwise set `drive_backup_dir` in
`config.json` to the folder and detection is skipped. An explicitly configured path
always wins over auto-detection, on every platform.

## When backups happen

1. **Manual** — the **Back up now** button, `./run.sh --backup`, or `backup` in Claude Code.
2. **Automatic on staleness** — if the newest snapshot is **older than 7 days** (or none
   exists), one is taken. Checked when the server starts and, throttled to once an hour,
   while the app is open.

Tune with `backup.max_age_days` in `config.json`. The check itself is a directory mtime
scan — it costs nothing until a backup is actually due.

There is no fixed daily cron by default: the app backs up when the data is genuinely
stale rather than on a clock. A launchd template is still provided if you prefer a fixed
schedule.

## Running a backup

| How | Command |
|---|---|
| Web app | **Sync & cost** tab → **Back up now** |
| Terminal | `./run.sh --backup` |
| Claude Code | say `backup` |
| Scheduled | launchd — below |

None of these cost AI tokens. The button posts to the local Python server, which writes
the file; Drive's own daemon uploads it. No model is involved.

## Daily schedule

```bash
sed "s|REPO_PATH|$PWD|g" scripts/com.portfolio.backup.plist.example \
  > ~/Library/LaunchAgents/com.portfolio.backup.plist
launchctl load ~/Library/LaunchAgents/com.portfolio.backup.plist
launchctl start com.portfolio.backup      # run once now to prove it works
```

Default 18:45 daily; edit `StartCalendarInterval` to taste. Output goes to `backup.log`.
This runs whether or not the app or Claude Code is open — the button alone only fires
when you remember to press it.

Remove with `launchctl unload ~/Library/LaunchAgents/com.portfolio.backup.plist`.

## Retention

`keep_daily` (default 30) most recent, plus the first snapshot of each month for
`keep_monthly` (default 12) months. Pruning runs after every snapshot. At ~2 MB each
that is roughly 85 MB steady-state.

## Restoring

**From the web app** — Sync & cost → Backup → **Restore** on any row. Confirms first.

**From the terminal** — `./run.sh --restore` lists snapshots newest first with date,
size and transaction count, and asks which. Neither ever auto-picks.

Before overwriting, the current database is preserved as `portfolio.db.pre-restore`
(via `VACUUM INTO`, not `cp` — the live DB is in WAL mode). Any snapshot failing
`integrity_check` is refused.

Restore writes through SQLite's **online backup API** rather than replacing the file.
Copying a file over a live WAL database fails with `database is locked` and can strand
a stale `-wal` beside a swapped main file; the backup API takes the correct locks and
rewrites page by page.

### Restoring rewinds the sync cursor automatically

The database is the source of truth for how current it is. After a restore, the app
recomputes `MAX(date)` per asset class and **regenerates the sync prompts** against it.

Restore a snapshot from two weeks ago and the daily prompt immediately changes to
`created_at_gte="<that date>"`. Say `sync` in Claude Code and it fetches exactly the gap —
nothing refetched, nothing missed. The Backup card shows this as
*"Database currently holds data through X — N days behind"*.

Because ingest dedupes on the broker's order id, an overlapping refetch is harmless
anyway; the cursor just makes it efficient.

## Verifying a backup

```bash
sqlite3 "<snapshot>" "PRAGMA integrity_check;"                  # -> ok
sqlite3 "<snapshot>" "SELECT COUNT(*) FROM transactions;"       # -> matches live
shasum -a 256 "<snapshot>"                                      # -> matches manifest
```
