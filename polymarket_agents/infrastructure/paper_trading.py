"""Paper trading engine with CSV persistence."""

from __future__ import annotations

import asyncio
import csv
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path

from polymarket_agents.domain.models import (
    Direction,
    MarketPosition,
    OrderSide,
    Position,
    TradeRecord,
)

logger = logging.getLogger(__name__)

_CSV_FIELDS = [
    "timestamp",
    "agent_id",
    "market_slug",
    "end_date",
    "direction",
    "order_side",
    "size",
    "price",
    "cost",
    "balance_after",
]


class AgentWallet:
    """In-memory balance + positions for a single agent."""

    def __init__(self, agent_id: str, initial_balance: float):
        self.agent_id = agent_id
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.positions: dict[str, MarketPosition] = {}  # keyed by market_slug

    def apply_trade(
        self,
        direction: Direction,
        order_side: OrderSide,
        size: float,
        price: float,
        market_slug: str,
        end_date: datetime | None = None,
    ) -> TradeRecord:
        cost = size * price

        mp = self.positions.get(market_slug)
        if mp is None:
            mp = MarketPosition(
                market_slug=market_slug,
                end_date=end_date or datetime.now(timezone.utc),
            )
            self.positions[market_slug] = mp

        side_attr = direction.value  # "up" or "down"
        existing: Position | None = getattr(mp, side_attr)

        if order_side == OrderSide.BUY:
            if cost > self.balance:
                raise ValueError(
                    f"Insufficient balance: need ${cost:.2f}, have ${self.balance:.2f}"
                )
            self.balance -= cost

            if existing and existing.size > 0:
                total_size = existing.size + size
                avg_price = (
                    (existing.avg_entry_price * existing.size + price * size)
                    / total_size
                )
                setattr(mp, side_attr, Position(
                    direction=direction,
                    size=total_size,
                    avg_entry_price=avg_price,
                ))
            else:
                setattr(mp, side_attr, Position(
                    direction=direction,
                    size=size,
                    avg_entry_price=price,
                ))

        elif order_side == OrderSide.SELL:
            if not existing or existing.size < size:
                available = existing.size if existing else 0.0
                raise ValueError(
                    f"Insufficient position: want to sell {size}, have {available}"
                )
            self.balance += cost
            remaining = existing.size - size
            if remaining < 1e-9:
                setattr(mp, side_attr, None)
            else:
                setattr(mp, side_attr, existing.model_copy(
                    update={"size": remaining}
                ))

        # Clean up empty MarketPositions
        if mp.up is None and mp.down is None:
            del self.positions[market_slug]

        return TradeRecord(
            timestamp=datetime.now(timezone.utc),
            agent_id=self.agent_id,
            market_slug=market_slug,
            end_date=mp.end_date.isoformat() if market_slug in self.positions else (end_date or datetime.now(timezone.utc)).isoformat(),
            direction=direction,
            order_side=order_side,
            size=size,
            price=price,
            cost=cost,
            balance_after=self.balance,
        )


class PaperTradingEngine:
    """Manages paper wallets for all agents with CSV persistence."""

    def __init__(self, data_dir: str | Path = "data"):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._wallets: dict[str, AgentWallet] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def register_agent(self, agent_id: str, initial_balance: float) -> None:
        wallet = AgentWallet(agent_id, initial_balance)
        csv_path = self._csv_path(agent_id)
        if csv_path.exists():
            self._replay_trades(wallet, csv_path)
            logger.info(
                "Loaded %s: balance=$%.2f, %d market(s)",
                agent_id,
                wallet.balance,
                len(wallet.positions),
            )
        self._wallets[agent_id] = wallet
        self._locks[agent_id] = asyncio.Lock()

    def _agent_lock(self, agent_id: str) -> asyncio.Lock:
        """Return the per-agent lock, creating one if needed."""
        if agent_id not in self._locks:
            self._locks[agent_id] = asyncio.Lock()
        return self._locks[agent_id]

    async def execute_trade(
        self,
        agent_id: str,
        direction: Direction,
        order_side: OrderSide,
        size: float,
        execution_price: float,
        market_slug: str,
        end_date: datetime | None = None,
        resolve_fn: Callable[[str], Awaitable[str | None]] | None = None,
    ) -> tuple[TradeRecord, list[TradeRecord]]:
        async with self._agent_lock(agent_id):
            wallet = self._wallets.get(agent_id)
            if not wallet:
                raise ValueError(f"Unknown agent: {agent_id}")

            # Settle expired markets before applying the new trade
            settlements: list[TradeRecord] = []
            if resolve_fn is not None:
                settlements = await self._settle_expired(wallet, resolve_fn)
                for s in settlements:
                    self._append_csv(agent_id, s)

            record = wallet.apply_trade(
                direction, order_side, size, execution_price, market_slug,
                end_date=end_date,
            )
            self._append_csv(agent_id, record)
            return record, settlements

    def get_wallet(self, agent_id: str) -> AgentWallet | None:
        return self._wallets.get(agent_id)

    async def settle_and_get_wallet(
        self,
        agent_id: str,
        resolve_fn: Callable[[str], Awaitable[str | None]],
    ) -> tuple[AgentWallet | None, list[TradeRecord]]:
        """Settle expired markets and return the wallet."""
        async with self._agent_lock(agent_id):
            wallet = self._wallets.get(agent_id)
            if not wallet:
                return None, []
            settlements = await self._settle_expired(wallet, resolve_fn)
            for s in settlements:
                self._append_csv(agent_id, s)
            return wallet, settlements

    async def _settle_expired(
        self,
        wallet: AgentWallet,
        resolve_fn: Callable[[str], Awaitable[str | None]],
    ) -> list[TradeRecord]:
        now = datetime.now(timezone.utc)
        to_settle = [
            (slug, mp) for slug, mp in wallet.positions.items()
            if mp.end_date < now
        ]
        records: list[TradeRecord] = []
        for slug, mp in to_settle:
            winner = await resolve_fn(slug)
            if winner is None:
                logger.warning(
                    "Market %s expired but not yet resolved, skipping", slug
                )
                continue
            # Settle each side
            for direction_str, pos in [("up", mp.up), ("down", mp.down)]:
                if pos is None or pos.size <= 0:
                    continue
                payout_price = 1.0 if direction_str == winner else 0.0
                payout = pos.size * payout_price
                wallet.balance += payout
                record = TradeRecord(
                    timestamp=now,
                    agent_id=wallet.agent_id,
                    market_slug=slug,
                    end_date=mp.end_date.isoformat(),
                    direction=Direction(direction_str),
                    order_side=OrderSide.SELL,
                    size=pos.size,
                    price=payout_price,
                    cost=payout,
                    balance_after=wallet.balance,
                )
                records.append(record)
            del wallet.positions[slug]
        return records

    def _csv_path(self, agent_id: str) -> Path:
        return self._data_dir / f"{agent_id}_trades.csv"

    def _append_csv(self, agent_id: str, record: TradeRecord) -> None:
        path = self._csv_path(agent_id)
        write_header = not path.exists()
        with open(path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
            if write_header:
                writer.writeheader()
            writer.writerow({
                "timestamp": record.timestamp.isoformat(),
                "agent_id": record.agent_id,
                "market_slug": record.market_slug,
                "end_date": record.end_date,
                "direction": record.direction.value,
                "order_side": record.order_side.value,
                "size": record.size,
                "price": record.price,
                "cost": record.cost,
                "balance_after": record.balance_after,
            })

    def _replay_trades(self, wallet: AgentWallet, csv_path: Path) -> None:
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    end_date_str = row.get("end_date", "")
                    end_date = (
                        datetime.fromisoformat(end_date_str)
                        if end_date_str
                        else None
                    )
                    wallet.apply_trade(
                        direction=Direction(row["direction"]),
                        order_side=OrderSide(row["order_side"]),
                        size=float(row["size"]),
                        price=float(row["price"]),
                        market_slug=row["market_slug"],
                        end_date=end_date,
                    )
                except Exception:
                    logger.exception("Failed to replay trade row: %s", row)
