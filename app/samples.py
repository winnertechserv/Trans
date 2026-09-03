#!/usr/bin/env python3
"""Install or remove the demo portfolio.

The demo is a whole database rather than rows mixed into yours, which makes removing it
exact: there is no "which of these did the sample put here" to get wrong. It is stamped
`demo=1` in the meta table, and `clear` refuses to touch a database without that stamp —
so pointing this at real data does nothing rather than something regrettable.

    python3 app/samples.py load     # install the demo as portfolio.db
    python3 app/samples.py status   # is the current database the demo?
    python3 app/samples.py clear    # remove it and start empty
"""
import os, sys, shutil, sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
import db as D

DEMO = os.path.join(ROOT, "samples", "demo.db")
LIVE = D.DB_PATH
KEEP = LIVE + ".pre-demo"


def _flag(path):
    """-> '1' if this database is the demo, None if not, False if unreadable."""
    if not os.path.exists(path):
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        row = con.execute("SELECT value FROM meta WHERE key='demo'").fetchone()
        con.close()
        return row[0] if row else None
    except sqlite3.Error:
        return False


def _counts(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    t = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    p = con.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    con.close()
    return t, p


def status():
    if not os.path.exists(LIVE):
        print("no database yet — `python3 app/samples.py load` installs the demo")
        return 0
    t, p = _counts(LIVE)
    if _flag(LIVE) == "1":
        print(f"the demo is installed: {t} transactions, {p} positions")
        print("  `python3 app/samples.py clear` removes it and starts you empty")
    elif not t and not p:
        print("empty database, ready for your own data")
        print("  say `bootstrap` in Claude Code, or import a file — docs/IMPORTING.md")
    else:
        print(f"your own data: {t} transactions, {p} positions (not the demo)")
    return 0


def load(force=False):
    if not os.path.exists(DEMO):
        print(f"missing {DEMO} — rebuild it with: python3 samples/build_demo.py")
        return 1
    if os.path.exists(LIVE) and _flag(LIVE) != "1":
        t, p = _counts(LIVE)
        # An empty database is not "your data" — clearing the demo leaves one behind, and
        # refusing to reload over it would strand anyone who wanted a second look.
        if not force and (t or p):
            print(f"portfolio.db already holds your own data ({t} transactions, "
                  f"{p} positions).\n"
                  "  Loading the demo would replace it. Your data would be kept at\n"
                  f"  {os.path.basename(KEEP)}, but back it up first if it matters:\n"
                  "      python3 app/backup.py snapshot manual\n"
                  "  Then re-run with --force.")
            return 1
        if t or p:
            shutil.copy2(LIVE, KEEP)
            print(f"kept your database as {os.path.basename(KEEP)}")
    for suffix in ("-wal", "-shm"):                 # stale WAL would shadow the copy
        if os.path.exists(LIVE + suffix):
            os.remove(LIVE + suffix)
    shutil.copy2(DEMO, LIVE)
    t, p = _counts(LIVE)
    print(f"demo installed: {t} transactions, {p} positions across two markets")
    print("  ./run.sh   then switch markets with the toggle in the header")
    print("  remove it with: python3 app/samples.py clear")
    return 0


def clear():
    flag = _flag(LIVE)
    if flag is None and not os.path.exists(LIVE):
        print("nothing to clear — no database")
        return 0
    if flag != "1":
        print("refusing: portfolio.db is not the demo, so this would delete your own data.\n"
              "  To start over deliberately: python3 app/backup.py snapshot manual\n"
              "  then remove portfolio.db by hand.")
        return 1
    for suffix in ("", "-wal", "-shm"):
        p = LIVE + suffix
        if os.path.exists(p):
            os.remove(p)
    D.init()
    print("demo removed — empty database ready")
    if os.path.exists(KEEP):
        print(f"  your earlier data is still at {os.path.basename(KEEP)}")
    print("  say `bootstrap` in Claude Code, or import a file — see docs/IMPORTING.md")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    force = "--force" in sys.argv
    sys.exit({"load": lambda: load(force), "clear": clear,
              "status": status}.get(cmd, status)())
