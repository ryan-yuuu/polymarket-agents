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
    "initial_balance",
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
        up_token_id: str = "",
        down_token_id: str = "",
    ) -> TradeRecord:
        cost = round(size * price, 2)

        mp = self.positions.get(market_slug)
        if mp is None:
            mp = MarketPosition(
                market_slug=market_slug,
                end_date=end_date or datetime.now(timezone.utc),
                up_token_id=up_token_id,
                down_token_id=down_token_id,
            )
            self.positions[market_slug] = mp

        side_attr = direction.value  # "up" or "down"
        existing: Position | None = getattr(mp, side_attr)

        if order_side == OrderSide.BUY:
            if cost > self.balance:
                raise ValueError(
                    f"Insufficient balance: need ${cost:.2f}, have ${self.balance:.2f}"
                )
            self.balance = round(self.balance - cost, 2)

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
            self.balance = round(self.balance + cost, 2)
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
        self._csv_paths: dict[str, Path] = {}

    def register_agent(
        self,
        agent_id: str,
        initial_balance: float | None = None,
        *,
        resume: bool = False,
    ) -> None:
        if agent_id in self._wallets:
            logger.debug("Agent %s already registered, skipping", agent_id)
            return

        if resume:
            csv_path = self._find_latest_csv(agent_id)
            if csv_path is None:
                raise ValueError(
                    f"resume=True but no existing CSV found for agent '{agent_id}'"
                )
            ib = self._read_initial_balance(csv_path)
            wallet = AgentWallet(agent_id, ib)
            self._replay_trades(wallet, csv_path)
            self._csv_paths[agent_id] = csv_path
            logger.info(
                "Resumed %s from %s: balance=$%.2f, %d market(s)",
                agent_id,
                csv_path.name,
                wallet.balance,
                len(wallet.positions),
            )
        else:
            if initial_balance is None:
                raise ValueError(
                    "initial_balance is required when resume=False"
                )
            wallet = AgentWallet(agent_id, initial_balance)
            epoch = int(datetime.now(timezone.utc).timestamp())
            csv_path = self._data_dir / f"{agent_id}.{epoch}.trades.csv"
            self._csv_paths[agent_id] = csv_path
            logger.info(
                "Registered %s (fresh): balance=$%.2f, csv=%s",
                agent_id,
                wallet.balance,
                csv_path.name,
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
        up_token_id: str = "",
        down_token_id: str = "",
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
                up_token_id=up_token_id,
                down_token_id=down_token_id,
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
            try:
                winner = await resolve_fn(slug)
            except Exception:
                logger.exception(
                    "Failed to resolve market %s, skipping settlement", slug
                )
                continue
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
                payout = round(pos.size * payout_price, 2)
                wallet.balance = round(wallet.balance + payout, 2)
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

    def _find_latest_csv(self, agent_id: str) -> Path | None:
        """Find the most recent CSV for *agent_id* by epoch in filename."""
        best: tuple[int, Path] | None = None
        for p in self._data_dir.glob(f"{agent_id}.*.trades.csv"):
            parts = p.name.rsplit(".", 3)  # [agent_id, epoch, "trades", "csv"]
            if len(parts) != 4:
                continue
            try:
                epoch = int(parts[1])
            except ValueError:
                continue
            if best is None or epoch > best[0]:
                best = (epoch, p)
        return best[1] if best else None

    @staticmethod
    def _read_initial_balance(csv_path: Path) -> float:
        """Read initial_balance from the first data row of a CSV."""
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            row = next(reader, None)
            if row is None:
                raise ValueError(f"CSV is empty: {csv_path}")
            raw = row.get("initial_balance")
            if raw is None or raw == "":
                raise ValueError(
                    f"CSV missing initial_balance column: {csv_path}"
                )
            return float(raw)

    def _append_csv(self, agent_id: str, record: TradeRecord) -> None:
        path = self._csv_paths[agent_id]
        write_header = not path.exists()
        wallet = self._wallets[agent_id]
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
                "initial_balance": wallet.initial_balance,
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
