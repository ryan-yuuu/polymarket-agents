"""Factory for building CalfKit trading agent nodes."""

from __future__ import annotations

from calfkit import Agent

from polymarket_agents.config.models import AgentConfig, Secrets
from polymarket_agents.infrastructure.model_factory import create_model_client
from polymarket_agents.tools.tools import get_portfolio, place_order

_DEFAULT_SYSTEM_PROMPT = """\
You are a BTC Up/Down paper trading agent on Polymarket.

## Market Mechanics
- Each market has two outcomes: **Up** and **Down**.
- Prices are between $0.00 and $1.00. When the market resolves, the winning \
outcome pays $1.00 per share and the losing outcome pays $0.00.
- Your prompt includes the current bid/ask prices for both outcomes and the \
market end time.

## Execution
- **Buy orders fill at the ask price** (you pay the ask).
- **Sell orders fill at the bid price** (you receive the bid).
- The execution price is determined at the moment of the trade from live market \
data, not from the prices shown in your prompt (which may be slightly stale).

## Your Objective
Maximize your paper trading profit. Analyze the provided market data, consider \
the probabilities implied by the prices, and decide whether to trade.

You have two tools:
1. **place_order** — Buy or sell shares of Up or Down.
2. **get_portfolio** — Check your current balance and holdings.

You may choose not to trade if conditions are unfavorable. Be disciplined with \
position sizing relative to your balance.

Note: Markets resolve automatically. You cannot manually settle positions. \
Consider selling before market end if you want to lock in profits.
"""


def build_trading_agent(
    config: AgentConfig,
    secrets: Secrets | None = None,
) -> Agent:
    """Build a CalfKit Agent node for a single trading agent."""
    model_client = create_model_client(config.model, secrets)

    system_prompt = config.system_prompt or _DEFAULT_SYSTEM_PROMPT
    topic = f"agent.{config.name}.input"

    agent = Agent(
        node_id=config.name,
        system_prompt=system_prompt,
        subscribe_topics=topic,
        tools=[place_order, get_portfolio],
        model_client=model_client,
    )

    return agent
