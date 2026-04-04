<h1 align="center">Polymarket Agents 📈🤖</h1>

Autonomous AI trading agents for [Polymarket](https://polymarket.com/) BTC Up/Down prediction markets. Agents consume live market data, analyze probabilities, and execute trades, all orchestrated with the [Calfkit](https://github.com/calf-ai/calfkit-sdk) distributed agents framework.

<br>

If you find this project interesting or useful, please consider:

- ⭐ Starring the repository — it helps others discover it!
- 🐛 Reporting issues
- 🔀 Submitting PRs

<br>

> [!NOTE]
> If you're interested in AI agents daytrading crypto spot markets, check out another project I built: [Crypto Trading Arena](https://github.com/ryan-yuuu/crypto-trading-arena). It's an open source arena where AI agents compete against each other trading with live market data 24/7.

<br>

## Architecture

```
┌──────────────────┐   ┌─────────────────────┐   ┌─────────────────────┐
│    Scheduler     │   │    Agent Worker     │   │    Tool Worker      │
│  (run_client)    │──▶│   (run_agents)      │──▶│   (run_tools)       │
│                  │   │                     │   │                     │
│ • Wake agents    │   │ • Autonomous AI     │   │ • Portfolio and     │
│   on schedule    │   │   agents reasoning  │   │   trading engine    │
│ • Realtime       │   │   on realtime       │   │ • Execute and       │
│   price feeds    │   │   market data       │   │   settle orders on  │
│ • Dynamic prompt │   │   streams           │   │   realtime price    │
│   serving to     │   │                     │   │   streams           │
│   agents         │   │                     │   │ • Individual agent  │
│                  │   │                     │   │   wallets           │
└──────────────────┘   └─────────────────────┘   └─────────────────────┘
         │                      │                          │
         └──────────────────────┴──────────────────────────┘
                         Calfkit Broker
```

Three independent microservices communicate via Calfkit:

1. **Scheduler** — wakes agents on schedule, serves realtime price feeds, and dynamically builds prompts with live market context
2. **Agent Worker** — autonomous AI agents reasoning on realtime market data to make trading decisions
3. **Tool Worker** — portfolio and trading tools that execute and settle orders on realtime pricing streams, with individual agent wallets

<br>

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- Docker (for the broker)
- An API key for OpenAI and/or Anthropic

<br>

## Quickstart

### 1. Install dependencies

```bash
git clone https://github.com/ryan-yuuu/polymarket-agents.git
cd polymarket-agents
uv sync
```

<br>

### 2. Configure environment

Copy the example files and fill in your API keys:

```bash
cp .env.example .env
cp agents.example.yaml agents.yaml
```

Edit `.env` with your key(s):

```
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

Edit `agents.yaml` to configure your agents. The default config runs a single gpt 5 mini agent on 15-minute BTC markets:

```yaml
broker_url: "localhost:9092"

execution:
  mode: paper

market_data:
  gamma_api_url: "https://gamma-api.polymarket.com"
  clob_api_url: "https://clob.polymarket.com"
  ws_url: "wss://ws-subscriptions-clob.polymarket.com/ws/market"

agents:
  - name: "btc-trader-15m"
    model:
      provider: openai          # "openai" or "anthropic"
      model_name: "gpt-5-mini"
    timeframe: "15m"            # "5m", "15m", or "4h"
    poll_interval_seconds: 60
    initial_balance: 10000.0
```

<br>

### 3. Start the Calfkit broker

The broker enables communication between all processes. Clone the [calfkit-broker](https://github.com/calf-ai/calfkit-broker) and start it with Docker:

```bash
git clone https://github.com/calf-ai/calfkit-broker && cd calfkit-broker && make dev-up
```

Once the broker is running, open **three new terminal tabs** in the `polymarket-agents` directory.

<br>

### 4. Launch the system

Start each process in its own terminal, in the following order:

**Terminal 1 — Tool Worker** (portfolio, trading tools, and realtime pricing):

```bash
uv run python -m scripts.run_tools
```

**Terminal 2 — Agent Worker** (autonomous AI agents):

```bash
uv run python -m scripts.run_agents
```

**Terminal 3 — Scheduler** (wakes agents, price feeds, dynamic prompts):

```bash
uv run python -m scripts.run_client
```

The scheduler will begin discovering active BTC Up/Down markets, fetching prices, and sending prompts to your agents. Agents will analyze the market and execute paper trades via the tool worker. Trade logs are written to `data/`.

<br>

## Agent Configuration

Each agent in `agents.yaml` supports:

| Field | Default | Description |
|---|---|---|
| `name` | `"btc-trader"` | Unique agent identifier |
| `model.provider` | `"openai"` | `"openai"` or `"anthropic"` |
| `model.model_name` | `"gpt-5-mini"` | Model ID passed to the provider |
| `model.reasoning_effort` | — | OpenAI only: `"low"`, `"medium"`, or `"high"` |
| `model.thinking` | `false` | Anthropic only: enable adaptive extended thinking |
| `model.api_key` | — | Per-agent API key override (falls back to `.env`) |
| `timeframe` | `"15m"` | Market timeframe: `"5m"`, `"15m"`, or `"4h"` |
| `poll_interval_seconds` | `60` | Seconds between market data prompts |
| `initial_balance` | `10000.0` | Starting paper trading balance (omit when `resume: true`) |
| `resume` | `false` | Resume from the latest saved session instead of starting fresh |
| `system_prompt` | — | Override the default trading system prompt |

<br>

Multiple agents can run simultaneously with different models, timeframes, and strategies:

```yaml
agents:
  - name: "gpt-trader"
    model:
      provider: openai
      model_name: "gpt-5-mini"
    timeframe: "15m"
    initial_balance: 10000.0

  - name: "claude-trader"
    model:
      provider: anthropic
      model_name: "claude-sonnet-4-6"
    timeframe: "15m"
    initial_balance: 10000.0
```

<br>

## Agent Tools

| Tool | Description |
|---|---|
| `place_order` | Buy or sell Up/Down shares at the live market price |
| `get_portfolio` | View cash balance, positions, and unrealized P&L |
| `calculator` | Evaluate arithmetic expressions |

<br>

## Trade Data

Each session writes trades to a timestamped CSV in `data/`:

```
{agent_id}.{epoch}.trades.csv
```

Columns:

```
timestamp, agent_id, market_slug, end_date, direction, order_side, size, price, cost, balance_after, initial_balance
```

Starting a new session (default) creates a fresh CSV, preserving old files. Setting `resume: true` on an agent finds its latest CSV, reads `initial_balance` from it, and replays all trades to restore the wallet.

