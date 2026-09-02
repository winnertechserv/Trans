#!/usr/bin/env python3
"""Run one TradingAgents analysis and write its report tree plus a usage file.

This script is OURS but runs with the TradingAgents virtualenv's interpreter, because
that is where langchain/langgraph live. Trans itself never imports any of it.

TradingAgents (github.com/TauricResearch/TradingAgents) is Apache 2.0. Nothing of it is
copied here regardless — we only call its public API:
    tradingagents.graph.trading_graph.TradingAgentsGraph
    tradingagents.reporting.write_report_tree

Invoked by app/analysis.py as:
    <venv-python> tools/ta_runner.py --ticker NVDA --date 2026-09-02 --out <dir>
Configuration arrives through TRADINGAGENTS_* environment variables, which their
default_config already honours.
"""
import argparse, json, os, sys, datetime as dt


class _Usage:
    """Per-agent token accounting plus live progress.

    Two jobs. First, attribute tokens to the agent that spent them, so cost can be
    reported per analyst rather than as one opaque total. LangGraph puts the node name in
    the callback metadata, so we capture it at on_*_start (keyed by run_id) and settle up
    at on_llm_end — the end callback alone does not carry the node name.

    Second, rewrite progress.json after every event so Trans can show what is happening
    while the run is still in flight. A file is the simplest cross-process channel here;
    the two sides share a directory already.

    LangChain reports usage inconsistently across providers, so every read is defensive.
    Missing counts stay zero and are reported as unknown rather than guessed.
    """

    def __init__(self, progress_path, ticker):
        self.path = progress_path
        self.ticker = ticker
        self.by_agent = {}
        self.run_agent = {}      # run_id -> agent, captured at start
        self.order = []
        self.current = None
        self.calls = 0
        self.started = dt.datetime.now()
        self._flush(phase="starting")

    # -- attribution ---------------------------------------------------------
    @staticmethod
    def _agent_from(kwargs):
        meta = kwargs.get("metadata") or {}
        for key in ("langgraph_node", "node", "agent", "name"):
            v = meta.get(key)
            if isinstance(v, str) and v:
                return v
        for t in (kwargs.get("tags") or []):
            if isinstance(t, str) and t.startswith("agent:"):
                return t.split(":", 1)[1]
        return "unattributed"

    def _slot(self, agent):
        if agent not in self.by_agent:
            self.by_agent[agent] = {"input": 0, "output": 0, "calls": 0, "state": "running"}
            self.order.append(agent)
        return self.by_agent[agent]

    def _flush(self, phase="running"):
        try:
            tot_i = sum(a["input"] for a in self.by_agent.values())
            tot_o = sum(a["output"] for a in self.by_agent.values())
            json.dump({
                "ticker": self.ticker, "phase": phase, "current": self.current,
                "started_at": self.started.isoformat(timespec="seconds"),
                "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
                "elapsed_s": round((dt.datetime.now() - self.started).total_seconds(), 1),
                "llm_calls": self.calls,
                "agents": [{"agent": a, **self.by_agent[a]} for a in self.order],
                "totals": {"input_tokens": tot_i, "output_tokens": tot_o},
            }, open(self.path, "w"), indent=1)
        except Exception:
            pass      # progress reporting must never break the run

    # -- totals for usage.json ----------------------------------------------
    @property
    def input(self):  return sum(a["input"] for a in self.by_agent.values())
    @property
    def output(self): return sum(a["output"] for a in self.by_agent.values())

    def handler(self):
        try:
            from langchain_core.callbacks.base import BaseCallbackHandler
        except Exception:
            return None
        outer = self

        class H(BaseCallbackHandler):
            def _start(self, kwargs):
                agent = outer._agent_from(kwargs)
                rid = str(kwargs.get("run_id") or "")
                if rid:
                    outer.run_agent[rid] = agent
                outer.current = agent
                outer._slot(agent)
                outer._flush()

            def on_chat_model_start(self, serialized, messages, **kw): self._start(kw)
            def on_llm_start(self, serialized, prompts, **kw):         self._start(kw)

            def on_llm_end(self, response, **kw):
                outer.calls += 1
                rid = str(kw.get("run_id") or "")
                agent = outer.run_agent.pop(rid, None) or outer.current or "unattributed"
                slot = outer._slot(agent)
                slot["calls"] += 1
                i = o = 0
                try:
                    u = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
                    if u:
                        i = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
                        o = int(u.get("completion_tokens") or u.get("output_tokens") or 0)
                    else:
                        for gen in (getattr(response, "generations", None) or []):
                            for g in gen:
                                msg = getattr(g, "message", None)
                                md = getattr(msg, "usage_metadata", None) or {}
                                i += int(md.get("input_tokens") or 0)
                                o += int(md.get("output_tokens") or 0)
                except Exception:
                    pass
                slot["input"] += i; slot["output"] += o
                slot["state"] = "done"
                outer._flush()

            def on_llm_error(self, error, **kw):
                rid = str(kw.get("run_id") or "")
                agent = outer.run_agent.pop(rid, None) or outer.current
                if agent:
                    outer._slot(agent)["state"] = "error"
                outer._flush()
        return H()



def _attach_globally(handler):
    """Make `handler` see every LLM call, without needing propagate() to accept kwargs.

    LangChain exposes a ContextVar hook that its own token counters (get_openai_callback
    and friends) use; registering there means the handler is added to every callback
    manager the graph builds internally. Returns False if the mechanism is unavailable
    so the caller can say so plainly rather than silently reporting zero usage.
    """
    try:
        from contextvars import ContextVar
        from langchain_core.tracers.context import register_configure_hook
        var = ContextVar("trans_usage_cb", default=None)
        register_configure_hook(var, True)
        var.set(handler)
        return True
    except Exception:
        pass
    try:  # older/newer layouts
        from langchain_core.callbacks import manager as _m
        existing = list(getattr(_m, "_configure_hooks", []))
        from contextvars import ContextVar
        var = ContextVar("trans_usage_cb", default=None)
        var.set(handler)
        _m._configure_hooks = existing + [(var, True, None, None)]
        return True
    except Exception:
        return False


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
    usage = _Usage(os.path.join(a.out, "progress.json"), a.ticker)

    # DEFAULT_CONFIG already applies the TRADINGAGENTS_* env overrides.
    cfg = DEFAULT_CONFIG.copy()
    try:
        h = usage.handler()
        # TradingAgentsGraph takes callbacks directly — use that first-class hook, and
        # keep the global ContextVar registration as a belt-and-braces fallback for
        # any LLM the graph builds outside the constructor's callback plumbing.
        attached = _attach_globally(h) if h else False
        try:
            graph = TradingAgentsGraph(debug=False, config=cfg, callbacks=[h] if h else None)
            attached = attached or bool(h)
        except TypeError:
            graph = TradingAgentsGraph(debug=False, config=cfg)
        if h and not attached:
            print("warning: no usage callback attached — token counts unavailable",
                  file=sys.stderr)
        final_state, decision = graph.propagate(a.ticker, a.date)
    except Exception as e:
        usage._flush(phase="failed")
        print(f"analysis failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    try:
        write_report_tree(final_state, a.ticker, a.out)
    except Exception as e:
        print(f"report write failed: {e}", file=sys.stderr)
        return 1

    usage._flush(phase="finished")
    json.dump({"input_tokens": usage.input, "output_tokens": usage.output,
               "llm_calls": usage.calls, "ticker": a.ticker, "date": a.date,
               "by_agent": [{"agent": ag, **usage.by_agent[ag]} for ag in usage.order],
               "provider": os.environ.get("TRADINGAGENTS_LLM_PROVIDER"),
               "deep_think_llm": os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM"),
               "generated_at": dt.datetime.now().isoformat(timespec="seconds")},
              open(os.path.join(a.out, "usage.json"), "w"), indent=1)

    print(str(decision)[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())
