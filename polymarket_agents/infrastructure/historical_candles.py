"""Load and index historical 1-min BTC candle CSVs for backtesting.

Provides O(log n) range queries and aggregation into arbitrary granularity
candles, matching the CoinbaseKlinesClient.fetch_all_layers() interface so
output plugs directly into format_candles_prompt().
"""

from __future__ import annotations

import bisect
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from polymarket_agents.domain.models import Candle, CandleLayer


@dataclass(frozen=True, slots=True)
class RawCandle:
    """Lightweight 1-min candle without Pydantic overhead."""

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class HistoricalCandleStore:
    """In-memory store of 1-min candles with fast range and aggregation queries."""

    def __init__(self, candles: list[RawCandle], index: dict[int, RawCandle]) -> None:
        self._candles = candles  # sorted by timestamp
        self._timestamps = [c.timestamp for c in candles]
        self._index = index  # timestamp -> RawCandle for O(1) lookup

    @classmethod
    def from_csv(
        cls,
        path: str | Path,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> HistoricalCandleStore:
        """Load 1-min candles from CSV, optionally filtered to a time range.

        Expected CSV columns: Timestamp, Open, High, Low, Close, Volume
        """
        candles: list[RawCandle] = []
        index: dict[int, RawCandle] = {}

        with open(path) as f:
            reader = csv.reader(f)
            next(reader)  # skip header

            for row in reader:
                ts = int(float(row[0]))
                if start_ts is not None and ts < start_ts:
                    continue
                if end_ts is not None and ts > end_ts:
                    continue

                candle = RawCandle(
                    timestamp=ts,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
                candles.append(candle)
                index[ts] = candle

        candles.sort(key=lambda c: c.timestamp)
        return cls(candles, index)

    def get_range(self, start_ts: int, end_ts: int) -> list[RawCandle]:
        """Return candles in [start_ts, end_ts) via bisect."""
        lo = bisect.bisect_left(self._timestamps, start_ts)
        hi = bisect.bisect_left(self._timestamps, end_ts)
        return self._candles[lo:hi]

    def get_open_price(self, timestamp: int) -> float | None:
        """O(1) lookup of the open price at a specific timestamp."""
        candle = self._index.get(timestamp)
        return candle.open if candle else None

    def aggregate_candles(
        self, start_ts: int, end_ts: int, granularity: int
    ) -> list[Candle]:
        """Group 1-min candles into buckets of *granularity* seconds, compute OHLCV."""
        raw = self.get_range(start_ts, end_ts)
        if not raw:
            return []

        buckets: dict[int, list[RawCandle]] = {}
        for c in raw:
            bucket_ts = c.timestamp - (c.timestamp % granularity)
            buckets.setdefault(bucket_ts, []).append(c)

        result: list[Candle] = []
        for bucket_ts in sorted(buckets):
            group = buckets[bucket_ts]
            result.append(
                Candle(
                    time=datetime.fromtimestamp(bucket_ts, tz=timezone.utc),
                    open=group[0].open,
                    high=max(c.high for c in group),
                    low=min(c.low for c in group),
                    close=group[-1].close,
                    volume=sum(c.volume for c in group),
                )
            )
        return result

    def build_candle_layers(
        self, reference_ts: int, layers: list[CandleLayer]
    ) -> dict[CandleLayer, list[Candle]]:
        """Build candle data for all layers relative to reference_ts.

        Mirrors CoinbaseKlinesClient.fetch_all_layers() so the output plugs
        directly into format_candles_prompt().
        """
        result: dict[CandleLayer, list[Candle]] = {}
        for layer in layers:
            start = reference_ts - layer.start_minutes_ago * 60
            end = reference_ts - layer.end_minutes_ago * 60
            result[layer] = self.aggregate_candles(start, end, layer.granularity)
        return result
