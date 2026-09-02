"""Snapshot the database into a Google-Drive-synced folder.

Why VACUUM INTO and not `cp portfolio.db`:
the database runs in WAL mode, so at any moment some committed data lives in
portfolio.db-wal rather than the main file. Copying the main file alone can capture a
torn state and silently lose recent writes. VACUUM INTO asks SQLite to write a fresh,
internally consistent single file with the WAL folded in — safe while the app is running.

The upload itself is done by Google Drive for Desktop, not by this code, and it is
asynchronous: this function returns as soon as the file is written locally, typically
well before the upload finishes. Writing the file is therefore "queued to Drive", not
"safely in the cloud". If Drive is signed out, paused, or out of quota the local write
still succeeds and nothing ever uploads — the app cannot detect that. See BACKUP.md.
"""
import os, sys, json, glob, shutil, sqlite3, hashlib, datetime as dt, socket
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as D, config as C

MANIFEST = "_backup_manifest.json"
PREFIX, SUFFIX = "portfolio-", ".db"

def _sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(chunk), b""): h.update(b)
    return h.hexdigest()

def _stats(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        n = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        last = con.execute("SELECT MAX(date) FROM transactions").fetchone()[0]
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()
    return n, last, ok

def snapshot(dest_dir=None, kind="manual"):
    dest_dir = dest_dir or C.drive_backup_dir()
    os.makedirs(dest_dir, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    name = f"{PREFIX}{ts}{SUFFIX}"
    tmp = os.path.join(dest_dir, f".{name}.partial")
    final = os.path.join(dest_dir, name)
    if os.path.exists(tmp): os.remove(tmp)

    con = sqlite3.connect(D.DB_PATH)
    try:
        con.execute("VACUUM INTO ?", (tmp,))   # consistent snapshot, WAL folded in
    finally:
        con.close()

    n, last, integrity = _stats(tmp)
    if integrity != "ok":
        os.remove(tmp); raise RuntimeError(f"snapshot failed integrity_check: {integrity}")
    os.replace(tmp, final)                      # atomic within the folder

    rec = {"file": name, "created_at": dt.datetime.now().isoformat(timespec="seconds"),
           "bytes": os.path.getsize(final), "sha256": _sha256(final),
           "n_transactions": n, "last_transaction_date": last, "kind": kind}
    write_manifest(dest_dir)
    c = D.connect()
    c.execute("INSERT INTO backups(ts,file,bytes,sha256,n_transactions,kind,status,detail)"
              " VALUES(?,?,?,?,?,?,?,?)",
              (rec["created_at"], name, rec["bytes"], rec["sha256"], n, kind, "ok",
               f"through {last}"))
    c.commit(); c.close()
    return rec

def _scan(dest_dir):
    """Filename + mtime scan — the fallback when the manifest is missing or stale."""
    out = []
    for p in sorted(glob.glob(os.path.join(dest_dir, f"{PREFIX}*{SUFFIX}")), reverse=True):
        st = os.stat(p)
        out.append({"file": os.path.basename(p), "bytes": st.st_size,
                    "created_at": dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                    "source": "mtime"})
    return out

def write_manifest(dest_dir):
    entries, by_name = [], {}
    for e in json.load(open(os.path.join(dest_dir, MANIFEST))) if os.path.exists(
            os.path.join(dest_dir, MANIFEST)) else []:
        by_name[e.get("file")] = e
    for p in sorted(glob.glob(os.path.join(dest_dir, f"{PREFIX}*{SUFFIX}")), reverse=True):
        name = os.path.basename(p)
        e = by_name.get(name)
        if not e or e.get("bytes") != os.path.getsize(p):
            n, last, _ = _stats(p)
            e = {"file": name,
                 "created_at": dt.datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="seconds"),
                 "bytes": os.path.getsize(p), "sha256": _sha256(p),
                 "n_transactions": n, "last_transaction_date": last}
        entries.append(e)
    json.dump(entries, open(os.path.join(dest_dir, MANIFEST), "w"), indent=1)
    return entries

def list_backups(dest_dir=None):
    dest_dir = dest_dir or C.drive_backup_dir()
    mpath = os.path.join(dest_dir, MANIFEST)
    on_disk = {os.path.basename(p) for p in glob.glob(os.path.join(dest_dir, f"{PREFIX}*{SUFFIX}"))}
    if os.path.exists(mpath):
        try:
            man = json.load(open(mpath))
            listed = {e["file"] for e in man}
            if listed == on_disk:
                return sorted(man, key=lambda e: e["file"], reverse=True)
        except Exception:
            pass
    return _scan(dest_dir)          # manifest missing or out of step -> mtime fallback

def prune(dest_dir=None, keep_daily=None, keep_monthly=None):
    dest_dir = dest_dir or C.drive_backup_dir()
    keep_daily = C.keep_daily() if keep_daily is None else keep_daily
    keep_monthly = C.keep_monthly() if keep_monthly is None else keep_monthly
    files = sorted((os.path.basename(p) for p in
                    glob.glob(os.path.join(dest_dir, f"{PREFIX}*{SUFFIX}"))), reverse=True)
    keep = set(files[:keep_daily])
    seen = []
    for f in files:                                  # first-of-month survivors
        ym = f[len(PREFIX):len(PREFIX) + 6]
        if ym not in seen:
            seen.append(ym)
            if len(seen) <= keep_monthly: keep.add(f)
    removed = []
    for f in files:
        if f not in keep:
            os.remove(os.path.join(dest_dir, f)); removed.append(f)
    if removed: write_manifest(dest_dir)
    return removed

def _port_in_use(p):
    with socket.socket() as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", p)) == 0

def restore(file_name, dest_dir=None, force=False):
    dest_dir = dest_dir or C.drive_backup_dir()
    src = os.path.join(dest_dir, os.path.basename(file_name))
    if not os.path.exists(src): raise FileNotFoundError(src)
    if _port_in_use(C.port()) and not force:
        raise RuntimeError(f"the app is running on port {C.port()} — stop it before restoring")
    n, last, integrity = _stats(src)
    if integrity != "ok": raise RuntimeError(f"refusing to restore, integrity_check={integrity}")
    # Keep a copy of what we are about to replace. VACUUM INTO, not cp — the live DB is
    # in WAL mode, so a plain file copy can capture a torn state (same hazard as backups).
    if os.path.exists(D.DB_PATH):
        pre = D.DB_PATH + ".pre-restore"
        if os.path.exists(pre): os.remove(pre)
        con = sqlite3.connect(D.DB_PATH)
        try: con.execute("VACUUM INTO ?", (pre,))
        finally: con.close()

    # Overwrite through SQLite's online backup API rather than replacing the file.
    # Copying the file out from under a WAL database fails with "database is locked"
    # and can leave stale -wal/-shm alongside a swapped main file. The backup API takes
    # the right locks, rewrites page-by-page, and leaves the WAL coherent.
    srccon = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    dstcon = sqlite3.connect(D.DB_PATH)
    try:
        srccon.backup(dstcon)
        dstcon.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        srccon.close(); dstcon.close()
    return {"restored": os.path.basename(src), "n_transactions": n,
            "last_transaction_date": last, "resume": resume_info()}

def resume_info():
    """What the database now contains, per asset class — this is exactly the cursor a
    subsequent sync resumes from, so after a restore nothing is refetched or missed."""
    c = D.connect()
    try:
        row = c.execute("SELECT MAX(date) d FROM transactions WHERE asset='equity'").fetchone()
        eq = row["d"] if row else None
        row = c.execute("SELECT MAX(date) d FROM transactions WHERE asset='crypto'").fetchone()
        cr = row["d"] if row else None
        n = c.execute("SELECT COUNT(*) n FROM transactions").fetchone()["n"]
    finally:
        c.close()
    gap = None
    if eq:
        gap = (dt.date.today() - dt.date.fromisoformat(eq)).days
    return {"equity_through": eq, "crypto_through": cr, "n_transactions": n,
            "days_behind": gap,
            "next_action": ("up to date" if gap == 0 else
                            f"say 'sync' in Claude Code to fetch everything after {eq}"
                            if eq else "empty database — say 'bootstrap' in Claude Code")}

# ---------- staleness-driven automatic backup ----------
_last_check = [0.0]

def age_days(dest_dir=None):
    """Days since the newest snapshot; None when there are no backups at all."""
    try:
        d = dest_dir or C.drive_backup_dir()
    except C.ConfigError:
        return None
    files = glob.glob(os.path.join(d, f"{PREFIX}*{SUFFIX}"))
    if not files: return None
    newest = max(os.path.getmtime(f) for f in files)
    return (dt.datetime.now() - dt.datetime.fromtimestamp(newest)).total_seconds() / 86400.0

def maybe_auto_backup(max_age_days=None, throttle_sec=3600):
    """Back up only if the newest snapshot is older than max_age_days (default 7),
    or if none exists. Cheap: a directory mtime scan, not a snapshot, unless due.

    Called on server start and opportunistically while the app is open; throttled so
    a long-running server does not rescan on every request."""
    import time
    if time.time() - _last_check[0] < throttle_sec:
        return {"ran": False, "reason": "checked recently"}
    _last_check[0] = time.time()
    max_age = C.backup_max_age_days() if max_age_days is None else max_age_days
    try:
        C.drive_backup_dir()
    except C.ConfigError as e:
        return {"ran": False, "reason": "backups not configured", "error": str(e)}
    a = age_days()
    if a is not None and a < max_age:
        return {"ran": False, "reason": f"newest backup is {a:.1f}d old (< {max_age}d)",
                "age_days": round(a, 2)}
    try:
        rec = snapshot(kind="auto")
        prune()
        return {"ran": True, "reason": "no backup yet" if a is None
                else f"newest was {a:.1f}d old (>= {max_age}d)", "backup": rec}
    except Exception as e:
        return {"ran": False, "reason": "snapshot failed", "error": str(e)}

def status(dest_dir=None):
    try:
        d = dest_dir or C.drive_backup_dir()
    except C.ConfigError as e:
        return {"ready": False, "error": str(e), "backups": []}
    b = list_backups(d)
    newest = b[0] if b else None
    stale_days = None
    if newest:
        stale_days = (dt.datetime.now() - dt.datetime.fromisoformat(newest["created_at"])).days
    a = age_days(d)
    return {"ready": True, "dir": d, "backups": b, "newest": newest,
            "stale_days": stale_days, "count": len(b),
            "age_days": round(a, 2) if a is not None else None,
            "max_age_days": C.backup_max_age_days(),
            "due": (a is None or a >= C.backup_max_age_days()),
            "resume": resume_info()}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "snapshot":
        r = snapshot(kind=sys.argv[2] if len(sys.argv) > 2 else "manual")
        print(f"wrote {r['file']}  {r['bytes']:,} B  {r['n_transactions']:,} txns through {r['last_transaction_date']}")
        rm = prune()
        if rm: print("pruned:", ", ".join(rm))
    elif cmd == "auto":
        print(json.dumps(maybe_auto_backup(throttle_sec=0), indent=1, default=str))
    elif cmd == "list":
        for e in list_backups(): print(f"  {e['file']}  {e.get('bytes',0):,} B  {e.get('created_at')}")
    elif cmd == "restore":
        print(restore(sys.argv[2]))
    else:
        print(json.dumps(status(), indent=1)[:600])
