"""WebSocket client for real-time bid/ask data from the Polymarket CLOB."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import websockets
from websockets.asyncio.client import ClientConnection

from polymarket_agents.domain.models import PriceSnapshot

logger = logging.getLogger(__name__)

_DEFAULT_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
_PING_INTERVAL = 10  # seconds
_RECONNECT_BASE_DELAY = 1  # seconds
_RECONNECT_MAX_DELAY = 30


class MarketDataStream:
    """Maintains a WebSocket connection to the Polymarket CLOB for live bid/ask.

    Runs in the tool worker process. Tools read from the in-memory cache
    to get execution prices at trade time.
    """

    def __init__(self, ws_url: str = _DEFAULT_WS_URL):
        self._ws_url = ws_url
        self._cache: dict[str, PriceSnapshot] = {}
        self._subscribed_tokens: set[str] = set()
        self._ws: ClientConnection | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False

    # -- Public price accessors (called by tools) --

    def get_bid(self, token_id: str) -> float | None:
        snap = self._cache.get(token_id)
        return snap.best_bid if snap and snap.best_bid > 0 else None

    def get_ask(self, token_id: str) -> float | None:
        snap = self._cache.get(token_id)
        return snap.best_ask if snap and snap.best_ask > 0 else None

    def get_mid(self, token_id: str) -> float | None:
        snap = self._cache.get(token_id)
        return snap.mid_price if snap and snap.mid_price > 0 else None

    def get_snapshot(self, token_id: str) -> PriceSnapshot | None:
        return self._cache.get(token_id)

    # -- Lifecycle --

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("MarketDataStream started")

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("MarketDataStream stopped")

    async def subscribe(self, token_ids: list[str]) -> None:
        new_tokens = set(token_ids) - self._subscribed_tokens
        if not new_tokens:
            return
        self._subscribed_tokens.update(new_tokens)
        if self._ws:
            await self._send_subscribe(list(new_tokens))

    # -- Internal --

    async def _run_loop(self) -> None:
        delay = _RECONNECT_BASE_DELAY
        while self._running:
            try:
                async with websockets.connect(self._ws_url) as ws:
                    self._ws = ws
                    delay = _RECONNECT_BASE_DELAY
                    logger.info("WebSocket connected to %s", self._ws_url)

                    if self._subscribed_tokens:
                        await self._send_subscribe(list(self._subscribed_tokens))

                    # Run ping + listen concurrently
                    ping_task = asyncio.create_task(self._ping_loop(ws))
                    try:
                        await self._listen(ws)
                    finally:
                        ping_task.cancel()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("WebSocket error, reconnecting in %ds", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, _RECONNECT_MAX_DELAY)

    async def _ping_loop(self, ws: ClientConnection) -> None:
        try:
            while True:
                await asyncio.sleep(_PING_INTERVAL)
                await ws.ping()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.debug("Ping failed, connection will reconnect")

    async def _listen(self, ws: ClientConnection) -> None:
        async for raw_msg in ws:
            try:
                msg = json.loads(raw_msg)
                self._handle_message(msg)
            except json.JSONDecodeError:
                logger.debug("Non-JSON message: %s", raw_msg[:100])

    async def _send_subscribe(self, token_ids: list[str]) -> None:
        if not self._ws:
            return
        # Subscribe to book data for each asset
        for token_id in token_ids:
            msg = {
                "type": "subscribe",
                "channel": "market",
                "assets_ids": [token_id],
            }
            await self._ws.send(json.dumps(msg))
            logger.info("Subscribed to token %s", token_id)

    def _handle_message(self, msg: dict) -> None:
        event_type = msg.get("event_type", msg.get("type", ""))
        now = datetime.now(timezone.utc)

        if event_type == "book":
            # Full book snapshot — extract best bid/ask
            token_id = msg.get("asset_id", "")
            if not token_id:
                return
            bids = msg.get("bids", [])
            asks = msg.get("asks", [])
            best_bid = float(bids[0]["price"]) if bids else 0.0
            best_ask = float(asks[0]["price"]) if asks else 0.0
            mid = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else max(best_bid, best_ask)
            self._cache[token_id] = PriceSnapshot(
                token_id=token_id,
                best_bid=best_bid,
                best_ask=best_ask,
                mid_price=mid,
                timestamp=now,
            )

        elif event_type == "price_change":
            # Price tick — update mid, preserve existing bid/ask
            token_id = msg.get("asset_id", "")
            price = float(msg.get("price", 0))
            if token_id and price:
                existing = self._cache.get(token_id)
                if existing:
                    self._cache[token_id] = existing.model_copy(
                        update={"mid_price": price, "timestamp": now}
                    )
                else:
                    self._cache[token_id] = PriceSnapshot(
                        token_id=token_id, mid_price=price, timestamp=now
                    )

        elif event_type in ("last_trade_price", "tick_size_change"):
            # Informational, skip
            pass
        else:
            logger.debug("Unhandled WS event: %s", event_type)
