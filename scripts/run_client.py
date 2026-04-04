"""Scheduler / client entry point.

Discovers active markets, fetches current bid/ask prices via REST,
builds prompts with market context, and dispatches to agent topics via Kafka.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from calfkit import Client

from polymarket_agents.config.loader import filter_agents, load_config, parse_agent_filter
from polymarket_agents.config.models import AgentConfig
from polymarket_agents.domain.models import TokenPair
from polymarket_agents.infrastructure.polymarket_client import ClobRestClient, GammaClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

_CYCLE_TIMEOUT = 120  # seconds


def _build_prompt(
    market: TokenPair,
    up_bid: float,
    up_ask: float,
    down_bid: float,
    down_ask: float,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end = market.end_date.strftime("%Y-%m-%d %H:%M:%S")

    return (
        f"It is {now} UTC.\n\n"
        f"CURRENT BTC UP/DOWN MARKET:\n"
        f"  Question: {market.question}\n"
        f"  Up:   bid ${up_bid:.4f} / ask ${up_ask:.4f}\n"
        f"  Down: bid ${down_bid:.4f} / ask ${down_ask:.4f}\n"
        f"  Market ends: {end} UTC\n\n"
        f"Note: Buy orders fill at the ask price, sell orders fill at the bid price.\n\n"
        f"Evaluate and decide whether to trade."
    )


async def _agent_loop(
    client: Client,
    agent_cfg: AgentConfig,
    gamma: GammaClient,
    clob: ClobRestClient,
) -> None:
    """Polling loop for a single agent."""
    topic = f"agent.{agent_cfg.name}.input"

    while True:
        try:
            # 1. Discover current active market
            markets = await gamma.find_active_markets(agent_cfg.timeframe, limit=1)
            if not markets:
                logger.warning("[%s] No active %s markets found", agent_cfg.name, agent_cfg.timeframe.value)
                await asyncio.sleep(agent_cfg.poll_interval_seconds)
                continue

            market = markets[0]

            # 2. Fetch current bid/ask via CLOB REST
            up_ask, up_bid, down_ask, down_bid = await asyncio.gather(
                clob.get_price(market.up_token_id, "buy"),
                clob.get_price(market.up_token_id, "sell"),
                clob.get_price(market.down_token_id, "buy"),
                clob.get_price(market.down_token_id, "sell"),
            )

            # 3. Build prompt
            prompt = _build_prompt(market, up_bid, up_ask, down_bid, down_ask)

            # 4. Dispatch to agent
            logger.info(
                "[%s] Sending prompt — %s | Up: $%.4f/$%.4f | Down: $%.4f/$%.4f",
                agent_cfg.name,
                market.slug,
                up_bid,
                up_ask,
                down_bid,
                down_ask,
            )

            result = await client.execute_node(
                user_prompt=prompt,
                topic=topic,
                deps={
                    "up_token_id": market.up_token_id,
                    "down_token_id": market.down_token_id,
                    "market_slug": market.slug,
                    "end_date": market.end_date.isoformat(),
                    "initial_balance": agent_cfg.initial_balance,
                    "resume": agent_cfg.resume,
                },
                timeout=_CYCLE_TIMEOUT,
            )

            logger.info("[%s] Response: %s", agent_cfg.name, str(result)[:500])

        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("[%s] Cycle error", agent_cfg.name)

        await asyncio.sleep(agent_cfg.poll_interval_seconds)


async def main() -> None:
    config = load_config()
    agent_filter = parse_agent_filter()
    agents = filter_agents(config, agent_filter)

    gamma = GammaClient(base_url=config.market_data.gamma_api_url)
    clob = ClobRestClient(base_url=config.market_data.clob_api_url)

    if not agents:
        logger.error("No agents configured in agents.yaml")
        return

    async with Client.connect(config.broker_url) as client:
        tasks = [
            asyncio.create_task(
                _agent_loop(client, agent_cfg, gamma, clob),
                name=f"scheduler-{agent_cfg.name}",
            )
            for agent_cfg in agents
        ]

        logger.info(
            "Scheduler started with %d agent(s) on %s",
            len(tasks),
            config.broker_url,
        )

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            await gamma.close()
            await clob.close()


if __name__ == "__main__":
    asyncio.run(main())
