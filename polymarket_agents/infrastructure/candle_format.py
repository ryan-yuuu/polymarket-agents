"""Format candlestick data into a compact CSV prompt section."""

from __future__ import annotations

from polymarket_agents.domain.models import Candle, CandleLayer


def format_candles_prompt(
    candle_data: dict[CandleLayer, list[Candle]],
    product_id: str = "BTC-USD",
) -> str:
    """Build a text section with OHLCV candles grouped by layer.

    Returns "" if no candle data is available.
    """
    sections: list[str] = []

    for layer, candles in candle_data.items():
        if not candles:
            continue
        header = f"### {layer.label}"
        rows = ["time,open,high,low,close,volume"]
        for c in candles:
            rows.append(
                f"{c.time.strftime('%Y-%m-%dT%H:%M:%SZ')},"
                f"{c.open:.2f},{c.high:.2f},{c.low:.2f},{c.close:.2f},"
                f"{c.volume:.4f}"
            )
        sections.append(f"{header}\n" + "\n".join(rows))

    if not sections:
        return ""

    preamble = (
        f"{product_id} PRICE HISTORY (OHLCV candlesticks):\n"
        "Below are candlesticks at multiple granularities — "
        "coarser for broader trend context, finer for recent price action.\n"
    )
    return preamble + "\n\n".join(sections) + "\n"
