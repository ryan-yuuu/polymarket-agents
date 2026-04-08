"""Scheduler / client entry point.

Discovers active markets, fetches current bid/ask prices via REST,
builds prompts with market context, and dispatches to agent topics via Kafka.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from calfkit import Client

from polymarket_agents.config.loader import (
    filter_agents,
    load_config,
    parse_cli_args,
)
from polymarket_agents.config.models import AgentConfig
from polymarket_agents.domain.models import CANDLE_LAYERS, TokenPair
from polymarket_agents.infrastructure.candle_format import format_candles_prompt
from polymarket_agents.infrastructure.coinbase_client import CoinbaseKlinesClient
from polymarket_agents.infrastructure.polymarket_client import (
    ClobRestClient,
    GammaClient,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logging.Formatter.converter = time.gmtime
logger = logging.getLogger(__name__)


def _build_prompt(
    market: TokenPair,
    up_bid: float,
    up_ask: float,
    down_bid: float,
    down_ask: float,
    price_to_beat: float,
    candle_section: str = "",
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end = market.end_date.strftime("%Y-%m-%d %H:%M:%S")

    parts = [
        f"It is {now} UTC.\n\n"
        f"CURRENT BTC UP/DOWN MARKET:\n"
        f"  Question: {market.question}\n"
        f"  Price to Beat: ${price_to_beat:,.2f}\n"
        f"  Up:   bid ${up_bid:.4f} / ask ${up_ask:.4f}\n"
        f"  Down: bid ${down_bid:.4f} / ask ${down_ask:.4f}\n"
        f"  Market ends: {end} UTC\n\n"
        f"Note: Buy orders fill at the ask price, sell orders fill at the bid price.",
    ]

    if candle_section:
        parts.append(candle_section)

    # parts.append("Evaluate and decide whether to trade.")
    return "\n\n".join(parts)


def _seconds_until_next_tick(interval_seconds: int) -> float:
    """Return seconds until the next clock-aligned tick, with a small buffer."""
    now = time.time()
    boundary = now - (now % interval_seconds) + interval_seconds
    return max(boundary - now + 2, 0)  # +2s buffer for market availability


async def _agent_loop(
    client: Client,
    agent_cfg: AgentConfig,
    gamma: GammaClient,
    clob: ClobRestClient,
    coinbase: CoinbaseKlinesClient,
    *,
    align_start_to_window: bool = False,
) -> None:
    """Polling loop for a single agent."""
    topic = f"agent.{agent_cfg.name}.input"
    if align_start_to_window:
        delay = _seconds_until_next_tick(agent_cfg.timeframe.seconds)
        logger.info(
            "[%s] Waiting %.1fs for next window boundary", agent_cfg.name, delay
        )
        await asyncio.sleep(delay)

    while True:
        try:
            # 1. Discover current active market and price to beat
            found_markets, price_to_beat = await gamma.find_active_markets(
                agent_cfg.timeframe, limit=1
            )
            if not found_markets:
                logger.warning(
                    "[%s] No active %s markets found",
                    agent_cfg.name,
                    agent_cfg.timeframe.value,
                )
                await asyncio.sleep(
                    _seconds_until_next_tick(agent_cfg.poll_interval_seconds)
                )
                continue

            market = found_markets[0]

            # 2. Fall back to Coinbase if Polymarket didn't provide a price
            if price_to_beat is None:
                # TODO: Coinbase is not the same data source as Polymarket
                # (which uses Chainlink BTC/USD). The open price differs by
                # ~$2-8 on average. Reconcile by using Chainlink directly.
                window_ts = int(time.time())
                window_ts -= window_ts % agent_cfg.timeframe.seconds
                price_to_beat = await coinbase.fetch_open_price(
                    "BTC-USD", window_ts
                )
                if price_to_beat is not None:
                    logger.info(
                        "[%s] Using Coinbase open price as fallback: %.2f",
                        agent_cfg.name,
                        price_to_beat,
                    )

            if price_to_beat is None:
                logger.warning(
                    "[%s] Could not resolve price to beat, skipping cycle",
                    agent_cfg.name,
                )
                await asyncio.sleep(
                    _seconds_until_next_tick(agent_cfg.poll_interval_seconds)
                )
                continue

            # 3. Fetch current bid/ask via CLOB REST
            up_ask, up_bid, down_ask, down_bid = await asyncio.gather(
                clob.get_price(market.up_token_id, "buy"),
                clob.get_price(market.up_token_id, "sell"),
                clob.get_price(market.down_token_id, "buy"),
                clob.get_price(market.down_token_id, "sell"),
            )

            # 4. Fetch BTC price history
            candle_section = ""
            layers = CANDLE_LAYERS.get(agent_cfg.timeframe, [])
            if layers:
                try:
                    candle_data = await coinbase.fetch_all_layers("BTC-USD", layers)
                    candle_section = format_candles_prompt(candle_data)
                except Exception:
                    logger.warning(
                        "[%s] Candle fetch failed, continuing without price history",
                        agent_cfg.name,
                    )

            # 5. Build prompt
            prompt = _build_prompt(
                market,
                up_bid,
                up_ask,
                down_bid,
                down_ask,
                price_to_beat,
                candle_section,
            )

            # 6. Dispatch to agent
            now = datetime.now(timezone.utc)
            time_left = market.end_date - now
            hours, remainder = divmod(int(time_left.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            logger.info(
                "[%s] Sending prompt — %s | Up: $%.4f/$%.4f | Down: $%.4f/$%.4f | now: %s | ends: %s | remaining: %dh%02dm%02ds",
                agent_cfg.name,
                market.slug,
                up_bid,
                up_ask,
                down_bid,
                down_ask,
                now.strftime("%H:%M:%S"),
                market.end_date.strftime("%H:%M:%S"),
                hours,
                minutes,
                seconds,
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
                timeout=agent_cfg.cycle_timeout_seconds,
            )

            logger.info("[%s] Response: %s", agent_cfg.name, result.output)

        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("[%s] Cycle error", agent_cfg.name)

        delay = _seconds_until_next_tick(agent_cfg.poll_interval_seconds)
        logger.info("[%s] Next cycle in %.1fs", agent_cfg.name, delay)
        await asyncio.sleep(delay)


async def main() -> None:
    cli = parse_cli_args()
    config = load_config()
    agents = filter_agents(config, cli.agent)

    gamma = GammaClient(base_url=config.market_data.gamma_api_url)
    clob = ClobRestClient(base_url=config.market_data.clob_api_url)
    coinbase = CoinbaseKlinesClient()

    if not agents:
        logger.error("No agents configured in agents.yaml")
        return

    async with Client.connect(config.broker_url) as client:
        tasks = [
            asyncio.create_task(
                _agent_loop(
                    client,
                    agent_cfg,
                    gamma,
                    clob,
                    coinbase,
                    align_start_to_window=cli.align_start_to_window,
                ),
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
            await coinbase.close()


if __name__ == "__main__":
    asyncio.run(main())
