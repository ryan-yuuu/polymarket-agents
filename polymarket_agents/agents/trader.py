"""Factory for building CalfKit trading agent nodes."""

from __future__ import annotations

from pathlib import Path

from calfkit import Agent

from polymarket_agents.config.models import AgentConfig, Secrets
from polymarket_agents.infrastructure.model_factory import create_model_client
from polymarket_agents.tools.toolsets import TOOLSETS

_DEFAULT_PROMPT_FILE = Path(".calfkit_agents/default.md")


def _resolve_system_prompt(prompt_file: str | None) -> str:
    """Read a system prompt from a markdown file.

    Falls back to ``.calfkit_agents/default.md`` when *prompt_file* is ``None``.
    """
    path = Path(prompt_file) if prompt_file else _DEFAULT_PROMPT_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"System prompt file not found: {path}. "
            "Ensure the file exists or set 'system_prompt_file' in agents.yaml."
        )
    return path.read_text().strip()


def build_trading_agent(
    config: AgentConfig,
    secrets: Secrets | None = None,
) -> Agent:
    """Build a CalfKit Agent node for a single trading agent."""
    model_client = create_model_client(config.model, secrets)

    system_prompt = _resolve_system_prompt(config.system_prompt_file)
    topic = f"agent.{config.name}.input"

    agent = Agent(
        node_id=config.name,
        system_prompt=system_prompt,
        subscribe_topics=topic,
        tools=TOOLSETS[config.toolset],
        model_client=model_client,
    )

    return agent
