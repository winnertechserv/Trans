"""Plain-English rewrite of a report, using the Anthropic API directly.

Why not the SDK: Trans is standard-library only. This is a single HTTPS POST, so
urllib does it without adding a dependency for one call.

Billed, so it follows the same rules as everything else that spends money: an estimate
first, explicit consent, and the ACTUAL usage written to token_ledger afterwards.
"""
import os, sys, json, urllib.request, urllib.error, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as D, config as C, costs as CO

API = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"       # cheapest; this is summarisation, not analysis

PROMPT = """You are explaining a stock research report to a smart intern who is new to
investing. They dollar-cost-average: they buy a small fixed amount of this stock every
single day, and they are not trading in and out.

Rewrite the report below for them.

Rules:
- Short bullet points. No long paragraphs. No walls of text.
- Plain English. If you must use a term like P/E, free cash flow, moving average or
  capex, explain it in the same breath, in five words or fewer.
- No hype and no hedging-for-its-own-sake. Say what is actually known.
- Be honest about disagreement between the analysts, and about what nobody knows.
- Never tell them to buy or sell. You are explaining someone else's analysis, not
  giving advice.

Use exactly these sections, as markdown headings:

## The one-line version
## What the company is doing well
## What could go wrong
## What the analysts disagreed about
## What this means if you buy a little every day
## What to watch next

Under the daily-buying section, address their situation specifically: entry timing
matters far less when buying daily than it would for a lump sum, so focus on whether
the business still looks worth owning, and on what would genuinely change that.

Here is the report:

---
{body}
---"""


def available():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def estimate(chars):
    e = CO.estimate_chars(MODEL, chars, expected_output_tokens=1400)
    return {"model": MODEL, "cost_usd": e.get("cost_usd"),
            "verified": e.get("verified"), "est_input_tokens": e.get("est_input_tokens")}


def _source_text(ticker):
    """Feed it the decision and the two managers — the reasoning, not every analyst
    table. Keeps the call small and the summary focused."""
    c = D.connect()
    try:
        rows = c.execute(
            "SELECT kind, content FROM ai_notes WHERE ticker=? AND kind LIKE 'ta:%'"
            " AND created_at=(SELECT MAX(created_at) FROM ai_notes WHERE ticker=?"
            "                 AND kind LIKE 'ta:%')", (ticker, ticker)).fetchall()
    finally:
        c.close()
    want = ("ta:decision", "ta:research_mgr", "ta:trader", "ta:bull", "ta:bear")
    by = {r["kind"]: r["content"] for r in rows}
    return "\n\n".join(f"### {k[3:]}\n{by[k]}" for k in want if k in by)


def explain(ticker, consented=False):
    ticker = ticker.upper()
    if not available():
        return {"ok": False, "error": "ANTHROPIC_API_KEY is not set in this environment."}
    body = _source_text(ticker)
    if not body:
        return {"ok": False, "error": f"No stored report for {ticker}."}
    prompt = PROMPT.format(body=body[:120000])
    est = estimate(len(prompt))
    if not consented:
        return {"ok": False, "needs_consent": True, "estimate": est}

    req = urllib.request.Request(API, method="POST", data=json.dumps({
        "model": MODEL, "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"content-type": "application/json",
                 "anthropic-version": "2023-06-01",
                 "x-api-key": os.environ["ANTHROPIC_API_KEY"]})
    t0 = dt.datetime.now()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")[:300]
        return {"ok": False, "error": f"API {e.code}: {detail}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    u = data.get("usage") or {}
    tin = int(u.get("input_tokens") or 0); tout = int(u.get("output_tokens") or 0)
    cost = CO.estimate(MODEL, tin, tout).get("cost_usd") or 0.0

    c = D.connect()
    try:
        created = c.execute("SELECT MAX(created_at) m FROM ai_notes WHERE ticker=?"
                            " AND kind LIKE 'ta:%'", (ticker,)).fetchone()["m"]
        c.execute("DELETE FROM ai_notes WHERE ticker=? AND kind='ta:plain'", (ticker,))
        c.execute("INSERT INTO ai_notes(ticker,created_at,kind,content,source)"
                  " VALUES(?,?,?,?,?)", (ticker, created, "ta:plain", text,
                                         f"plain-english/{MODEL}"))
        c.commit()
        D.log_tokens(c, f"explain:{ticker}", "anthropic_api", model=MODEL,
                     input_tokens=tin, output_tokens=tout, cost_usd=cost, consented=1,
                     note=f"plain-English rewrite in "
                          f"{(dt.datetime.now()-t0).total_seconds():.1f}s")
    finally:
        c.close()
    return {"ok": True, "ticker": ticker, "chars": len(text),
            "input_tokens": tin, "output_tokens": tout, "cost_usd": cost}


if __name__ == "__main__":
    t = sys.argv[1] if len(sys.argv) > 1 else "MSFT"
    print("key present:", available())
    print("estimate:", json.dumps(estimate(len(_source_text(t))), indent=1))
