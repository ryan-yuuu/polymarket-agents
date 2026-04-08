"""Agent worker entry point.

Builds trading agents from config and runs them as CalfKit worker nodes.
"""

from __future__ import annotations

import asyncio
import logging
import time

from calfkit import Client, Worker

from polymarket_agents.agents.trader import build_trading_agent
from polymarket_agents.config.loader import (
    filter_agents,
    load_config,
    load_secrets,
    parse_agent_filter,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logging.Formatter.converter = time.gmtime
logger = logging.getLogger(__name__)


async def main() -> None:
    config = load_config()
    secrets = load_secrets()
    agent_filter = parse_agent_filter()
    agent_configs = filter_agents(config, agent_filter)

    agents = []
    for agent_cfg in agent_configs:
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

    async with Client.connect(config.broker_url) as client:
        worker = Worker(client, nodes=agents)
        logger.info(
            "Agent worker starting with %d agents on %s", len(agents), config.broker_url
        )
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
