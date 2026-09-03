#!/usr/bin/env bash
# Local portfolio dashboard.
# Dependencies: python3 (stdlib only). Claude Code is used to sync broker data.
set -euo pipefail
cd "$(dirname "$0")"

DB=portfolio.db

# python3, not the sqlite3 CLI: sqlite3 is absent on many minimal Linux installs and
# its absence used to be indistinguishable from an empty database.
txn_count() {
  python3 - "$DB" <<'PY' 2>/dev/null || echo 0
import sqlite3, sys, os
p = sys.argv[1]
if not os.path.exists(p): print(0); raise SystemExit
try:
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    print(c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]); c.close()
except Exception:
    print(0)
PY
}

case "${1:-}" in
  --demo)    exec python3 app/samples.py load "${2:-}" ;;
  --clear-demo) exec python3 app/samples.py clear ;;
  --restore) exec python3 app/restore_cli.py ;;
  --backup)  exec python3 app/backup.py snapshot manual ;;
  --help|-h)
    cat <<'MSG'
./run.sh              start the dashboard
./run.sh --demo       install a sample portfolio and look around first
./run.sh --clear-demo remove it, leaving an empty database
./run.sh --restore    restore a snapshot from your Google Drive folder
./run.sh --backup     write a snapshot now
./run.sh <port>       start on a specific port
MSG
    exit 0 ;;
esac

# config.json is needed to SYNC from a broker, not to run. Refusing to start without one
# locked newcomers out of the demo, which is the thing the README tells them to try first.
if [ ! -f config.json ]; then
  if [ -f "$DB" ]; then
    echo "No config.json — running without broker sync. Copy config.example.json to"
    echo "config.json when you want to connect one."
    echo
  else
    cat <<'MSG'
No database and no config.json yet. Two ways to start:

  ./run.sh --demo        look around with a sample portfolio, no broker needed

  cp config.example.json config.json     then connect a broker and say `sync`
                                         in Claude Code (see README.md)
MSG
    exit 1
  fi
fi

# Hooks are not carried by a clone, so switch the personal-data guard on locally. One
# line, idempotent, and it means an accidental `git add -A` is refused rather than pushed.
[ -d .git ] && [ "$(git config core.hooksPath 2>/dev/null)" != ".githooks" ] \
  && git config core.hooksPath .githooks 2>/dev/null || true

python3 app/db.py >/dev/null            # ensure schema
python3 app/sync.py >/dev/null 2>&1 || true   # (re)generate sync/prompts/*.txt

if [ "$(txn_count)" -eq 0 ]; then
  cat <<'MSG'

No portfolio data found in portfolio.db.

  Build from scratch    open Claude Code in this folder and say:  bootstrap
                        (pulls your full history from the broker)

  Restore a backup      ./run.sh --restore
                        (lists snapshots from your Google Drive folder)

Then run ./run.sh again.

MSG
  exit 0
fi

PORT="${1:-$(python3 -c 'import sys;sys.path.insert(0,"app");import config;print(config.port())')}"
exec python3 app/server.py "$PORT"
