#!/usr/bin/env python3
"""Refuse to commit personal data. Run before every commit.

Checks the files git would actually track — not the working tree — so anything
correctly ignored is invisible here.
"""
import subprocess, sys, os, json, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

def tracked():
    r = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                       capture_output=True, text=True)
    return [f for f in r.stdout.splitlines() if f]

FORBIDDEN_PATHS = [
    (re.compile(r"\.db$|\.db-wal$|\.db-shm$"), "SQLite database"),
    # Anything under a sync folder is raw broker data whatever its extension. The old
    # rule named only .json, so Zerodha tradebook CSVs (account code in the filename)
    # and Paytm statement PDFs (PAN, address, phone) were reported clean and committed.
    (re.compile(r"^sync/(inbox|archive)/(?!\.gitkeep$).+"), "raw broker data"),
    (re.compile(r"\.pdf$", re.I), "PDF — broker statements carry PAN and address"),
    (re.compile(r"tradebook|_statement|Transactions_\d", re.I), "broker export"),
    (re.compile(r"^(transactions|positions)\.csv$"), "exported holdings"),
    (re.compile(r"^report\.json$"), "generated report"),
    (re.compile(r"^config\.json$"), "local config with account number"),
]

def secrets():
    """Account numbers from config.json, so the check adapts to whoever runs it."""
    out = set()
    p = os.path.join(ROOT, "config.json")
    if os.path.exists(p):
        b = json.load(open(p)).get("broker", {})
        for v in b.values():
            if v and v != "REPLACE_ME" and len(str(v)) >= 6: out.add(str(v))
    return out

def holdings():
    """Your ticker list is personal — this repo is shared, so it must stay in config."""
    p = os.path.join(ROOT, "config.json")
    if not os.path.exists(p): return set()
    return {t.upper() for t in (json.load(open(p)).get("ticker_sectors") or {})}

def main():
    files, bad = tracked(), []
    for f in files:
        for rx, why in FORBIDDEN_PATHS:
            if rx.search(f): bad.append(f"  {f}  <- {why}")
    sec = secrets()
    if sec:
        for f in files:
            if f in ("config.example.json",) or not os.path.isfile(f): continue
            try: txt = open(f, "r", errors="ignore").read()
            except Exception: continue
            for s in sec:
                if s in txt: bad.append(f"  {f}  <- contains account number {s[:3]}***")
    # a shared repo should not disclose which tickers you hold
    held = holdings()
    if held:
        for f in files:
            if not f.endswith((".py", ".md", ".html")) or f in ("config.example.json",):
                continue
            if not os.path.isfile(f): continue
            try: txt = open(f, "r", errors="ignore").read()
            except Exception: continue
            hits = {t for t in held if re.search(r"\b%s\b" % re.escape(t), txt)}
            # 3+ matches in one file means a holdings list, not an incidental mention
            if len(hits) >= 3:
                bad.append(f"  {f}  <- names {len(hits)} of your holdings ({', '.join(sorted(hits)[:5])}…)")
    if bad:
        print("REFUSING: personal data would be committed:\n" + "\n".join(sorted(set(bad))))
        print("\nFix .gitignore, then: git rm --cached <file>")
        return 1
    print(f"clean — {len(files)} files safe to commit"
          + (f"; {len(sec)} account number(s) checked for" if sec else ""))
    return 0

if __name__ == "__main__":
    sys.exit(main())
