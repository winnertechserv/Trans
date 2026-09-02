"""Configuration. Real values live in config.json (gitignored); config.example.json
is the committed template. Nothing here may ever contain a real account number.
"""
import json, os, glob, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "config.json")
EXAMPLE = os.path.join(ROOT, "config.example.json")

class ConfigError(RuntimeError): pass

_cache = None

def load(reload=False):
    global _cache
    if _cache is not None and not reload: return _cache
    if os.path.exists(PATH):
        _cache = json.load(open(PATH))
    elif os.path.exists(EXAMPLE):
        _cache = json.load(open(EXAMPLE))
    else:
        _cache = {}
    return _cache

def _req(path, hint):
    cur = load()
    for k in path.split("."):
        cur = (cur or {}).get(k)
    if not cur or cur == "REPLACE_ME":
        raise ConfigError(
            f"config.json is missing '{path}'.\n"
            f"  cp config.example.json config.json  and set it.\n  {hint}")
    return cur

def account_number():        return _req("broker.account_number", "Your Robinhood account number.")
def crypto_account_number():
    try: return _req("broker.crypto_account_number", "")
    except ConfigError: return account_number()
def port():                  return int(load().get("port") or 8787)
def keep_daily():            return int((load().get("backup") or {}).get("keep_daily", 30))
def keep_monthly():          return int((load().get("backup") or {}).get("keep_monthly", 12))

BACKUP_FOLDER = "trans"

def detect_drive_roots():
    """Every plausible synced-Drive root on macOS and Linux, newest-looking first.

    macOS  : Google Drive for Desktop mounts under ~/Library/CloudStorage/GoogleDrive-<acct>
             (older builds used ~/Google Drive).
    Linux  : no official client. Common third-party mounts are rclone,
             google-drive-ocamlfuse and Insync, which land in predictable places.
    """
    h = os.path.expanduser("~")
    pats = [
        # macOS, current and legacy
        os.path.join(h, "Library/CloudStorage/GoogleDrive-*/My Drive"),
        os.path.join(h, "Library/CloudStorage/GoogleDrive-*"),
        os.path.join(h, "Google Drive/My Drive"),
        os.path.join(h, "Google Drive"),
        # Linux: Insync
        os.path.join(h, "Insync/*/Google Drive"),
        os.path.join(h, "Insync/*/*"),
        # Linux: rclone / ocamlfuse / manual mounts
        os.path.join(h, "GoogleDrive"), os.path.join(h, "gdrive"),
        os.path.join(h, "google-drive"), os.path.join(h, "Drive"),
        "/mnt/gdrive", "/mnt/google-drive", "/media/gdrive",
        os.path.join(h, "mnt/gdrive"),
    ]
    seen, out = set(), []
    for p in pats:
        for d in sorted(glob.glob(p)):
            r = os.path.realpath(d)
            if os.path.isdir(d) and r not in seen and os.access(d, os.W_OK):
                seen.add(r); out.append(d)
    return out

def drive_backup_dir(must_exist=True, autodetect=True):
    """Configured path wins. Otherwise auto-detect a Drive root and use
    <root>/portfolio-backups — so a fresh clone on any Mac or Linux box just works."""
    try:
        d = os.path.expanduser(_req("drive_backup_dir", ""))
        if os.path.isdir(d) or not autodetect:
            if must_exist and not os.path.isdir(d):
                raise ConfigError(_missing(d))
            return d
    except ConfigError:
        d = None
    if autodetect:
        for root in detect_drive_roots():
            cand = os.path.join(root, BACKUP_FOLDER)
            try:
                os.makedirs(cand, exist_ok=True)
                return cand
            except OSError:
                continue
    raise ConfigError(_missing(d))

def _missing(d):
    roots = detect_drive_roots()
    if roots:
        return ("Backup folder not usable:\n  %s\nDetected Drive roots you could use:\n  %s"
                % (d or "(not set)", "\n  ".join(roots)))
    if sys.platform == "darwin":
        hint = ("Install Google Drive for Desktop and sign in, then enable the Drive "
                "File Provider extension (System Settings > General > Login Items & "
                "Extensions). Expected under ~/Library/CloudStorage/GoogleDrive-*.")
    else:
        hint = ("No synced Google Drive folder found. On Linux there is no official "
                "client — mount Drive with rclone, google-drive-ocamlfuse or Insync, "
                "then set \"drive_backup_dir\" in config.json to that folder.")
    return "No Google Drive folder found.\n  %s" % hint

def backup_max_age_days():
    return int((load().get("backup") or {}).get("max_age_days", 7))

def is_configured():
    try:
        account_number(); return True
    except ConfigError:
        return False

if __name__ == "__main__":
    print("configured:", is_configured())
    for f in (account_number, port, keep_daily, keep_monthly):
        try: print(f" {f.__name__:22}", f())
        except ConfigError as e: print(f" {f.__name__:22} ERROR: {e}")
    print(" detected roots        ", detect_drive_roots() or "(none)")
    try: print(" drive_backup_dir      ", drive_backup_dir())
    except ConfigError as e: print(" drive_backup_dir       NOT READY:", str(e).splitlines()[0])
