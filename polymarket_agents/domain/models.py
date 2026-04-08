"""Pure Pydantic data models for the Polymarket trading domain."""

from __future__ import annotations

from dataclasses import dataclass
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
    up_token_id: str = ""
    down_token_id: str = ""
    up: Position | None = None
    down: Position | None = None


# ---------------------------------------------------------------------------
# Candlestick / OHLCV models
# ---------------------------------------------------------------------------


class Candle(BaseModel):
    """A single OHLCV candlestick."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class CandleLayer:
    """Static config for one candlestick granularity layer."""

    granularity: int  # seconds per candle
    start_minutes_ago: int
    end_minutes_ago: int
    label: str


CANDLE_LAYERS: dict[Timeframe, list[CandleLayer]] = {
    Timeframe.FIVE_MIN: [
        CandleLayer(300, 120, 30, "5-min candles (2h ago -> 30min ago)"),
        CandleLayer(60, 30, 0, "1-min candles (last 30 minutes)"),
    ],
    Timeframe.FIFTEEN_MIN: [
        CandleLayer(900, 360, 120, "15-min candles (6h ago -> 2h ago)"),
        CandleLayer(300, 120, 20, "5-min candles (2h ago -> 20min ago)"),
        CandleLayer(60, 20, 0, "1-min candles (last 20 minutes)"),
    ],
    Timeframe.FOUR_HOUR: [
        CandleLayer(3600, 1440, 360, "1-hour candles (24h ago -> 6h ago)"),
        CandleLayer(900, 360, 30, "15-min candles (6h ago -> 30min ago)"),
        CandleLayer(300, 30, 0, "5-min candles (last 30 minutes)"),
    ],
}
