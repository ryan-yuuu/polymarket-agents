# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Start the system (three separate terminals)
uv run python -m scripts.run_tools          # Tool worker (must start first)
uv run python -m scripts.run_agents         # Agent worker
uv run python -m scripts.run_client         # Scheduler

# Optional flags
uv run python -m scripts.run_client --agent btc-trader-15m --align-start-to-window
uv run python -m scripts.run_agents --agent btc-trader-15m

# Dashboard
uv run streamlit run scripts/dashboard.py

# Add dependencies (never edit pyproject.toml manually)
uv add <package>
```

There is no test suite or linter configured.

## Architecture

Three-service system orchestrated via [CalfKit](https://github.com/calf-ai/calfkit) broker (Kafka-based):

1. **Scheduler** (`scripts/run_client.py`) — Discovers active BTC Up/Down markets via Gamma API, fetches live prices from CLOB REST API, fetches BTC-USD candlesticks from Coinbase, builds prompts with market context, and publishes to agent topics. Polls on a configurable interval with optional clock-alignment.

2. **Agent Worker** (`scripts/run_agents.py`) — CalfKit Worker nodes running LLM agents. Each agent subscribes to its topic, receives market prompts, reasons with tools, and executes trades. Default toolset: `place_order`, `get_portfolio`, `calculator`. Contrarian toolset: `submit_order`, `view_portfolio`, `calculator` (silently flips trade direction). Supports OpenAI (Responses API by default, Chat Completions via `openai-chat`) and Anthropic models with per-agent config.

3. **Tool Worker** (`scripts/run_tools.py`) — CalfKit Worker running tool nodes. Handles paper trading execution, portfolio management, and math calculations. Uses module-level globals (`_engine`, `_clob`, `_gamma`) in both `tools/tools.py` and `tools/contrarian.py`, initialized at startup.

## Key Design Patterns

- **Lazy wallet initialization:** Wallets are created on first `get_portfolio` call, not at startup
- **Per-agent async locks:** Prevent race conditions during trade execution and settlement
- **CSV persistence:** Every trade appends to `data/{agent_id}.{epoch}.trades.csv`; `resume: true` replays the latest CSV to restore state
- **Market settlement:** Expired markets auto-resolve via Gamma API with payout settlement
- **Module-level DI:** `tools/tools.py` exports `init_tools()` and `tools/contrarian.py` exports `init_contrarian_tools()`, both called by `run_tools.py` before starting the worker
- **Toolset registry:** `tools/toolsets.py` maps toolset names (`"default"`, `"contrarian"`) to tool lists, selected per-agent via `toolset` config
- **Effective balance cap:** `max_usable_amount` config limits how much of the real wallet an agent can see/spend, scoped to the current market slug's deployed cost basis
- **Buy order limit:** `buy_order_limit` config skips buy execution when the real token price exceeds the threshold, returning a pending status to the agent

## Configuration

- `agents.yaml` — Agent definitions (model, timeframe, balance, strategy prompt, polling interval, cycle timeout, toolset, max_usable_amount, buy_order_limit). See `agents.example.yaml` for reference.
- `.env` — API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`). See `.env.example`.
- `.calfkit_agents/*.md` — System prompt files for agent strategies (default conservative, aggressive).
- CalfKit broker must be running on `broker_url` (default `localhost:9092`).

## Package Layout

- `polymarket_agents/domain/models.py` — Core enums and Pydantic models (Timeframe, Direction, OrderSide, TokenPair, Position, TradeRecord, Candle)
- `polymarket_agents/config/` — Pydantic config models and YAML/secrets loader
- `polymarket_agents/infrastructure/` — External integrations (Gamma, CLOB, Coinbase, WebSocket, PaperTradingEngine)
- `polymarket_agents/agents/trader.py` — CalfKit agent factory
- `polymarket_agents/tools/tools.py` — CalfKit `@agent_tool` definitions (place_order, get_portfolio, calculator)
- `polymarket_agents/tools/contrarian.py` — Contrarian `@agent_tool` variants (submit_order, view_portfolio) that silently flip trade direction
- `polymarket_agents/tools/toolsets.py` — Toolset name → tool list registry
- `polymarket_agents/tools/_balance.py` — Shared effective-balance computation for `max_usable_amount` cap
