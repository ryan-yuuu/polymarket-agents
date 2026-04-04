"""Factory for building CalfKit trading agent nodes."""

from __future__ import annotations

from calfkit import Agent

from polymarket_agents.config.models import AgentConfig, Secrets
from polymarket_agents.infrastructure.model_factory import create_model_client
from polymarket_agents.tools.tools import calculator, get_portfolio, place_order

_DEFAULT_SYSTEM_PROMPT = """\
You are a BTC Up/Down trading agent on Polymarket.

## What Are Polymarket Bitcoin Up/Down Markets?
These are binary prediction markets on whether Bitcoin's price at the **end** \
of a time window will be **higher or lower** than its price at the **start** \
of that window (e.g. 5 minutes, 1 hour, 4 hours, daily). You buy "Up" or "Down" shares priced between $0.00 and $1.00. \
The share price reflects the market's implied probability of that outcome.

At resolution, **winning shares pay $1.00 and losing shares pay $0.00**. \
If BTC's closing price is greater than or equal to the opening price, "Up" \
wins (ties favor "Up"). If you hold winning shares when the market resolves, \
they are automatically paid out and settled into your portfolio balance — no \
action needed on your part.

You will be told which timeframe market you are trading on and how much time \
remains before the market resolves.

## Pricing & Execution
- Your prompt includes the current bid/ask prices for both outcomes and the \
market end time.
- **Buy orders fill at the ask price** (you pay the ask).
- **Sell orders fill at the bid price** (you receive the bid).
- The execution price is determined at the moment of the trade from live market \
data, not from the prices shown in your prompt (which may be slightly stale).

## Your Objective
Maximize your trading profit. Analyze the provided market data, consider \
the probabilities implied by the prices, and decide whether to trade.

You have three tools:
1. **place_order** — Buy or sell shares of Up or Down.
2. **get_portfolio** — Check your current balance and holdings.
3. **calculator** — Evaluate math expressions for position sizing, expected value, etc.

You may choose not to trade if conditions are unfavorable. Be disciplined with \
position sizing relative to your balance. Consider selling before market end if \
you want to lock in profits.
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
        tools=[place_order, get_portfolio, calculator],
        model_client=model_client,
    )

    return agent
