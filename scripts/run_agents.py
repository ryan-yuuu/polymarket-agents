"""Agent worker entry point.

Builds trading agents from config and runs them as CalfKit worker nodes.
"""

from __future__ import annotations

import asyncio
import logging

from calfkit import Client, Worker

from polymarket_agents.agents.trader import build_trading_agent
from polymarket_agents.config.loader import load_config, load_secrets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    config = load_config()
    secrets = load_secrets()

    agents = []
    for agent_cfg in config.agents:
        agent = build_trading_agent(agent_cfg, secrets)
        agents.append(agent)
        logger.info(
            "Built agent '%s' (%s/%s, %s timeframe)",
            agent_cfg.name,
            agent_cfg.model.provider,
            agent_cfg.model.model_name,
            agent_cfg.timeframe.value,
        )

    if not agents:
        logger.error("No agents configured in agents.yaml")
        return

    async with await Client.connect(config.broker_url) as client:
        worker = Worker(client, nodes=agents)
        logger.info("Agent worker starting with %d agents on %s", len(agents), config.broker_url)
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
