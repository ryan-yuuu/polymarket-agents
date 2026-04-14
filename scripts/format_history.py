"""Format 1-min BTC candle data into 5-min market window records for backtesting.

Reads the raw 1-min CSV and produces a CSV with one row per 5-min window:
    timestamp, price_to_beat, final_price, resolution

The output is indexed by 5-min aligned epoch timestamps (matching the Polymarket
slug format btc-updown-5m-{timestamp}). Any timeframe (15m, 1h, 4h) can be
derived by indexing into this data at the appropriate interval.

Usage:
    uv run python -m scripts.format_history
    uv run python -m scripts.format_history --start 2025-12-01 --end 2026-04-12
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

INPUT_PATH = Path("data/historical/btcusd_1-min_data.csv")
OUTPUT_PATH = Path("data/historical/btc_5m_windows.csv")
WINDOW_SECONDS = 300


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Format 1-min BTC candles into 5-min window records",
    )
    parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD), inclusive")
    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD), inclusive")
    args = parser.parse_args()

    start_ts: int | None = None
    end_ts: int | None = None
    if args.start:
        start_ts = int(
            datetime.strptime(args.start, "%Y-%m-%d")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
    if args.end:
        # Include the full end day
        end_ts = (
            int(
                datetime.strptime(args.end, "%Y-%m-%d")
                .replace(tzinfo=timezone.utc)
                .timestamp()
            )
            + 86400
        )

    # Pass 1: stream through input, collect Open prices at 5-min boundaries
    aligned: dict[int, float] = {}
    total_rows = 0

    print(f"Reading {INPUT_PATH} ...")
    with open(INPUT_PATH) as f:
        reader = csv.reader(f)
        next(reader)  # skip header

        for row in reader:
            ts = int(float(row[0]))
            total_rows += 1

            if start_ts and ts < start_ts:
                continue
            if end_ts and ts > end_ts:
                continue

            if ts % WINDOW_SECONDS == 0:
                aligned[ts] = float(row[1])  # Open price

    print(f"  Read {total_rows:,} rows, found {len(aligned):,} at 5-min boundaries")

    # Pass 2: pair consecutive boundaries into windows
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = 0

    with open(OUTPUT_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "price_to_beat", "final_price", "resolution"])

        for ts in sorted(aligned.keys()):
            next_ts = ts + WINDOW_SECONDS
            if next_ts not in aligned:
                skipped += 1
                continue

            price_to_beat = aligned[ts]
            final_price = aligned[next_ts]
            resolution = "up" if final_price >= price_to_beat else "down"

            writer.writerow([ts, f"{price_to_beat:.2f}", f"{final_price:.2f}", resolution])
            written += 1

    print(f"Written {written:,} windows to {OUTPUT_PATH}")
    if skipped:
        print(f"Skipped {skipped:,} windows (missing next boundary)")


if __name__ == "__main__":
    main()
