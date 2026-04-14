"""Toolset registry — maps config names to CalfKit tool node lists."""

from __future__ import annotations

from polymarket_agents.tools.contrarian import submit_order, view_portfolio
from polymarket_agents.tools.tools import calculator, get_portfolio, place_order

TOOLSETS: dict[str, list] = {
    "default": [place_order, get_portfolio, calculator],
    "contrarian": [submit_order, view_portfolio, calculator],
}
