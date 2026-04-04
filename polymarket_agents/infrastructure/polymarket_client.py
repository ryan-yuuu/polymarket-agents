"""Polymarket REST API clients for market discovery and price fetching."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx

from polymarket_agents.domain.models import Timeframe, TokenPair

logger = logging.getLogger(__name__)

_TIMEFRAME_SLUGS: dict[Timeframe, str] = {
    Timeframe.FIVE_MIN: "5m",
    Timeframe.FIFTEEN_MIN: "15m",
    Timeframe.FOUR_HOUR: "4h",
}


class GammaClient:
    """Discovers active BTC Up/Down markets via the Gamma API."""

    def __init__(self, base_url: str = "https://gamma-api.polymarket.com"):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=15.0)

    async def find_active_markets(
        self, timeframe: Timeframe, limit: int = 5
    ) -> list[TokenPair]:
        tag = _TIMEFRAME_SLUGS[timeframe]
        params = {
            "closed": "false",
            "tag": f"btc-updown-{tag}",
            "limit": limit,
            "order": "endDate",
            "ascending": "true",
        }
        resp = await self._client.get("/markets", params=params)
        resp.raise_for_status()
        raw_markets = resp.json()

        if not raw_markets:
            # Fallback: keyword search
            params = {
                "closed": "false",
                "limit": limit,
                "order": "endDate",
                "ascending": "true",
                "tag": "btc-updown",
            }
            resp = await self._client.get("/markets", params=params)
            resp.raise_for_status()
            raw_markets = resp.json()

        results: list[TokenPair] = []
        for market in raw_markets:
            parsed = self._parse_market(market)
            if parsed is not None:
                results.append(parsed)
        return results

    def _parse_market(self, raw: dict) -> TokenPair | None:
        try:
            # Gamma API returns tokens as JSON-encoded string or list
            tokens = raw.get("tokens", [])
            if isinstance(tokens, str):
                tokens = json.loads(tokens)

            if len(tokens) < 2:
                logger.warning("Market %s has fewer than 2 tokens", raw.get("slug"))
                return None

            # Map outcomes: first token is typically "Up", second is "Down"
            up_token_id = None
            down_token_id = None
            for token in tokens:
                outcome = token.get("outcome", "").lower()
                if "up" in outcome or "yes" in outcome:
                    up_token_id = token["token_id"]
                elif "down" in outcome or "no" in outcome:
                    down_token_id = token["token_id"]

            if not up_token_id or not down_token_id:
                # Fallback to positional
                up_token_id = tokens[0]["token_id"]
                down_token_id = tokens[1]["token_id"]

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

        tokens = market.get("tokens", [])
        if isinstance(tokens, str):
            tokens = json.loads(tokens)

        for i, price in enumerate(outcome_prices):
            if str(price) == "1" and i < len(tokens):
                outcome = tokens[i].get("outcome", "").lower()
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
