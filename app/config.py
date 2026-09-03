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
    src = PATH if os.path.exists(PATH) else (EXAMPLE if os.path.exists(EXAMPLE) else None)
    if src:
        with open(src) as fh:          # was leaking the handle on every call
            _cache = json.load(fh)
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
def demergers():
    """Optional {child: parent} overrides for demergers we do not ship."""
    return {str(k).upper(): str(v).upper()
            for k, v in (load().get("demergers") or {}).items()}
def splits():
    """{ticker: [{date, ratio}, ...]} — confirmed corporate actions that multiplied a
    share count. Populated by `python3 app/splits.py`, which proposes and never writes
    without confirmation."""
    out = {}
    for k, v in (load().get("splits") or {}).items():
        events = v if isinstance(v, list) else [v]
        clean = []
        for e in events:
            try:
                clean.append({"date": str(e["date"])[:10], "ratio": float(e["ratio"])})
            except (KeyError, TypeError, ValueError):
                continue
        if clean:
            out[str(k).upper()] = sorted(clean, key=lambda e: e["date"])
    return out


def ticker_aliases():
    """Optional {old_symbol: new_symbol} overrides for renames we do not ship."""
    return {str(k).upper(): str(v).upper()
            for k, v in (load().get("ticker_aliases") or {}).items()}
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


# ---------------------------------------------------------------- TradingAgents
# Optional, out-of-process integration with TauricResearch/TradingAgents.
# That project pulls ~22 pip dependencies (langchain, langgraph, pandas, yfinance...),
# so it is never vendored here and never imported into this process — Trans stays
# runnable with just python3. It is Apache 2.0, so this is an engineering boundary,
# not a licensing one. We hold a path to a clone the user installed, and use subprocess.

TA_DEFAULTS = {
    "enabled": False,
    "repo_path": "~/src/TradingAgents",
    "venv_python": "~/src/TradingAgents/.venv/bin/python",
    "backend": "ollama",
    "max_debate_rounds": 1,
    "max_risk_rounds": 1,
    "results_dir": "analysis",
    "ollama": {"backend_url": "http://localhost:11434/v1",
               "deep_think_llm": "qwen3:14b", "quick_think_llm": "qwen3:8b"},
    "anthropic": {"deep_think_llm": "claude-sonnet-5",
                  "quick_think_llm": "claude-haiku-4-5-20251001",
                  "require_consent": True, "max_cost_usd": 2.00},
}

def tradingagents():
    cfg = dict(TA_DEFAULTS)
    cfg.update(load().get("tradingagents") or {})
    for k in ("ollama", "anthropic"):
        merged = dict(TA_DEFAULTS[k]); merged.update(cfg.get(k) or {}); cfg[k] = merged
    return cfg

def ta_models(cfg=None):
    cfg = cfg or tradingagents()
    b = cfg.get("backend", "ollama")
    sub = cfg.get(b, {})
    return sub.get("deep_think_llm"), sub.get("quick_think_llm")

def runner_available():
    """Just the local pieces: config block, enabled, venv and clone. No LLM backend.
    Symbol lookup uses yfinance inside that venv and needs no API key, so it must not
    be gated behind one."""
    cfg = tradingagents()
    if "tradingagents" not in load():
        return False, 'No "tradingagents" block in config.json.'
    if not cfg.get("enabled"):
        return False, "Analysis is off. Set tradingagents.enabled = true in config.json."
    vp = os.path.expanduser(cfg.get("venv_python") or "")
    if not (vp and os.path.isfile(vp) and os.access(vp, os.X_OK)):
        return False, f"No TradingAgents interpreter at {vp or '(unset)'}."
    repo = os.path.expanduser(cfg.get("repo_path") or "")
    if not os.path.isdir(os.path.join(repo, "tradingagents")):
        return False, f"{repo or '(unset)'} is not a TradingAgents clone."
    return True, None


def analysis_available():
    """Why analysis can or cannot run. Always returns a dict — never raises — because
    the Research tab must render a helpful message rather than an error."""
    cfg = tradingagents()
    b = cfg.get("backend", "ollama")
    deep, quick = ta_models(cfg)
    out = {"ready": False, "backend": b, "deep_think_llm": deep,
           "quick_think_llm": quick, "paid": b != "ollama",
           "repo": os.path.expanduser(cfg.get("repo_path") or ""),
           "reason": None}
    if "tradingagents" not in load():
        out["reason"] = ('No "tradingagents" block in config.json. '
                         "Copy it from config.example.json — see docs/RESEARCH.md.")
        return out
    if not cfg.get("enabled"):
        out["reason"] = 'Analysis is off. Set tradingagents.enabled = true in config.json.'
        return out
    vp = os.path.expanduser(cfg.get("venv_python") or "")
    if not (vp and os.path.isfile(vp) and os.access(vp, os.X_OK)):
        out["reason"] = (f"No TradingAgents interpreter at {vp or '(unset)'}.\n"
                         "  python3 -m venv .venv && .venv/bin/pip install .   "
                         "(inside your TradingAgents clone)")
        return out
    repo = out["repo"]
    if not os.path.isdir(os.path.join(repo, "tradingagents")):
        out["reason"] = (f"{repo or '(unset)'} does not look like a TradingAgents clone "
                         "(no tradingagents/ package inside).\n"
                         "  git clone https://github.com/TauricResearch/TradingAgents")
        return out
    if b == "ollama":
        url = cfg["ollama"]["backend_url"]
        if not _ollama_up(url):
            out["reason"] = (f"Ollama is not answering at {url}.\n"
                             f"  ollama serve &\n  ollama pull {deep}")
            return out
    elif b == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            out["reason"] = ("ANTHROPIC_API_KEY is not set in this environment.\n"
                             "  export ANTHROPIC_API_KEY=...   then restart ./run.sh\n"
                             "  (the key is deliberately never stored in config.json)")
            return out
    else:
        out["reason"] = f'Unknown backend "{b}" — use "ollama" or "anthropic".'
        return out
    out["ready"] = True
    return out

def _ollama_up(url, timeout=1.5):
    import urllib.request, urllib.error
    base = url.rstrip("/")
    if base.endswith("/v1"): base = base[:-3]
    try:
        urllib.request.urlopen(base + "/api/tags", timeout=timeout).read(1)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    print("configured:", is_configured())
    for f in (account_number, port, keep_daily, keep_monthly):
        try: print(f" {f.__name__:22}", f())
        except ConfigError as e: print(f" {f.__name__:22} ERROR: {e}")
    print(" detected roots        ", detect_drive_roots() or "(none)")
    a = analysis_available()
    print(" analysis              ", "ready" if a["ready"] else a["reason"].splitlines()[0])
    try: print(" drive_backup_dir      ", drive_backup_dir())
    except ConfigError as e: print(" drive_backup_dir       NOT READY:", str(e).splitlines()[0])
