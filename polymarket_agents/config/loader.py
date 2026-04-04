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


def parse_agent_filter() -> str | None:
    """Parse an optional ``--agent <name>`` argument from the CLI."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--agent", default=None)
    args, _ = parser.parse_known_args()
    return args.agent


def filter_agents(config: AppConfig, agent_name: str | None) -> list[AgentConfig]:
    """Return agents matching *agent_name*, or all agents if *None*."""
    if agent_name is None:
        return config.agents

    matched = [a for a in config.agents if a.name == agent_name]
    if not matched:
        available = [a.name for a in config.agents]
        raise SystemExit(
            f"Agent '{agent_name}' not found in config. Available: {available}"
        )
    return matched
