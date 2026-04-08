"""Tool worker entry point.

Owns the CLOB REST client for live bid/ask and the paper trading engine.
Serves place_order and get_portfolio tools to agents via Kafka.
"""

from __future__ import annotations

import asyncio
import logging

from calfkit import Client, Worker

from polymarket_agents.config.loader import load_config
from polymarket_agents.infrastructure.paper_trading import PaperTradingEngine
from polymarket_agents.infrastructure.polymarket_client import ClobRestClient, GammaClient
from polymarket_agents.tools.tools import (
    calculator,
    get_portfolio,
    init_tools,
    place_order,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    config = load_config()

    # --- Paper Trading Engine (wallets registered lazily via get_portfolio) ---
    engine = PaperTradingEngine(data_dir="data")

    # --- CLOB REST Client (stateless price lookups, no connection lifecycle) ---
    clob = ClobRestClient(base_url=config.market_data.clob_api_url)

    # --- GammaClient (kept alive for resolution queries) ---
    gamma = GammaClient(base_url=config.market_data.gamma_api_url)

    # --- Inject into tools module ---
    init_tools(engine, clob, gamma)

    # --- Start Worker ---
    async with Client.connect(config.broker_url) as client:
        worker = Worker(client, nodes=[place_order, get_portfolio, calculator])
        logger.info("Tool worker starting on %s", config.broker_url)
        try:
            await worker.run()
        finally:
            await clob.close()
            await gamma.close()


if __name__ == "__main__":
    asyncio.run(main())
