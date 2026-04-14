"""Backtest runner: replay historical BTC candle data through an LLM agent.

Measures directional prediction accuracy — no trading involved. The agent
analyzes candle data and predicts "up" or "down" for each window. Uses
FastStream's TestKafkaBroker for in-process broker simulation (no Kafka).

Usage:
    uv run python -m scripts.run_backtest --agent btc-trader-15m --start 2025-06-01 --end 2025-06-30
    uv run python -m scripts.run_backtest --agent btc-trader-15m --start 2025-06-01 --end 2025-06-30 --system-prompt .calfkit_agents/backtest.md
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sqlite3
import sys
import time
import uuid
from collections import Counter
from faker import Faker
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from calfkit import Client, Worker
from faststream.kafka import TestKafkaBroker

from polymarket_agents.agents.trader import build_trading_agent
from polymarket_agents.config.loader import filter_agents, load_config, load_secrets
from polymarket_agents.config.models import AgentConfig
from polymarket_agents.domain.models import CANDLE_LAYERS, Timeframe
from polymarket_agents.infrastructure.candle_format import format_candles_prompt
from polymarket_agents.infrastructure.historical_candles import HistoricalCandleStore
from polymarket_agents.tools.backtest import get_prediction
from polymarket_agents.tools.toolsets import TOOLSETS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logging.Formatter.converter = time.gmtime
logger = logging.getLogger(__name__)

WINDOWS_CSV = Path("data/historical/btc_5m_windows.csv")
CANDLES_CSV = Path("data/historical/btcusd_1-min_data.csv")


@dataclass
class WindowRecord:
    timestamp: int
    price_to_beat: float
    final_price: float
    resolution: str


def _parse_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest BTC prediction accuracy")
    parser.add_argument("--agent", required=True, help="Agent name from agents.yaml")
    parser.add_argument(
        "--start", required=True, help="Start date (YYYY-MM-DD), inclusive"
    )
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD), inclusive")
    parser.add_argument(
        "--system-prompt",
        default=None,
        help="Override system prompt file (default: .calfkit_agents/backtest.md)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Per-window timeout in seconds (default: agent's cycle_timeout_seconds)",
    )
    return parser.parse_args()


def _load_windows(path: Path, start_ts: int, end_ts: int) -> dict[int, WindowRecord]:
    """Load 5-min window records from CSV, filtered to [start_ts, end_ts)."""
    windows: dict[int, WindowRecord] = {}
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = int(row["timestamp"])
            if ts < start_ts or ts >= end_ts:
                continue
            windows[ts] = WindowRecord(
                timestamp=ts,
                price_to_beat=float(row["price_to_beat"]),
                final_price=float(row["final_price"]),
                resolution=row["resolution"],
            )
    return windows


def _generate_window_timestamps(
    start_ts: int, end_ts: int, stride_seconds: int
) -> list[int]:
    """Generate aligned window timestamps within [start_ts, end_ts)."""
    # Align start to stride boundary
    aligned_start = start_ts - (start_ts % stride_seconds)
    if aligned_start < start_ts:
        aligned_start += stride_seconds

    timestamps = []
    ts = aligned_start
    while ts < end_ts:
        timestamps.append(ts)
        ts += stride_seconds
    return timestamps


def _resolve_window(
    ts: int, stride_seconds: int, windows: dict[int, WindowRecord]
) -> WindowRecord | None:
    """Derive resolution for a window of arbitrary stride from 5-min data.

    For a stride > 5min, we use the price_to_beat from the window at ts
    and the price_to_beat from the window at ts + stride as the final price.
    """
    start_window = windows.get(ts)
    if start_window is None:
        return None

    end_ts = ts + stride_seconds
    end_window = windows.get(end_ts)
    if end_window is None:
        return None

    price_to_beat = start_window.price_to_beat
    final_price = end_window.price_to_beat
    resolution = "up" if final_price >= price_to_beat else "down"

    return WindowRecord(
        timestamp=ts,
        price_to_beat=price_to_beat,
        final_price=final_price,
        resolution=resolution,
    )


def _build_backtest_prompt(
    window_ts: int,
    timeframe: Timeframe,
    price_to_beat: float,
    candle_section: str,
) -> str:
    """Build a prompt for one backtest window (no bid/ask)."""
    dt = datetime.fromtimestamp(window_ts, tz=timezone.utc)
    now_str = dt.strftime("%Y-%m-%d %H:%M:%S")
    end_dt = datetime.fromtimestamp(window_ts + timeframe.seconds, tz=timezone.utc)
    end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")

    parts = [
        f"It is {now_str} UTC.\n\n"
        f"CURRENT BTC UP/DOWN MARKET:\n"
        f"  Question: Will the price of BTC be higher in {timeframe.label}?\n"
        f"  Price to Beat: ${price_to_beat:,.2f}\n"
        f"  Market ends: {end_str} UTC\n\n"
        f"Analyze the price data and submit your prediction using submit_prediction, and provide your reasoning and thinking.",
    ]

    if candle_section:
        parts.append(candle_section)

    return "\n\n".join(parts)


async def main() -> None:
    args = _parse_cli()

    # Parse date range
    start_date = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_date = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_ts = int(start_date.timestamp())
    end_ts = int(end_date.timestamp()) + 86400  # include full end day

    # Load agent config
    config = load_config()
    secrets = load_secrets()
    agents = filter_agents(config, [args.agent])
    agent_cfg = agents[0]

    # Override toolset and optionally system prompt
    agent_cfg_dict = agent_cfg.model_dump()
    agent_cfg_dict["toolset"] = "backtest"
    if args.system_prompt:
        agent_cfg_dict["system_prompt_file"] = args.system_prompt
    elif agent_cfg.system_prompt_file is None:
        agent_cfg_dict["system_prompt_file"] = ".calfkit_agents/backtest.md"
    backtest_cfg = AgentConfig(**agent_cfg_dict)

    timeout = args.timeout or backtest_cfg.cycle_timeout_seconds
    stride = backtest_cfg.timeframe.seconds
    layers = CANDLE_LAYERS.get(backtest_cfg.timeframe, [])
    topic = f"agent.{backtest_cfg.name}.input"

    # Compute lookback needed for candle layers
    max_lookback_minutes = max((layer.start_minutes_ago for layer in layers), default=0)
    candle_start_ts = start_ts - max_lookback_minutes * 60

    logger.info(
        "Loading historical data: windows %s to %s, candles from %s",
        args.start,
        args.end,
        datetime.fromtimestamp(candle_start_ts, tz=timezone.utc).strftime("%Y-%m-%d"),
    )

    # Load data
    windows = _load_windows(WINDOWS_CSV, start_ts, end_ts + stride)
    candle_store = HistoricalCandleStore.from_csv(
        CANDLES_CSV, candle_start_ts, end_ts + stride
    )

    logger.info(
        "Loaded %d window records, %d candle records",
        len(windows),
        len(candle_store._candles),
    )

    # Generate window timestamps and resolve outcomes
    window_timestamps = _generate_window_timestamps(start_ts, end_ts, stride)
    valid_windows: list[WindowRecord] = []
    for ts in window_timestamps:
        record = _resolve_window(ts, stride, windows)
        if record is not None:
            valid_windows.append(record)

    if not valid_windows:
        logger.error("No valid windows found in date range. Check your data coverage.")
        sys.exit(1)

    logger.info(
        "Found %d valid %s windows to backtest (of %d candidates)",
        len(valid_windows),
        backtest_cfg.timeframe.value,
        len(window_timestamps),
    )

    # Build agent
    agent = build_trading_agent(backtest_cfg, secrets)

    # Set up CalfKit with TestKafkaBroker
    client = Client.connect()
    worker = Worker(client, nodes=[agent, *TOOLSETS[backtest_cfg.toolset]])
    worker.register_handlers()

    # Prepare output — CSV for predictions, shared SQLite for agent responses
    output_dir = Path("data/backtest")
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = "-".join(Faker().words(2))
    csv_path = output_dir / f"backtest_{backtest_cfg.name}_{args.start}_{args.end}_{slug}.csv"
    db_path = output_dir / "backtest_responses.db"

    CSV_FIELDS = [
        "id",
        "timestamp",
        "datetime_utc",
        "price_to_beat",
        "final_price",
        "actual_resolution",
        "predicted_direction",
        "correct",
    ]

    correct_count = 0
    error_count = 0
    total_count = 0
    resolution_counts: Counter[str] = Counter()

    db = sqlite3.connect(db_path)
    db.execute(
        """CREATE TABLE IF NOT EXISTS responses (
            id TEXT PRIMARY KEY,
            timestamp INTEGER NOT NULL,
            agent_response TEXT NOT NULL
        )"""
    )
    db.commit()

    csv_file = open(csv_path, "w", newline="")
    csv_writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
    csv_writer.writeheader()
    csv_file.flush()

    try:
        async with TestKafkaBroker(client._connection) as _:
            for i, window in enumerate(valid_windows):
                window_dt = datetime.fromtimestamp(window.timestamp, tz=timezone.utc)
                prediction_id = uuid.uuid4().hex
                logger.info(
                    "[%d/%d] %s | PTB: $%.2f | Actual: %s",
                    i + 1,
                    len(valid_windows),
                    window_dt.strftime("%Y-%m-%d %H:%M"),
                    window.price_to_beat,
                    window.resolution,
                )

                # Build candle data and prompt
                candle_data = candle_store.build_candle_layers(window.timestamp, layers)
                candle_section = format_candles_prompt(candle_data)
                prompt = _build_backtest_prompt(
                    window.timestamp,
                    backtest_cfg.timeframe,
                    window.price_to_beat,
                    candle_section,
                )

                # Execute agent
                predicted = None
                agent_response = ""
                try:
                    result = await client.execute_node(
                        user_prompt=prompt,
                        topic=topic,
                        deps={},
                        timeout=timeout,
                    )
                    agent_response = str(result.output) if result.output else ""
                    predicted = get_prediction(result.correlation_id)
                except asyncio.TimeoutError:
                    logger.warning("[%d/%d] Agent timed out", i + 1, len(valid_windows))
                    error_count += 1
                except Exception:
                    logger.exception("[%d/%d] Agent error", i + 1, len(valid_windows))
                    error_count += 1

                is_correct = predicted == window.resolution if predicted else False
                total_count += 1
                if predicted:
                    resolution_counts[window.resolution] += 1
                    if is_correct:
                        correct_count += 1

                # Write CSV row immediately
                csv_writer.writerow(
                    {
                        "id": prediction_id,
                        "timestamp": window.timestamp,
                        "datetime_utc": window_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "price_to_beat": f"{window.price_to_beat:.2f}",
                        "final_price": f"{window.final_price:.2f}",
                        "actual_resolution": window.resolution,
                        "predicted_direction": predicted or "error",
                        "correct": "1" if is_correct else "0",
                    }
                )
                csv_file.flush()

                # Write full agent response to shared SQLite
                db.execute(
                    "INSERT INTO responses (id, timestamp, agent_response) VALUES (?, ?, ?)",
                    (prediction_id, window.timestamp, agent_response),
                )
                db.commit()

                if predicted:
                    logger.info(
                        "[%d/%d] Predicted: %s | Actual: %s | %s",
                        i + 1,
                        len(valid_windows),
                        predicted,
                        window.resolution,
                        "CORRECT" if is_correct else "WRONG",
                    )
    finally:
        csv_file.close()
        db.close()
        logger.info(
            "Results: %s (%d rows), responses: %s", csv_path, total_count, db_path
        )

    # Print summary
    total_predicted = total_count - error_count
    print("\n" + "=" * 60)
    print(f"BACKTEST RESULTS: {backtest_cfg.name}")
    print(f"  Period: {args.start} to {args.end}")
    print(f"  Timeframe: {backtest_cfg.timeframe.value}")
    print(f"  Total windows: {total_count}")
    print(f"  Predictions made: {total_predicted}")
    print(f"  Errors/timeouts: {error_count}")

    if total_predicted > 0:
        accuracy = correct_count / total_predicted * 100
        print(f"  Correct: {correct_count}/{total_predicted} ({accuracy:.1f}%)")

        # Baseline: always predict majority class
        majority_class = (
            resolution_counts.most_common(1)[0] if resolution_counts else ("n/a", 0)
        )
        baseline_accuracy = (
            majority_class[1] / total_predicted * 100 if total_predicted else 0
        )
        print(
            f"  Baseline (always '{majority_class[0]}'): {majority_class[1]}/{total_predicted} ({baseline_accuracy:.1f}%)"
        )
        print(f"  Edge over baseline: {accuracy - baseline_accuracy:+.1f}pp")

    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
