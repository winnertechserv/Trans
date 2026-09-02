"""Optional bridge to TauricResearch/TradingAgents.

Trans is stdlib-only; TradingAgents needs ~22 pip packages (langchain, langgraph,
pandas, yfinance...), so it is never imported or vendored here — doing so would end the
"works with just python3" property for a feature most users never enable. We spawn *our*
runner script (tools/ta_runner.py) with *their* venv interpreter and read back a tree of
markdown. TradingAgents is Apache 2.0, so this separation is an engineering choice, not a
licensing one. If TradingAgents is absent, this module degrades to a clear message.

Reports are stored in the `ai_notes` table; every run is written to `token_ledger` —
free for local Ollama, with actual token counts and cost for the paid backend.
"""
import os, sys, json, glob, time, threading, subprocess, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as D, config as C, costs as CO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(ROOT, "tools", "ta_runner.py")

# section file -> (kind, human label, display order)
# (directory-suffix, filename) -> kind, label, display order.
# write_report_tree() numbers its directories (1_analysts, 2_research, 3_trading,
# 4_risk, 5_portfolio), so match on the suffix after the number rather than a fixed
# path — an earlier version hardcoded unnumbered names and silently ingested nothing.
SECTIONS = [
    ("portfolio", "decision.md",     "decision",      "Final decision",        0),
    ("trading",   "trader.md",       "trader",        "Trader plan",           1),
    ("research",  "manager.md",      "research_mgr",  "Research manager",      2),
    ("research",  "bull.md",         "bull",          "Bull case",             3),
    ("research",  "bear.md",         "bear",          "Bear case",             4),
    ("analysts",  "fundamentals.md", "fundamentals",  "Fundamentals analyst",  5),
    ("analysts",  "market.md",       "market",        "Market analyst",        6),
    ("analysts",  "news.md",         "news",          "News analyst",          7),
    ("analysts",  "sentiment.md",    "sentiment",     "Sentiment analyst",     8),
    ("risk",      "aggressive.md",   "risk_aggr",     "Risk — aggressive",     9),
    ("risk",      "neutral.md",      "risk_neutral",  "Risk — neutral",       10),
    ("risk",      "conservative.md", "risk_cons",     "Risk — conservative",  11),
]

def _find_section(outdir, dirsuffix, filename):
    """Locate <outdir>/<anything ending in dirsuffix>/<filename>."""
    for d in sorted(glob.glob(os.path.join(outdir, "*"))):
        if os.path.isdir(d) and os.path.basename(d).endswith(dirsuffix):
            p = os.path.join(d, filename)
            if os.path.exists(p):
                return p
    p = os.path.join(outdir, dirsuffix, filename)   # unnumbered fallback
    return p if os.path.exists(p) else None
LABEL = {k: (lbl, order) for _, _, k, lbl, order in SECTIONS}

# The full pipeline, in execution order, with the node names TradingAgents actually
# registers (read from tradingagents/graph/setup.py in the installed clone). Showing the
# whole roster up front means a run reads as progress through a known list rather than a
# table that grows mysteriously. Analysts are selectable in TradingAgents, so some may
# never run — they are marked skipped at the end rather than left hanging.
PIPELINE = [
    ("Market Analyst",       "Market analyst",        "Analysts"),
    ("Sentiment Analyst",    "Sentiment analyst",     "Analysts"),
    ("News Analyst",         "News analyst",          "Analysts"),
    ("Fundamentals Analyst", "Fundamentals analyst",  "Analysts"),
    ("Bull Researcher",      "Bull researcher",       "Research debate"),
    ("Bear Researcher",      "Bear researcher",       "Research debate"),
    ("Research Manager",     "Research manager",      "Research debate"),
    ("Trader",               "Trader",                "Trading"),
    ("Aggressive Analyst",   "Risk — aggressive",     "Risk debate"),
    ("Conservative Analyst", "Risk — conservative",   "Risk debate"),
    ("Neutral Analyst",      "Risk — neutral",        "Risk debate"),
    ("Portfolio Manager",    "Portfolio manager",     "Decision"),
]
AGENT_LABEL = {n: lbl for n, lbl, _ in PIPELINE}
AGENT_STAGE = {n: st for n, _, st in PIPELINE}

_jobs = {}
_lock = threading.Lock()


def available():
    return C.analysis_available()


def results_root():
    cfg = C.tradingagents()
    d = cfg.get("results_dir") or "analysis"
    return d if os.path.isabs(d) else os.path.join(ROOT, d)


# ---------------------------------------------------------------- cost estimate
# Calibrated against a real MSFT run (2026-09-02) read from the Anthropic console.
# Two corrections to the original guesswork, which pulled in opposite directions:
#   1. TradingAgents does NOT run every call on the deep model. That run showed 2 calls
#      on the deep model and 15 on the quick one. Pricing everything at deep rates
#      overstated cost badly.
#   2. Per-call context is much larger than assumed — ~12.7k input and ~2.8k output on
#      average, against an original guess of 3-9k in / 0.7-2k out.
# Those errors partly cancelled, which is why the old range happened to bracket the
# actual. The console list was truncated, so the observed $0.56 is a floor, not a total;
# the high end below is deliberately generous rather than fitted to a censored sample.
_DEEP_CALLS_LO, _DEEP_CALLS_HI = 2, 4
_QUICK_CALLS_LO, _QUICK_CALLS_HI = 15, 30
_IN_LO, _IN_HI = 8000, 18000
_OUT_LO, _OUT_HI = 1200, 4000

def estimate(ticker=None, model=None):
    cfg = C.tradingagents()
    b = cfg.get("backend", "ollama")
    deep, quick = C.ta_models(cfg)
    if model and b != "ollama":
        deep = model
    if b == "ollama":
        return {"paid": False, "backend": b, "low": 0.0, "high": 0.0,
                "deep_think_llm": deep, "quick_think_llm": quick, "verified": True,
                "models": cfg.get("anthropic", {}).get("models", []),
                "note": "Local model — no API charge."}
    rounds = max(1, int(cfg.get("max_debate_rounds", 1)))
    dl, dh = _DEEP_CALLS_LO, _DEEP_CALLS_HI * rounds
    ql, qh = _QUICK_CALLS_LO, _QUICK_CALLS_HI * rounds

    def side(m, calls, tin, tout):
        e = CO.estimate(m, calls * tin, calls * tout)
        return e.get("cost_usd") or 0.0

    low = side(deep, dl, _IN_LO, _OUT_LO) + side(quick, ql, _IN_LO, _OUT_LO)
    high = side(deep, dh, _IN_HI, _OUT_HI) + side(quick, qh, _IN_HI, _OUT_HI)
    p = CO.pricing()
    return {"paid": True, "backend": b, "ticker": ticker,
            "models": cfg.get("anthropic", {}).get("models", []),
            "low": round(low, 4), "high": round(high, 4),
            "deep_think_llm": deep, "quick_think_llm": quick,
            "calls": f"{dl + ql}–{dh + qh}",
            "deep_calls": f"{dl}–{dh}", "quick_calls": f"{ql}–{qh}",
            "verified": p.get("verified", False),
            "max_cost_usd": cfg["anthropic"].get("max_cost_usd"),
            "note": ("Estimate only, calibrated on one observed run. Most calls use the "
                     "quick model; only the heavy reasoning steps use the model you pick. "
                     "Actual usage is logged after the run.")}


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


def run(ticker, date=None, consented=False, model=None):
    """Start a background run. Returns a job dict immediately — a local multi-agent
    debate takes minutes to tens of minutes, so this must never block the HTTP thread."""
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return {"ok": False, "error": "no ticker"}
    a = available()
    if not a["ready"]:
        return {"ok": False, "error": a["reason"]}
    cfg = C.tradingagents()
    est = estimate(ticker, model)
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
    if model and cfg.get("backend") != "ollama":
        cfg = json.loads(json.dumps(cfg))          # per-run override, config untouched
        cfg["anthropic"]["deep_think_llm"] = model
    job = {"id": job_id, "ticker": ticker, "date": date, "state": "running",
           "started": dt.datetime.now().isoformat(timespec="seconds"),
           "backend": cfg.get("backend"), "outdir": outdir, "estimate": est,
           "model": model or C.ta_models(cfg)[0]}
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
    deep = job.get("model") or C.ta_models(cfg)[0]
    tin = tout = 0
    usage_path = os.path.join(outdir, "usage.json")
    if os.path.exists(usage_path):
        try:
            u = json.load(open(usage_path))
            tin = int(u.get("input_tokens") or 0); tout = int(u.get("output_tokens") or 0)
        except Exception:
            pass
    source = "ollama_local" if b == "ollama" else "anthropic_api"
    consented = 0 if b == "ollama" else 1
    paid = b != "ollama"

    by_agent = []
    if os.path.exists(usage_path):
        try:
            by_agent = json.load(open(usage_path)).get("by_agent") or []
        except Exception:
            pass

    c = D.connect()
    try:
        if by_agent:
            # One row per agent, so the ledger answers "which analyst cost what".
            # Rows sum to the run total — no separate summary row, which would double-count.
            for a in by_agent:
                ai, ao = int(a.get("input") or 0), int(a.get("output") or 0)
                cost = (CO.estimate(deep, ai, ao).get("cost_usd") or 0.0) if paid else 0.0
                D.log_tokens(c, f"analysis:{job['ticker']}/{a.get('agent','?')}", source,
                             model=deep, input_tokens=ai, output_tokens=ao, cost_usd=cost,
                             consented=consented,
                             note=f"{a.get('calls',0)} call(s), {job['state']}")
        else:
            cost = (CO.estimate(deep, tin, tout).get("cost_usd") or 0.0) if (paid and (tin or tout)) else 0.0
            D.log_tokens(c, f"analysis:{job['ticker']}", source, model=deep,
                         input_tokens=tin, output_tokens=tout, cost_usd=cost,
                         consented=consented,
                         note=f"{job['state']} in {job.get('seconds')}s"
                              + ("" if (tin or tout) else " (no per-agent usage reported)"))
    finally:
        c.close()
    job["by_agent"] = by_agent


def _with_progress(job):
    """Merge the runner's live progress.json into a job record.

    The runner rewrites that file after every LLM event, so this is how the UI can show
    which agent is working and what it has spent while the run is still going."""
    j = dict(job)
    p = os.path.join(job.get("outdir") or "", "progress.json")
    if os.path.exists(p):
        try:
            prog = json.load(open(p))
            deep = job.get("model") or C.ta_models()[0]
            paid = C.tradingagents().get("backend") != "ollama"
            for a in prog.get("agents", []):
                a["label"] = AGENT_LABEL.get(a["agent"], a["agent"])
                a["cost_usd"] = (CO.estimate(deep, a["input"], a["output"]).get("cost_usd")
                                 if paid and (a["input"] or a["output"]) else 0.0)
            seen = {a["agent"]: a for a in prog.get("agents", [])}
            merged, extra = [], []
            for node, lbl, stage in PIPELINE:
                a = seen.pop(node, None)
                merged.append(a if a else {"agent": node, "input": 0, "output": 0,
                                           "calls": 0, "state": "pending",
                                           "label": lbl, "stage": stage, "cost_usd": 0.0})
                if a: a["stage"] = stage
            for node, a in seen.items():          # anything we did not anticipate
                a["stage"] = "Other"; a.setdefault("label", node); extra.append(a)
            prog["agents"] = merged + extra
            if prog.get("phase") == "finished":
                for a in prog["agents"]:
                    if a["state"] == "pending": a["state"] = "skipped"
            t = prog.get("totals", {})
            prog["cost_usd"] = (CO.estimate(deep, t.get("input_tokens", 0),
                                            t.get("output_tokens", 0)).get("cost_usd")
                                if paid else 0.0)
            j["progress"] = prog
        except Exception:
            pass
    return j


def status(job_id=None):
    with _lock:
        if job_id:
            j = _jobs.get(job_id)
            return _with_progress(j) if j else {"error": "unknown job"}
        jobs = sorted(_jobs.values(), key=lambda j: j["started"], reverse=True)
    return [_with_progress(j) for j in jobs]


# ---------------------------------------------------------------- storing / reading
def ingest_report(outdir, ticker, date, cfg=None):
    """Store a report tree. The model recorded is the one that actually produced the
    run — read from usage.json — not whatever the config happens to say now. Re-ingesting
    an old report after changing the default model used to relabel it incorrectly."""
    cfg = cfg or C.tradingagents()
    deep, _ = C.ta_models(cfg)
    backend = cfg.get("backend")
    up = os.path.join(outdir, "usage.json")
    if os.path.exists(up):
        try:
            u = json.load(open(up))
            deep = u.get("deep_think_llm") or deep
            backend = u.get("provider") or backend
        except Exception:
            pass
    src = f"tradingagents/{backend}/{deep}"
    created = dt.datetime.now().isoformat(timespec="seconds")
    c = D.connect(); n = 0
    try:
        c.execute("DELETE FROM ai_notes WHERE ticker=? AND kind LIKE 'ta:%'"
                  " AND created_at LIKE ?", (ticker, f"{date}%"))
        for dsuf, fname, kind, _lbl, _o in SECTIONS:
            p = _find_section(outdir, dsuf, fname)
            if not p: continue
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
