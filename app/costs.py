"""Token + cost ledger.

Two execution paths, logged differently:

  claude_code : work done by the Claude Code session you already pay for.
                Tokens are recorded for visibility; cost_usd is 0 at point of use
                because it is not an incremental API charge.
  claude_api  : a direct Anthropic API call. Incrementally billed, so it requires
                explicit consent BEFORE running and is logged with a dollar cost.

PRICING BELOW IS UNVERIFIED — confirm against https://www.anthropic.com/pricing
before trusting any dollar figure. Edit pricing.json to correct it; the UI shows a
warning banner while "verified" is false.
"""
import os, json, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as D

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICING_PATH = os.path.join(ROOT, "pricing.json")

DEFAULT_PRICING = {
    "verified": False,
    "_comment": "USD per 1,000,000 tokens. Confirm at anthropic.com/pricing, then set verified=true.",
    "models": {
        "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
        "claude-sonnet-5":           {"input": 3.00, "output": 15.00},
        "claude-opus-5":             {"input": 15.00, "output": 75.00},
    },
}

def pricing():
    if not os.path.exists(PRICING_PATH):
        json.dump(DEFAULT_PRICING, open(PRICING_PATH, "w"), indent=2)
    return json.load(open(PRICING_PATH))

def estimate(model, input_tokens, output_tokens):
    p = pricing(); m = p["models"].get(model)
    if not m: return {"cost_usd": None, "verified": False, "model": model, "unknown_model": True}
    cost = input_tokens / 1e6 * m["input"] + output_tokens / 1e6 * m["output"]
    return {"cost_usd": round(cost, 6), "verified": p.get("verified", False), "model": model,
            "input_rate": m["input"], "output_rate": m["output"]}

def estimate_chars(model, prompt_chars, expected_output_tokens=1200):
    """Pre-flight estimate for the consent dialog. ~3.6 chars/token is a rough English ratio."""
    est_in = int(prompt_chars / 3.6)
    e = estimate(model, est_in, expected_output_tokens)
    e.update({"est_input_tokens": est_in, "est_output_tokens": expected_output_tokens,
              "is_estimate": True})
    return e

def record(operation, source, model=None, input_tokens=0, output_tokens=0,
           consented=0, note=None, cost_usd=None):
    c = D.connect()
    if cost_usd is None:
        cost_usd = 0.0 if source == "claude_code" else (
            estimate(model, input_tokens, output_tokens).get("cost_usd") or 0.0)
    rid = D.log_tokens(c, operation, source, model, input_tokens, output_tokens,
                       cost_usd, consented, note)
    c.close(); return rid

if __name__ == "__main__":
    print(json.dumps(estimate_chars("claude-sonnet-5", 8000), indent=2))
