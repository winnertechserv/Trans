"""Optional bridge to TauricResearch/TradingAgents.

Trans is stdlib-only; TradingAgents needs ~22 pip packages and has no license, so it is
never imported, vendored or redistributed here. We spawn *our* runner script
(tools/ta_runner.py) with *their* venv interpreter, and read back a tree of markdown.
If TradingAgents is absent, everything in this module degrades to a clear message.

Reports are stored in the `ai_notes` table; every run is written to `token_ledger` —
free for local Ollama, with actual token counts and cost for the paid backend.
"""
import os, sys, json, glob, time, threading, subprocess, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as D, config as C, costs as CO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(ROOT, "tools", "ta_runner.py")

# section file -> (kind, human label, display order)
SECTIONS = [
    ("portfolio/decision.md",   "decision",      "Final decision",        0),
    ("trading/trader.md",       "trader",        "Trader plan",           1),
    ("research/manager.md",     "research_mgr",  "Research manager",      2),
    ("research/bull.md",        "bull",          "Bull case",             3),
    ("research/bear.md",        "bear",          "Bear case",             4),
    ("analysts/fundamentals.md","fundamentals",  "Fundamentals analyst",  5),
    ("analysts/market.md",      "market",        "Market analyst",        6),
    ("analysts/news.md",        "news",          "News analyst",          7),
    ("analysts/sentiment.md",   "sentiment",     "Sentiment analyst",     8),
    ("risk/aggressive.md",      "risk_aggr",     "Risk — aggressive",     9),
    ("risk/neutral.md",         "risk_neutral",  "Risk — neutral",       10),
    ("risk/conservative.md",    "risk_cons",     "Risk — conservative",  11),
]
LABEL = {k: (lbl, order) for _, k, lbl, order in SECTIONS}

_jobs = {}
_lock = threading.Lock()


def available():
    return C.analysis_available()


def results_root():
    cfg = C.tradingagents()
    d = cfg.get("results_dir") or "analysis"
    return d if os.path.isabs(d) else os.path.join(ROOT, d)


# ---------------------------------------------------------------- cost estimate
# ~9 agents each seeing a few thousand tokens of context, multiplied by debate rounds.
# Deliberately reported as a wide range: multi-agent debate length is genuinely variable
# and quoting a precise number here would be false precision.
_AGENTS = 9
_CTX_LOW, _CTX_HIGH = 3000, 9000
_OUT_LOW, _OUT_HIGH = 700, 2000

def estimate(ticker=None):
    cfg = C.tradingagents()
    b = cfg.get("backend", "ollama")
    deep, quick = C.ta_models(cfg)
    if b == "ollama":
        return {"paid": False, "backend": b, "low": 0.0, "high": 0.0,
                "deep_think_llm": deep, "quick_think_llm": quick, "verified": True,
                "note": "Local model — no API charge."}
    rounds = max(1, int(cfg.get("max_debate_rounds", 1))) + \
             max(1, int(cfg.get("max_risk_rounds", 1)))
    calls = _AGENTS * rounds
    lo = CO.estimate(deep, _CTX_LOW * calls, _OUT_LOW * calls)
    hi = CO.estimate(deep, _CTX_HIGH * calls, _OUT_HIGH * calls)
    p = CO.pricing()
    return {"paid": True, "backend": b, "ticker": ticker,
            "low": lo.get("cost_usd"), "high": hi.get("cost_usd"),
            "deep_think_llm": deep, "quick_think_llm": quick,
            "calls": calls, "verified": p.get("verified", False),
            "max_cost_usd": cfg["anthropic"].get("max_cost_usd"),
            "note": ("Estimate only — a multi-agent debate's token use varies widely. "
                     "Actual cost is logged after the run.")}


# ---------------------------------------------------------------- running
def _env_for(cfg):
    b = cfg.get("backend", "ollama")
    sub = cfg.get(b, {})
    env = dict(os.environ)
    env.update({
        "TRADINGAGENTS_LLM_PROVIDER": b,
        "TRADINGAGENTS_DEEP_THINK_LLM": str(sub.get("deep_think_llm") or ""),
        "TRADINGAGENTS_QUICK_THINK_LLM": str(sub.get("quick_think_llm") or ""),
        "TRADINGAGENTS_MAX_DEBATE_ROUNDS": str(cfg.get("max_debate_rounds", 1)),
        "TRADINGAGENTS_MAX_RISK_ROUNDS": str(cfg.get("max_risk_rounds", 1)),
        "PYTHONPATH": os.path.expanduser(cfg.get("repo_path") or ""),
    })
    if b == "ollama":
        env["TRADINGAGENTS_LLM_BACKEND_URL"] = sub.get("backend_url", "")
    return env


def run(ticker, date=None, consented=False):
    """Start a background run. Returns a job dict immediately — a local multi-agent
    debate takes minutes to tens of minutes, so this must never block the HTTP thread."""
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return {"ok": False, "error": "no ticker"}
    a = available()
    if not a["ready"]:
        return {"ok": False, "error": a["reason"]}
    cfg = C.tradingagents()
    est = estimate(ticker)
    if est["paid"]:
        if cfg["anthropic"].get("require_consent", True) and not consented:
            return {"ok": False, "error": "consent required", "estimate": est,
                    "needs_consent": True}
        cap = cfg["anthropic"].get("max_cost_usd")
        if cap is not None and est.get("high") is not None and est["high"] > cap:
            return {"ok": False, "needs_consent": False, "estimate": est,
                    "error": (f"Estimated cost up to ${est['high']:.2f} exceeds the "
                              f"max_cost_usd cap of ${cap:.2f}. Raise the cap in "
                              f"config.json or reduce max_debate_rounds.")}
    date = date or dt.date.today().isoformat()
    outdir = os.path.join(results_root(), ticker, date)
    job_id = f"{ticker}-{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}"
    job = {"id": job_id, "ticker": ticker, "date": date, "state": "running",
           "started": dt.datetime.now().isoformat(timespec="seconds"),
           "backend": cfg.get("backend"), "outdir": outdir, "estimate": est}
    with _lock:
        _jobs[job_id] = job
    threading.Thread(target=_worker, args=(job, cfg, outdir), daemon=True).start()
    return {"ok": True, **job}


def _worker(job, cfg, outdir):
    t0 = time.time()
    os.makedirs(outdir, exist_ok=True)
    cmd = [os.path.expanduser(cfg["venv_python"]), RUNNER,
           "--ticker", job["ticker"], "--date", job["date"], "--out", outdir]
    try:
        p = subprocess.run(cmd, env=_env_for(cfg), capture_output=True, text=True,
                           cwd=os.path.expanduser(cfg["repo_path"]))
        job["returncode"] = p.returncode
        tail = (p.stderr or p.stdout or "").strip().splitlines()[-5:]
        if p.returncode != 0:
            job.update(state="failed", error="\n".join(tail) or "runner failed")
        else:
            n = ingest_report(outdir, job["ticker"], job["date"], cfg)
            job.update(state="finished", sections=n)
    except Exception as e:
        job.update(state="failed", error=str(e))
    finally:
        job["seconds"] = round(time.time() - t0, 1)
        job["finished"] = dt.datetime.now().isoformat(timespec="seconds")
        _log_run(job, cfg, outdir)


def _log_run(job, cfg, outdir):
    """Record the run in token_ledger — actual usage, never the estimate."""
    b = cfg.get("backend", "ollama")
    deep, _ = C.ta_models(cfg)
    tin = tout = 0
    usage_path = os.path.join(outdir, "usage.json")
    if os.path.exists(usage_path):
        try:
            u = json.load(open(usage_path))
            tin = int(u.get("input_tokens") or 0); tout = int(u.get("output_tokens") or 0)
        except Exception:
            pass
    if b == "ollama":
        source, cost = "ollama_local", 0.0
    else:
        source = "anthropic_api"
        cost = (CO.estimate(deep, tin, tout).get("cost_usd") or 0.0) if (tin or tout) else 0.0
    c = D.connect()
    try:
        D.log_tokens(c, f"analysis:{job['ticker']}", source, model=deep,
                     input_tokens=tin, output_tokens=tout, cost_usd=cost,
                     consented=1 if b != "ollama" else 0,
                     note=f"{job['state']} in {job.get('seconds')}s"
                          + ("" if (tin or tout) else " (no usage reported)"))
    finally:
        c.close()


def status(job_id=None):
    with _lock:
        if job_id: return _jobs.get(job_id) or {"error": "unknown job"}
        return sorted(_jobs.values(), key=lambda j: j["started"], reverse=True)


# ---------------------------------------------------------------- storing / reading
def ingest_report(outdir, ticker, date, cfg=None):
    cfg = cfg or C.tradingagents()
    deep, _ = C.ta_models(cfg)
    src = f"tradingagents/{cfg.get('backend')}/{deep}"
    created = dt.datetime.now().isoformat(timespec="seconds")
    c = D.connect(); n = 0
    try:
        c.execute("DELETE FROM ai_notes WHERE ticker=? AND kind LIKE 'ta:%'"
                  " AND created_at LIKE ?", (ticker, f"{date}%"))
        for rel, kind, _lbl, _o in SECTIONS:
            p = os.path.join(outdir, rel)
            if not os.path.exists(p): continue
            body = open(p, encoding="utf-8", errors="ignore").read().strip()
            if not body: continue
            c.execute("INSERT INTO ai_notes(ticker,created_at,kind,content,source)"
                      " VALUES(?,?,?,?,?)",
                      (ticker, f"{date}T{created[11:]}", f"ta:{kind}", body, src))
            n += 1
        c.commit()
    finally:
        c.close()
    return n


def reports(ticker=None):
    c = D.connect()
    try:
        if ticker:
            rows = [dict(r) for r in c.execute(
                "SELECT ticker,created_at,kind,content,source FROM ai_notes"
                " WHERE ticker=? AND kind LIKE 'ta:%'"
                " AND created_at=(SELECT MAX(created_at) FROM ai_notes"
                "                 WHERE ticker=? AND kind LIKE 'ta:%')",
                (ticker.upper(), ticker.upper()))]
            for r in rows:
                k = r["kind"][3:]
                r["label"], r["order"] = LABEL.get(k, (k, 99))
            rows.sort(key=lambda r: r["order"])
            return {"ticker": ticker.upper(), "sections": rows,
                    "created_at": rows[0]["created_at"] if rows else None,
                    "source": rows[0]["source"] if rows else None}
        out = []
        for r in c.execute(
            "SELECT ticker, MAX(created_at) created_at, COUNT(*) n, source FROM ai_notes"
            " WHERE kind LIKE 'ta:%' GROUP BY ticker ORDER BY created_at DESC"):
            d = dict(r)
            dec = c.execute("SELECT content FROM ai_notes WHERE ticker=? AND kind='ta:decision'"
                            " ORDER BY created_at DESC LIMIT 1", (d["ticker"],)).fetchone()
            d["decision_excerpt"] = (dec["content"][:180] + "…") if dec else None
            out.append(d)
        return out
    finally:
        c.close()


if __name__ == "__main__":
    a = available()
    print("ready:", a["ready"], "| backend:", a["backend"])
    if not a["ready"]: print(a["reason"])
    print("estimate:", json.dumps(estimate("NVDA"), indent=1))
