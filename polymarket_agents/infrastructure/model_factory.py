"""LLM model client factory for CalfKit agents."""

from __future__ import annotations

import logging

from calfkit import AnthropicModelClient, OpenAIModelClient
from calfkit.providers.pydantic_ai.model_client import PydanticModelClient

from polymarket_agents.config.models import ModelConfig, Secrets

logger = logging.getLogger(__name__)


def create_model_client(
    config: ModelConfig,
    secrets: Secrets | None = None,
) -> PydanticModelClient:
    """Factory: dispatch on config.provider to create the right model client."""
    secrets = secrets or Secrets()  # type: ignore[call-arg]

    if config.provider == "openai":
        api_key = config.api_key or secrets.openai_api_key or None
        return OpenAIModelClient(
            model_name=config.model_name,
            api_key=api_key,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            reasoning_effort=config.reasoning_effort,
        )
    elif config.provider == "anthropic":
        api_key = config.api_key or secrets.anthropic_api_key or None
        thinking = {"type": "adaptive"} if config.thinking else None
        return AnthropicModelClient(
            model_name=config.model_name,
            api_key=api_key,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            anthropic_thinking=thinking,
        )
    else:
        raise ValueError(f"Unsupported model provider: {config.provider}")
