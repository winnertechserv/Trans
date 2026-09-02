"""Interactive restore. Never auto-picks a backup."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backup as B, config as C

def main():
    try:
        st = B.status()
    except C.ConfigError as e:
        print(e); return 1
    if not st["ready"]:
        print("Backups are not available:\n" + st["error"]); return 1
    b = st["backups"]
    if not b:
        print(f"No backups found in:\n  {st['dir']}\n\n"
              "Nothing to restore. Open Claude Code here and say: bootstrap"); return 1
    print(f"\nBackups in {st['dir']}\n")
    for i, e in enumerate(b, 1):
        n = e.get("n_transactions")
        print(f"  {i:>2}. {e['file']}   {(e.get('created_at') or '').replace('T',' ')}"
              f"   {(e.get('bytes',0))/1048576:.1f} MB"
              + (f"   {n:,} transactions" if n is not None else ""))
    print()
    try:
        pick = input(f"Restore which? [1-{len(b)}, Enter to cancel]: ").strip()
    except EOFError:
        pick = ""
    if not pick:
        print("Cancelled."); return 0
    try:
        chosen = b[int(pick) - 1]
    except (ValueError, IndexError):
        print("Not a valid choice."); return 1
    print(f"\nThis overwrites portfolio.db (a copy is kept as portfolio.db.pre-restore).")
    if input(f"Restore {chosen['file']}? [y/N]: ").strip().lower() != "y":
        print("Cancelled."); return 0
    r = B.restore(chosen["file"])
    print(f"\nRestored {r['restored']} — {r['n_transactions']:,} transactions "
          f"through {r['last_transaction_date']}.\nNow run ./run.sh")
    return 0

if __name__ == "__main__":
    sys.exit(main())
