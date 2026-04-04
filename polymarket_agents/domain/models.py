"""Pure Pydantic data models for the Polymarket trading domain."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class Timeframe(str, Enum):
    """Supported BTC Up/Down market timeframes."""

    FIVE_MIN = "5m"
    FIFTEEN_MIN = "15m"
    FOUR_HOUR = "4h"

    @property
    def seconds(self) -> int:
        return {
            Timeframe.FIVE_MIN: 300,
            Timeframe.FIFTEEN_MIN: 900,
            Timeframe.FOUR_HOUR: 14400,
        }[self]

    @property
    def label(self) -> str:
        return {
            Timeframe.FIVE_MIN: "5 minutes",
            Timeframe.FIFTEEN_MIN: "15 minutes",
            Timeframe.FOUR_HOUR: "4 hours",
        }[self]


class Direction(str, Enum):
    UP = "up"
    DOWN = "down"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class TokenPair(BaseModel):
    """A Polymarket BTC Up/Down market with its two outcome tokens."""

    condition_id: str
    question: str
    slug: str
    up_token_id: str
    down_token_id: str
    end_date: datetime


class PriceSnapshot(BaseModel):
    """A point-in-time price observation from the CLOB."""

    token_id: str
    best_bid: float = 0.0
    best_ask: float = 0.0
    mid_price: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TradeRecord(BaseModel):
    """A single paper trade, maps 1:1 to a CSV row."""

    timestamp: datetime
    agent_id: str
    market_slug: str
    end_date: str = ""
    direction: Direction
    order_side: OrderSide
    size: float
    price: float
    cost: float
    balance_after: float


class Position(BaseModel):
    """An agent's current holding in one direction."""

    direction: Direction
    size: float
    avg_entry_price: float


class MarketPosition(BaseModel):
    """Both sides of a single market for one agent."""

    market_slug: str
    end_date: datetime
    up: Position | None = None
    down: Position | None = None
