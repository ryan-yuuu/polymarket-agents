"""Configuration and settings models."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings

from polymarket_agents.domain.models import Timeframe


class ModelConfig(BaseModel):
    provider: str = "openai"
    model_name: str = "gpt-5-mini"
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None  # OpenAI: "minimal", "low", "medium", "high"
    thinking: bool = False  # Anthropic: enable adaptive thinking
    api_key: str | None = None  # override per-agent; falls back to env


class AgentConfig(BaseModel):
    name: str = "btc-trader"
    model: ModelConfig = Field(default_factory=ModelConfig)
    timeframe: Timeframe = Timeframe.FIFTEEN_MIN
    poll_interval_seconds: int = 60
    initial_balance: float | None = None
    resume: bool = False
    system_prompt_file: str | None = None  # path to .md file; defaults to .calfkit_agents/default.md

    @model_validator(mode="after")
    def _validate_resume_balance(self) -> AgentConfig:
        if self.resume and self.initial_balance is not None:
            raise ValueError("initial_balance must not be set when resume=True")
        if not self.resume and self.initial_balance is None:
            self.initial_balance = 10_000.0
        return self


class MarketDataConfig(BaseModel):
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    clob_api_url: str = "https://clob.polymarket.com"
    ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class ExecutionConfig(BaseModel):
    mode: str = "paper"


class AppConfig(BaseModel):
    broker_url: str = "localhost:9092"
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    market_data: MarketDataConfig = Field(default_factory=MarketDataConfig)
    agents: list[AgentConfig] = Field(default_factory=list)


class Secrets(BaseSettings):
    """Loads API keys from environment / .env file."""

    model_config = {"env_file": ".env", "extra": "ignore"}

    openai_api_key: str = ""
    anthropic_api_key: str = ""
