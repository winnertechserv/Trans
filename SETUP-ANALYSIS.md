# Trans — research analysis (optional)

Trans can run **[TradingAgents](https://github.com/TauricResearch/TradingAgents)** over a
holding and store the report: five analysts (fundamentals, market, news, sentiment,
social), a bull/bear research debate, a trader, and a risk panel.

This is **entirely optional**. Trans works exactly the same without it, and installs
nothing on your behalf.

> **What this is not.** TradingAgents ends with a BUY/SELL/HOLD line. Trans displays it
> as third-party model output, attributed to the model that produced it. It is generated
> research, not a recommendation, and Trans does not check its conclusions. Treat it as
> one more opinion to read, not a signal to act on.

## Why it is not bundled

Two reasons, both hard:

1. **No license.** TradingAgents publishes no LICENSE file, so all rights are reserved by
   default. It cannot legally be vendored into or redistributed with this repo. You clone
   it yourself; Trans only stores a path to your clone.
2. **22 pip dependencies** — langchain, langgraph, pandas, yfinance, backtrader, redis,
   typer, rich. Trans is standard-library only, and that is deliberate. They cannot share
   a process.

So the integration is a **subprocess boundary**: `app/analysis.py` runs
`tools/ta_runner.py` (our code) using *their* virtualenv's Python, and reads back a tree
of markdown files. Trans never imports TradingAgents.

## Install

```bash
git clone https://github.com/TauricResearch/TradingAgents ~/src/TradingAgents
cd ~/src/TradingAgents
python3 -m venv .venv && .venv/bin/pip install .
```

Then pick a backend.

## Backends

|  | Local (Ollama) | Anthropic API |
|---|---|---|
| Cost | free | real money per run |
| Privacy | nothing leaves your machine | ticker and fetched market/news data go to the API |
| Quality | materially weaker at multi-step reasoning | substantially better |
| Speed | minutes to tens of minutes | faster |
| Consent | not needed | **required before every run** |

### Local — Ollama

```bash
ollama serve &
ollama pull qwen3:14b
```

Be realistic: a 14B model is a long way below frontier models at this kind of layered
reasoning, and a full debate takes minutes per ticker. Start with `max_debate_rounds: 1`
and treat the output as a rough second opinion.

### Anthropic API

```bash
export ANTHROPIC_API_KEY=...      # then restart ./run.sh
```

**The key is never stored in `config.json`.** It is read from the environment and passed
to the subprocess. `config.json` is gitignored, but keys in config files get copied into
backups, pasted into chats, and shared by accident — an environment variable does not.

Before every paid run Trans shows the estimated cost and requires explicit confirmation.
After the run it logs the **actual** token usage and cost to the ledger you can see in the
Sync & cost tab.

## Configure

In `config.json`:

```json
"tradingagents": {
  "enabled": true,
  "repo_path": "~/src/TradingAgents",
  "venv_python": "~/src/TradingAgents/.venv/bin/python",
  "backend": "ollama",
  "max_debate_rounds": 1,
  "max_risk_rounds": 1,
  "results_dir": "analysis",
  "ollama":    { "backend_url": "http://localhost:11434/v1",
                 "deep_think_llm": "qwen3:14b", "quick_think_llm": "qwen3:8b" },
  "anthropic": { "deep_think_llm": "claude-sonnet-5",
                 "quick_think_llm": "claude-haiku-4-5-20251001",
                 "require_consent": true, "max_cost_usd": 2.00 }
}
```

`max_cost_usd` is a hard ceiling — a run whose estimate exceeds it is refused, not
started. `max_debate_rounds` is the main cost and time multiplier.

## Use it

Open the **Research** tab, pick a holding, press **Run analysis**. On a paid backend you
get a confirmation dialog with the estimated range first.

Or in Claude Code: `analyse NVDA`.

Runs happen in the background — the dashboard stays usable, and the tab refreshes itself
while a run is in flight.

Reports land in `analysis/<TICKER>/<DATE>/` as markdown and are stored in the `ai_notes`
table. Both the directory and any vendored clone are gitignored, so generated research is
never committed.

## Cost estimates are estimates

The pre-flight number is derived from agent count × debate rounds × typical context size.
A multi-agent debate's real token use varies widely, so it is shown as a range and
labelled an estimate. The ledger records what actually happened, never the estimate.

`pricing.json` ships with `"verified": false`. Confirm the rates at
[anthropic.com/pricing](https://www.anthropic.com/pricing) and set it true before
trusting any dollar figure.

## Troubleshooting

The Research tab names whatever is missing. In probe order:

| Message | Fix |
|---|---|
| No `"tradingagents"` block | copy it from `config.example.json` |
| Analysis is off | set `enabled: true` |
| No TradingAgents interpreter at … | create the venv and `pip install .` |
| … does not look like a TradingAgents clone | check `repo_path` points at the clone |
| Ollama is not answering at … | `ollama serve &` and pull the model |
| `ANTHROPIC_API_KEY` is not set | export it, then restart `./run.sh` |
| `cannot import TradingAgents` in a run | the venv lacks the install — rerun `pip install .` |
