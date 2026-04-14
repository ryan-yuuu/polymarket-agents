"""Shared effective-balance computation for tool modules."""

from __future__ import annotations


def compute_effective_balance(
    wallet_balance: float,
    positions: dict,
    market_slug: str,
    max_usable_amount: float | None,
) -> float:
    """Return the agent's effective spendable balance.

    When *max_usable_amount* is set, the balance is capped at
    ``max_usable_amount`` minus the cost basis of open positions
    in *market_slug*.  Falls back to *wallet_balance* when uncapped
    or when the real balance is lower.

    Formula: min(max_usable_amount - deployed_in_slug, wallet_balance)
    """
    if max_usable_amount is None:
        return wallet_balance

    deployed = 0.0
    mp = positions.get(market_slug)
    if mp is not None:
        if mp.up and mp.up.size > 0:
            deployed += mp.up.size * mp.up.avg_entry_price
        if mp.down and mp.down.size > 0:
            deployed += mp.down.size * mp.down.avg_entry_price

    return max(0.0, min(max_usable_amount - deployed, wallet_balance))
