"""YAML config + .env secrets loader."""

from __future__ import annotations

from pathlib import Path

import yaml

from polymarket_agents.config.models import AppConfig, Secrets


def load_config(path: str | Path = "agents.yaml") -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text())
    return AppConfig(**raw)


def load_secrets() -> Secrets:
    return Secrets()  # type: ignore[call-arg]
