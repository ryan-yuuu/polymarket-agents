"""CalfKit tool definitions: place_order and get_portfolio.

Module-level singletons (_engine, _ws_stream, _gamma) are injected at startup
by the tool worker via init_tools().
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from calfkit import ToolContext, agent_tool

from polymarket_agents.domain.models import Direction, OrderSide
from polymarket_agents.infrastructure.paper_trading import PaperTradingEngine
from polymarket_agents.infrastructure.polymarket_client import GammaClient
from polymarket_agents.infrastructure.polymarket_ws import MarketDataStream

logger = logging.getLogger(__name__)

# Injected by run_tools.py at startup
_engine: PaperTradingEngine | None = None
_ws_stream: MarketDataStream | None = None
_gamma: GammaClient | None = None


def init_tools(
    engine: PaperTradingEngine,
    ws_stream: MarketDataStream,
    gamma: GammaClient,
) -> None:
    """Inject runtime dependencies into the tools module."""
    global _engine, _ws_stream, _gamma
    _engine = engine
    _ws_stream = ws_stream
    _gamma = gamma


@agent_tool
async def place_order(
    ctx: ToolContext,
    direction: str,
    side: str,
    size: float,
) -> str:
    """Place a paper trade order on the current BTC Up/Down market.

    Args:
        direction: "up" or "down" — which outcome to trade.
        side: "buy" or "sell" — buy into a position or sell an existing one.
        size: Number of shares to trade.

    Returns:
        JSON with execution details: status, direction, side, size,
        execution_price, cost, and balance_after.
    """
    if _engine is None or _ws_stream is None or _gamma is None:
        raise RuntimeError("Tools not initialized — call init_tools() first")

    if size <= 0:
        return json.dumps({"status": "error", "message": "Size must be positive."})

    # Resolve market context from deps
    deps = ctx.deps.provided_deps
    up_token_id = deps.get("up_token_id", "")
    down_token_id = deps.get("down_token_id", "")
    market_slug = deps.get("market_slug", "")

    # Lazy-subscribe to token IDs for new markets
    await _ws_stream.subscribe([up_token_id, down_token_id])

    direction_enum = Direction(direction.lower())
    order_side = OrderSide(side.lower())

    # Map direction to the correct token
    token_id = up_token_id if direction_enum == Direction.UP else down_token_id

    # Determine execution price from live WebSocket data
    if order_side == OrderSide.BUY:
        execution_price = _ws_stream.get_ask(token_id)
    else:
        execution_price = _ws_stream.get_bid(token_id)

    # Fallback to mid price if bid/ask not yet available
    if execution_price is None:
        execution_price = _ws_stream.get_mid(token_id)

    if execution_price is None or execution_price <= 0:
        return json.dumps({
            "status": "error",
            "message": f"No price data available for {direction} token. "
            "WebSocket may not have received data yet. Try again shortly.",
        })

    agent_id = ctx.agent_name or "unknown"

    # Parse end_date from deps
    end_date_str = deps.get("end_date", "")
    end_date = (
        datetime.fromisoformat(end_date_str)
        if end_date_str
        else datetime.now(timezone.utc)
    )

    try:
        record, settlements = await _engine.execute_trade(
            agent_id=agent_id,
            direction=direction_enum,
            order_side=order_side,
            size=size,
            execution_price=execution_price,
            market_slug=market_slug,
            end_date=end_date,
            resolve_fn=_gamma.get_resolution,
        )
    except ValueError as e:
        return json.dumps({"status": "error", "message": str(e)})

    for s in settlements:
        logger.info(
            "Settled %s %s: %d shares @ $%.2f",
            s.market_slug, s.direction.value, s.size, s.price,
        )

    logger.info(
        "%s %s %s %.1f shares @ $%.4f ($%.2f) | bal=$%.2f",
        agent_id,
        side,
        direction,
        size,
        execution_price,
        record.cost,
        record.balance_after,
    )

    return json.dumps({
        "status": "filled",
        "direction": direction,
        "side": side,
        "size": size,
        "execution_price": round(execution_price, 4),
        "cost": round(record.cost, 4),
        "balance_after": round(record.balance_after, 2),
        "settlements": len(settlements),
    })


@agent_tool
async def get_portfolio(ctx: ToolContext) -> str:
    """Get the current paper trading portfolio for this agent.

    Returns:
        JSON with cash_balance, initial_balance, total_pnl, and
        a list of holdings with unrealized P&L.
    """
    if _engine is None or _gamma is None:
        raise RuntimeError("Tools not initialized — call init_tools() first")

    agent_id = ctx.agent_name or "unknown"
    wallet, settlements = await _engine.settle_and_get_wallet(
        agent_id, _gamma.get_resolution,
    )

    if wallet is None:
        return json.dumps({
            "status": "error",
            "message": f"No wallet found for agent '{agent_id}'.",
        })

    for s in settlements:
        logger.info(
            "Settled %s %s: %d shares @ $%.2f",
            s.market_slug, s.direction.value, s.size, s.price,
        )

    holdings = []
    for slug, mp in wallet.positions.items():
        deps = ctx.deps.provided_deps
        up_token_id = deps.get("up_token_id")
        down_token_id = deps.get("down_token_id")

        for direction_str, pos in [("up", mp.up), ("down", mp.down)]:
            if pos is None or pos.size <= 0:
                continue

            token_id = None
            if direction_str == "up":
                token_id = up_token_id
            else:
                token_id = down_token_id

            current_mid = None
            if token_id and _ws_stream:
                current_mid = _ws_stream.get_mid(token_id)

            unrealized_pnl = 0.0
            if current_mid is not None:
                unrealized_pnl = (current_mid - pos.avg_entry_price) * pos.size

            holdings.append({
                "market_slug": slug,
                "direction": direction_str,
                "size": round(pos.size, 4),
                "avg_entry_price": round(pos.avg_entry_price, 4),
                "current_mid_price": round(current_mid, 4) if current_mid else None,
                "unrealized_pnl": round(unrealized_pnl, 4),
            })

    # Total P&L = (current balance - initial) + sum of unrealized
    realized_pnl = wallet.balance - wallet.initial_balance
    unrealized_total = sum(h["unrealized_pnl"] for h in holdings)
    total_pnl = realized_pnl + unrealized_total

    return json.dumps({
        "cash_balance": round(wallet.balance, 2),
        "initial_balance": round(wallet.initial_balance, 2),
        "realized_pnl": round(realized_pnl, 2),
        "unrealized_pnl": round(unrealized_total, 2),
        "total_pnl": round(total_pnl, 2),
        "holdings": holdings,
    })
