#!/usr/bin/env python3
"""Run one TradingAgents analysis and write its report tree plus a usage file.

This script is OURS but runs with the TradingAgents virtualenv's interpreter, because
that is where langchain/langgraph live. Trans itself never imports any of it.

TradingAgents (github.com/TauricResearch/TradingAgents) carries no license, so nothing
of it is copied here — we only call its public API:
    tradingagents.graph.trading_graph.TradingAgentsGraph
    tradingagents.reporting.write_report_tree

Invoked by app/analysis.py as:
    <venv-python> tools/ta_runner.py --ticker NVDA --date 2026-09-02 --out <dir>
Configuration arrives through TRADINGAGENTS_* environment variables, which their
default_config already honours.
"""
import argparse, json, os, sys, datetime as dt


class _Usage:
    """Accumulate token usage across every LLM call so the real cost can be logged.

    LangChain surfaces usage inconsistently across providers, so we read defensively and
    report zeros rather than guessing. app/analysis.py records "no usage reported" when
    that happens instead of inventing a number.
    """
    def __init__(self):
        self.input = self.output = self.calls = 0

    def handler(self):
        try:
            from langchain_core.callbacks.base import BaseCallbackHandler
        except Exception:
            return None
        outer = self

        class H(BaseCallbackHandler):
            def on_llm_end(self, response, **kw):
                outer.calls += 1
                try:
                    u = (response.llm_output or {}).get("token_usage") or {}
                    if not u:
                        gens = getattr(response, "generations", None) or []
                        for g in gens:
                            for gg in g:
                                meta = getattr(gg, "message", None)
                                meta = getattr(meta, "usage_metadata", None) or {}
                                outer.input += int(meta.get("input_tokens") or 0)
                                outer.output += int(meta.get("output_tokens") or 0)
                        return
                    outer.input += int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
                    outer.output += int(u.get("completion_tokens") or u.get("output_tokens") or 0)
                except Exception:
                    pass
        return H()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    try:
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from tradingagents.reporting import write_report_tree
    except Exception as e:
        print(f"cannot import TradingAgents: {e}\n"
              f"Is this the right venv, and is PYTHONPATH the clone? "
              f"(PYTHONPATH={os.environ.get('PYTHONPATH')})", file=sys.stderr)
        return 2

    os.makedirs(a.out, exist_ok=True)
    usage = _Usage()

    # DEFAULT_CONFIG already applies the TRADINGAGENTS_* env overrides.
    cfg = DEFAULT_CONFIG.copy()
    try:
        graph = TradingAgentsGraph(debug=False, config=cfg)
        h = usage.handler()
        kwargs = {"config": {"callbacks": [h]}} if h else {}
        try:
            final_state, decision = graph.propagate(a.ticker, a.date, **kwargs)
        except TypeError:
            # older signature without passthrough kwargs
            final_state, decision = graph.propagate(a.ticker, a.date)
    except Exception as e:
        print(f"analysis failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    try:
        write_report_tree(final_state, a.ticker, a.out)
    except Exception as e:
        print(f"report write failed: {e}", file=sys.stderr)
        return 1

    json.dump({"input_tokens": usage.input, "output_tokens": usage.output,
               "llm_calls": usage.calls, "ticker": a.ticker, "date": a.date,
               "provider": os.environ.get("TRADINGAGENTS_LLM_PROVIDER"),
               "deep_think_llm": os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM"),
               "generated_at": dt.datetime.now().isoformat(timespec="seconds")},
              open(os.path.join(a.out, "usage.json"), "w"), indent=1)

    print(str(decision)[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())
