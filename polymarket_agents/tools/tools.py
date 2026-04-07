"""CalfKit tool definitions: place_order and get_portfolio.

Module-level singletons (_engine, _ws_stream, _gamma) are injected at startup
by the tool worker via init_tools().
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import sympy
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
def calculator(ctx: ToolContext, expression: str) -> str:
    """Evaluate a math expression and return the result.

    Use this tool whenever you need to perform arithmetic or financial math
    such as position sizing, expected value, implied probability, P&L
    calculations, percentage changes, or risk/reward ratios.

    Operators: +, -, *, / (division), ** (power), % (modulo), parentheses.
    Functions: abs(), sqrt(), log() (natural log), floor(), ceiling(),
               Min(), Max(), Rational(a, b) for exact fractions.

    Examples:
        "100 * (1.00 - 0.55)"              → profit if 100 shares bought at $0.55 win
        "0.70 * (1 - 0.55) - 0.30 * 0.55"  → EV per share at $0.55 if true prob is 70%
        "(0.72 - 0.65) / 0.65 * 100"       → percentage change in price
        "1000 * 0.05"                       → 5% position size on $1000 balance
        "Rational(1, 3) + Rational(1, 6)"   → exact fraction arithmetic

    The expression is parsed safely with sympy (no eval). Agents familiar
    with sympy syntax can use any sympy expression that sympify accepts.

    Args:
        expression: A math expression string to evaluate.

    Returns:
        The result as a string, or a descriptive error message.
    """
    logger.debug("calculator called with expression=%r", expression)

    try:
        result = sympy.sympify(expression)
    except (sympy.SympifyError, TypeError) as exc:
        logger.debug("calculator parse error: %s", exc)
        return f"Error: could not parse expression: {exc}"

    # Evaluate to a float if the result is symbolic (e.g. contains sqrt)
    if result.is_number and not result.is_Integer and not result.is_Rational:
        evaluated = str(result.evalf())
    else:
        evaluated = str(result)

    logger.debug("calculator result=%s", evaluated)
    return evaluated


@agent_tool
async def place_order(
    ctx: ToolContext,
    direction: str,
    side: str,
    size: float,
) -> str:
    """Use this tool to place an order on the current BTC Up/Down market.

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

    agent_id = ctx.agent_name or "unknown"
    if _engine.get_wallet(agent_id) is None:
        return json.dumps(
            {
                "status": "error",
                "message": "Wallet not initialized. Call get_portfolio first to set up your wallet.",
            }
        )

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
        return json.dumps(
            {
                "status": "error",
                "message": f"No price data available for {direction} token. "
                "Market data may not be available yet. Try again shortly.",
            }
        )

    # Parse end_date from deps
    end_date_str = deps.get("end_date", "")
    end_date = (
        datetime.fromisoformat(end_date_str)
        if end_date_str
        else datetime.now(timezone.utc)
    )

    # Reject trades on expired markets
    if end_date <= datetime.now(timezone.utc):
        return json.dumps(
            {
                "status": "error",
                "message": "Market has expired. Wait for the next market window.",
            }
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
            up_token_id=up_token_id,
            down_token_id=down_token_id,
        )
    except Exception as e:
        logger.exception("execute_trade failed for %s", agent_id)
        return json.dumps({"status": "error", "message": str(e)})

    for s in settlements:
        logger.info(
            "Settled %s %s: %d shares @ $%.2f",
            s.market_slug,
            s.direction.value,
            s.size,
            s.price,
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

    return json.dumps(
        {
            "status": "filled",
            "direction": direction,
            "side": side,
            "size": size,
            "execution_price": round(execution_price, 4),
            "cost": round(record.cost, 4),
            "balance_after": round(record.balance_after, 2),
            "settlements": len(settlements),
        }
    )


@agent_tool
async def get_portfolio(ctx: ToolContext) -> str:
    """Use this tool to get your current trading portfolio, containing any cash balance and open positions.

    Returns:
        JSON with cash_balance and a list of active holdings with
        per-position stats (size, avg_entry_price, current_mid_price,
        unrealized_pnl). Expired positions are automatically settled
        into your cash balance.
    """
    if _engine is None or _gamma is None:
        raise RuntimeError("Tools not initialized — call init_tools() first")

    agent_id = ctx.agent_name or "unknown"
    deps = ctx.deps.provided_deps

    # Lazy wallet initialization — register on first portfolio check
    if _engine.get_wallet(agent_id) is None:
        initial_balance = deps.get("initial_balance")
        resume = deps.get("resume", False)
        _engine.register_agent(agent_id, initial_balance, resume=resume)
        logger.info("Lazily registered wallet for agent '%s'", agent_id)

    try:
        wallet, settlements = await _engine.settle_and_get_wallet(
            agent_id,
            _gamma.get_resolution,
        )
    except Exception:
        logger.exception("settle_and_get_wallet failed for %s", agent_id)
        wallet = _engine.get_wallet(agent_id)
        settlements = []

    if wallet is None:
        return json.dumps(
            {
                "status": "error",
                "message": f"No wallet found for agent '{agent_id}'.",
            }
        )

    for s in settlements:
        logger.info(
            "Settled %s %s: %d shares @ $%.2f",
            s.market_slug,
            s.direction.value,
            s.size,
            s.price,
        )

    now = datetime.now(timezone.utc)
    holdings = []
    for slug, mp in wallet.positions.items():
        # Hide expired-but-unresolved positions
        if mp.end_date < now:
            continue

        for direction_str, pos in [("up", mp.up), ("down", mp.down)]:
            if pos is None or pos.size <= 0:
                continue

            token_id = mp.up_token_id if direction_str == "up" else mp.down_token_id

            current_mid = None
            if token_id and _ws_stream:
                current_mid = _ws_stream.get_mid(token_id)

            unrealized_pnl = 0.0
            if current_mid is not None:
                unrealized_pnl = (current_mid - pos.avg_entry_price) * pos.size

            holdings.append(
                {
                    "market_slug": slug,
                    "direction": direction_str,
                    "size": round(pos.size, 4),
                    "avg_entry_price": round(pos.avg_entry_price, 4),
                    "current_mid_price": (
                        round(current_mid, 4) if current_mid else None
                    ),
                    "unrealized_pnl": round(unrealized_pnl, 4),
                }
            )

    return json.dumps(
        {
            "cash_balance": round(wallet.balance, 2),
            "holdings": holdings,
        }
    )
