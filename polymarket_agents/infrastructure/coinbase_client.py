"""Coinbase Exchange REST client for fetching BTC-USD candlestick data."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from time import time

import httpx

from polymarket_agents.domain.models import Candle, CandleLayer

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.exchange.coinbase.com"


def _parse_coinbase_candle(row: list) -> Candle:
    """Parse a single Coinbase candle row.

    Coinbase returns: [timestamp, low, high, open, close, volume]
    """
    ts, low, high, open_, close, volume = row
    return Candle(
        time=datetime.fromtimestamp(ts, tz=timezone.utc),
        open=float(open_),
        high=float(high),
        low=float(low),
        close=float(close),
        volume=float(volume),
    )


class CoinbaseKlinesClient:
    """Fetches OHLCV candlestick data from the Coinbase Exchange API."""

    def __init__(self, base_url: str = _BASE_URL):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=15.0)

    async def fetch_candles(self, product_id: str, layer: CandleLayer) -> list[Candle]:
        """Fetch candles for a single layer."""
        now = time()
        start = int(now - layer.start_minutes_ago * 60)
        end = int(now - layer.end_minutes_ago * 60)

        try:
            resp = await self._client.get(
                f"/products/{product_id}/candles",
                params={
                    "granularity": layer.granularity,
                    "start": start,
                    "end": end,
                },
            )
            resp.raise_for_status()
            rows = resp.json()
            # Coinbase returns newest first; reverse to chronological order
            candles = [_parse_coinbase_candle(r) for r in reversed(rows)]
            return candles
        except httpx.HTTPError:
            logger.warning(
                "Coinbase candle fetch failed for %s (%s)",
                product_id,
                layer.label,
                exc_info=True,
            )
            return []

    async def fetch_all_layers(
        self, product_id: str, layers: list[CandleLayer]
    ) -> dict[CandleLayer, list[Candle]]:
        """Fetch candles for all layers concurrently."""
        results = await asyncio.gather(
            *(self.fetch_candles(product_id, layer) for layer in layers),
            return_exceptions=True,
        )
        data: dict[CandleLayer, list[Candle]] = {}
        for layer, result in zip(layers, results):
            if isinstance(result, Exception):
                logger.warning("Candle layer %s failed: %s", layer.label, result)
                data[layer] = []
            else:
                data[layer] = result
        return data

    async def fetch_open_price(self, product_id: str, timestamp: int) -> float | None:
        """Fetch the BTC price at a specific Unix timestamp.

        Fetches a broad range of 1-min candles around the target and filters
        for the matching timestamp. This is more reliable than a targeted
        single-candle query which can intermittently return empty results.
        """
        try:
            resp = await self._client.get(
                f"/products/{product_id}/candles",
                params={
                    "granularity": 60,
                    "start": timestamp - 120,
                    "end": timestamp + 120,
                },
            )
            resp.raise_for_status()
            rows = resp.json()
            for row in rows:
                if row[0] == timestamp:
                    candle = _parse_coinbase_candle(row)
                    return candle.open
        except httpx.HTTPError:
            logger.warning(
                "Coinbase open price fetch failed for %s at %d",
                product_id,
                timestamp,
                exc_info=True,
            )
        return None

    async def close(self) -> None:
        await self._client.aclose()
