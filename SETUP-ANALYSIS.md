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

**22 pip dependencies** — langchain, langgraph, pandas, yfinance, backtrader, redis,
typer, rich. Trans is standard-library only, and that is deliberate: `./run.sh` works on
any machine with `python3` and nothing else. Vendoring TradingAgents would end that
property permanently, for a feature most users will never switch on.

TradingAgents is **Apache 2.0** licensed, so bundling it would have been legally fine
(with attribution and a NOTICE file). The dependency argument is the real reason, and it
is sufficient on its own.

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

The **Model** dropdown in the Research tab picks the model per run without touching your
config. The default is **Haiku 4.5** — cheapest, and the sensible choice while you are
still finding out whether the output is useful to you.

| Model | Estimated per run (1 debate round) |
|---|---|
| Haiku 4.5 *(default)* | $0.12–$0.34 |
| Sonnet 5 | $0.35–$1.03 |
| Opus 5 | $1.75–$5.13 |

Opus exceeds the default `max_cost_usd: 2.00` ceiling and will be refused until you
raise it. Only models with a rate in `pricing.json` are offered — nothing is shown with
a guessed price.

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

## Per-agent cost logging

The ledger records **one row per agent**, not one total, so you can see which analyst
spent what:

```
analysis:MSFT/market_analyst          1,240 in    380 out   $0.0031
analysis:MSFT/fundamentals_analyst    2,910 in    720 out   $0.0065
analysis:MSFT/bull_researcher         ...
```

Rows sum to the run total; there is no separate summary row, which would double-count.

Attribution comes from the LangGraph node name, captured when each model call starts and
settled when it ends. If a provider reports no usage, the run is logged as
"no per-agent usage reported" rather than showing an invented number.

## Live progress

While a run is in flight the Research tab refreshes every 5 seconds and shows which agent
is working, how many model calls have happened, and the tokens and cost each agent has
spent so far. Before the first model call it says it is gathering market data — that
phase takes a minute or two and involves no LLM spend.

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
