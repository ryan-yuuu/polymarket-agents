"""Backtest prediction tool: submit_prediction.

Captures the agent's directional prediction keyed by correlation_id so the
backtest runner can match predictions to outcomes deterministically.

Follows the same module-level DI pattern as tools.py / contrarian.py.
"""

from __future__ import annotations

import json
import logging

from calfkit import ToolContext, agent_tool

logger = logging.getLogger(__name__)

# correlation_id -> direction ("up" | "down")
_predictions: dict[str, str] = {}


def get_prediction(correlation_id: str) -> str | None:
    """Pop and return the prediction for a given correlation_id, or None."""
    return _predictions.pop(correlation_id, None)


@agent_tool
def submit_prediction(ctx: ToolContext, direction: str) -> str:
    """Submit your directional prediction for this market window.

    You MUST call this tool exactly once per prompt with your prediction.

    Args:
        direction: "up" or "down" — your prediction for whether BTC's price
            will be higher or lower at the end of the window.

    Returns:
        JSON confirmation with the recorded direction.
    """
    direction_lower = direction.strip().lower()
    if direction_lower not in ("up", "down"):
        return json.dumps(
            {"status": "error", "message": f"Invalid direction: {direction!r}. Must be 'up' or 'down'."}
        )

    correlation_id = ctx.deps.correlation_id
    _predictions[correlation_id] = direction_lower
    logger.debug("Prediction recorded: %s -> %s", correlation_id[:8], direction_lower)

    return json.dumps({"status": "recorded", "direction": direction_lower})
