"""YAML config + .env secrets loader."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from polymarket_agents.config.models import AgentConfig, AppConfig, Secrets


def load_config(path: str | Path = "agents.yaml") -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text())
    return AppConfig(**raw)


def load_secrets() -> Secrets:
    return Secrets()  # type: ignore[call-arg]


def parse_cli_args() -> argparse.Namespace:
    """Parse CLI arguments for the scheduler."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--agent", action="extend", nargs="+", default=None)
    parser.add_argument(
        "--align-start-to-window",
        action="store_true",
        default=False,
        help="On startup, wait until the next window boundary before polling.",
    )
    args, _ = parser.parse_known_args()
    return args


def parse_agent_filter() -> list[str] | None:
    """Parse optional ``--agent <name> ...`` arguments from the CLI."""
    return parse_cli_args().agent


def filter_agents(
    config: AppConfig, agent_names: list[str] | None
) -> list[AgentConfig]:
    """Return agents matching *agent_names*, or all agents if *None*."""
    if not agent_names:
        return config.agents

    available = [a.name for a in config.agents]
    missing = [name for name in agent_names if name not in available]
    if missing:
        raise SystemExit(
            f"Agent(s) {missing} not found in config. Available: {available}"
        )
    return [a for a in config.agents if a.name in agent_names]
