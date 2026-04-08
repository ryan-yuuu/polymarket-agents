"""Polymarket REST API clients for market discovery and price fetching."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime

import asyncio
import httpx

from polymarket_agents.domain.models import Timeframe, TokenPair

logger = logging.getLogger(__name__)


class GammaClient:
    """Discovers active BTC Up/Down markets via the Gamma API."""

    def __init__(self, base_url: str = "https://gamma-api.polymarket.com"):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=15.0)

    async def find_active_markets(
        self, timeframe: Timeframe, limit: int = 5
    ) -> tuple[list[TokenPair], float | None]:
        """Discover active markets and resolve the price to beat.

        Returns (markets, price_to_beat). price_to_beat is None if neither
        priceToBeat nor the previous window's finalPrice are available yet.
        """
        now = int(time.time())
        window_ts = now - (now % timeframe.seconds)
        slug = f"btc-updown-{timeframe.value}-{window_ts}"

        logger.info("Querying events with slug: %s", slug)

        # Fetch the current window's event
        resp = await self._client.get("/events", params={"slug": slug})
        resp.raise_for_status()
        events = resp.json()

        if not events:
            logger.warning("No event found for slug: %s", slug)
            return [], None

        event = events[0] if isinstance(events, list) else events

        # 1. Try priceToBeat from the current event metadata
        price_to_beat = self._extract_metadata_float(event, "priceToBeat")
        if price_to_beat is not None:
            logger.info("Got priceToBeat from current event: %.2f", price_to_beat)

        # 2. Fall back to finalPrice from the previous window
        if price_to_beat is None:
            prev_ts = window_ts - timeframe.seconds
            prev_slug = f"btc-updown-{timeframe.value}-{prev_ts}"
            price_to_beat = await self._fetch_final_price(prev_slug)

        markets = event.get("markets", [])
        results: list[TokenPair] = []
        for market in markets[:limit]:
            parsed = self._parse_market(market)
            if parsed is not None:
                results.append(parsed)
        return results, price_to_beat

    async def _fetch_final_price(self, slug: str) -> float | None:
        """Fetch finalPrice from a previous window's eventMetadata with retries."""
        for attempt in range(4):
            try:
                resp = await self._client.get("/events", params={"slug": slug})
                resp.raise_for_status()
                events = resp.json()
            except Exception:
                logger.warning("Failed to fetch previous event %s", slug, exc_info=True)
                events = []

            if events:
                event = events[0] if isinstance(events, list) else events
                final_price = self._extract_metadata_float(event, "finalPrice")
                if final_price is not None:
                    logger.info(
                        "Got finalPrice from previous window %s: %.2f",
                        slug,
                        final_price,
                    )
                    return final_price

            if attempt < 3:
                delay = 2 * (2 ** attempt)  # 2s, 4s, 8s
                logger.info(
                    "finalPrice not yet available for %s, retrying in %ds (attempt %d/3)",
                    slug,
                    delay,
                    attempt + 1,
                )
                await asyncio.sleep(delay)

        return None

    @staticmethod
    def _extract_metadata_float(event: dict, key: str) -> float | None:
        """Extract a float value from an event's eventMetadata."""
        metadata = event.get("eventMetadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                return None
        raw = metadata.get(key)
        if raw is not None:
            try:
                return float(raw)
            except (ValueError, TypeError):
                pass
        return None

    def _parse_market(self, raw: dict) -> TokenPair | None:
        try:
            outcomes = raw.get("outcomes", [])
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes)

            clob_token_ids = raw.get("clobTokenIds", [])
            if isinstance(clob_token_ids, str):
                clob_token_ids = json.loads(clob_token_ids)

            if len(outcomes) < 2 or len(clob_token_ids) < 2:
                logger.warning(
                    "Market %s has fewer than 2 outcomes/tokens", raw.get("slug")
                )
                return None

            up_token_id = None
            down_token_id = None
            for i, outcome in enumerate(outcomes):
                lower = outcome.lower()
                if "up" in lower:
                    up_token_id = clob_token_ids[i]
                elif "down" in lower:
                    down_token_id = clob_token_ids[i]

            if not up_token_id or not down_token_id:
                up_token_id = clob_token_ids[0]
                down_token_id = clob_token_ids[1]

            end_date_str = raw.get("endDate", raw.get("end_date_iso", ""))
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))

            return TokenPair(
                condition_id=raw.get("conditionId", raw.get("condition_id", "")),
                question=raw.get("question", ""),
                slug=raw.get("slug", ""),
                up_token_id=up_token_id,
                down_token_id=down_token_id,
                end_date=end_date,
            )
        except Exception:
            logger.exception("Failed to parse market: %s", raw.get("slug"))
            return None

    async def get_resolution(self, slug: str) -> str | None:
        """Return winning outcome for a resolved market, or None if unresolved.

        Queries Gamma API for the market, checks umaResolutionStatus == "resolved",
        then reads outcomePrices to find the winner.

        Returns:
            "up", "down", or None.
        """
        resp = await self._client.get(
            "/markets", params={"slug": slug, "closed": "true"}
        )
        resp.raise_for_status()
        markets = resp.json()

        if not markets:
            return None

        market = markets[0] if isinstance(markets, list) else markets
        if market.get("umaResolutionStatus") != "resolved":
            return None

        outcome_prices_raw = market.get("outcomePrices", "")
        if isinstance(outcome_prices_raw, str):
            try:
                outcome_prices = json.loads(outcome_prices_raw)
            except (json.JSONDecodeError, TypeError):
                return None
        else:
            outcome_prices = outcome_prices_raw

        # Use the outcomes field (e.g. ["Up", "Down"]) to identify the winner.
        # The tokens field is often None for closed markets.
        outcomes_raw = market.get("outcomes", "[]")
        if isinstance(outcomes_raw, str):
            try:
                outcomes = json.loads(outcomes_raw)
            except (json.JSONDecodeError, TypeError):
                outcomes = []
        else:
            outcomes = outcomes_raw or []

        for i, price in enumerate(outcome_prices):
            try:
                price_f = float(price)
            except (ValueError, TypeError):
                logger.warning(
                    "Non-numeric outcome price %r for market %s", price, slug
                )
                continue
            if price_f >= 0.99 and i < len(outcomes):
                outcome = outcomes[i].lower()
                if "up" in outcome or "yes" in outcome:
                    return "up"
                elif "down" in outcome or "no" in outcome:
                    return "down"

        return None

    async def close(self) -> None:
        await self._client.aclose()


class ClobRestClient:
    """Fetches bid/ask prices from the CLOB REST API (used by scheduler)."""

    def __init__(self, base_url: str = "https://clob.polymarket.com"):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def get_price(self, token_id: str, side: str = "buy") -> float:
        """Get the best price for a token on the given side.

        Args:
            token_id: The CLOB token identifier.
            side: "buy" returns best ask (what a buyer pays),
                  "sell" returns best bid (what a seller receives).
        """
        resp = await self._client.get(
            "/price", params={"token_id": token_id, "side": side}
        )
        resp.raise_for_status()
        data = resp.json()
        return float(data.get("price", 0.0))

    async def close(self) -> None:
        await self._client.aclose()
