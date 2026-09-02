"""List holdings that have no sector mapping yet.

A new user's config has no ticker_sectors, so the Fundamentals tab falls back to a
generic metric set. This prints what needs classifying and the valid sector keys, so
Claude Code (or a human) can fill config.json in one pass.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as D, sectors as S, config as C

def main():
    c = D.connect()
    held = [r["ticker"] for r in c.execute("SELECT ticker FROM positions ORDER BY ticker")]
    if not held:
        held = [r["ticker"] for r in c.execute(
            "SELECT DISTINCT ticker FROM transactions ORDER BY ticker")]
    c.close()
    mapped = S.TICKER_SECTOR()
    missing = [t for t in held if t.upper() not in mapped]
    print(f"{len(held)} tickers held; {len(mapped)} mapped; {len(missing)} unmapped\n")
    if missing:
        print("unmapped:", " ".join(missing), "\n")
        print("valid sector keys:")
        for k in sorted(S.SECTOR_METRICS):
            print(f"  {k:18} {S.label(k)}")
        print('\nAdd them to config.json under "ticker_sectors", e.g.')
        print(json.dumps({"ticker_sectors": {missing[0]: "software"}}, indent=2))
    else:
        print("every holding is classified.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
