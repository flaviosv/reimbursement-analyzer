"""Agent-only configuration — consumer tuning and the two LLM nodes' model
config no other service reads.

Kept out of `shared.config`: that `Config` is process-wide across every
service, but nothing here (`consumer_group_id`, `consume_timeout_seconds`,
`ai`, `models`) is read by `api` or `publisher` — mirrors
`src/publisher/src/config.py`'s own reasoning for `PublisherConfig`."""

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from shared.config import KafkaConfig

_DEFAULT_AI_TIMEOUT_SECONDS = 30.0
_DEFAULT_TEMPERATURE = 0.0


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} environment variable is required")
    return value


@dataclass(frozen=True)
class ModelConfig:
    model_name: str
    temperature: float = _DEFAULT_TEMPERATURE


@dataclass(frozen=True)
class AIConfig:
    api_key: str
    timeout_seconds: float = _DEFAULT_AI_TIMEOUT_SECONDS


@dataclass(frozen=True)
class AgentModelsConfig:
    extract_fields: ModelConfig
    analysis: ModelConfig


@dataclass(frozen=True)
class AgentConfig:
    consumer_group_id: str
    ai: AIConfig
    models: AgentModelsConfig
    consume_timeout_seconds: float = 1.0

    def to_consumer_config(self, kafka: KafkaConfig) -> dict[str, Any]:
        # No fetch.max.bytes/max.partition.fetch.bytes override, unlike
        # PublisherConfig: Reimbursement messages are small, fixed-shape
        # envelopes, so librdkafka's default fetch sizing is sufficient.
        return {
            "bootstrap.servers": kafka.bootstrap_servers,
            "group.id": self.consumer_group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
            **kafka.security_config(),
        }


@lru_cache(maxsize=1)
def load_agent_config() -> AgentConfig:
    return AgentConfig(
        consumer_group_id=os.getenv("AGENT_CONSUMER_GROUP_ID", "agent"),
        ai=AIConfig(
            api_key=_require_env("GROQ_API_KEY"),
            timeout_seconds=float(
                os.getenv("AI_TIMEOUT_SECONDS", str(_DEFAULT_AI_TIMEOUT_SECONDS))
            ),
        ),
        models=AgentModelsConfig(
            extract_fields=ModelConfig(
                model_name=_require_env("EXTRACT_FIELDS_MODEL_NAME"),
                temperature=float(
                    os.getenv("EXTRACT_FIELDS_TEMPERATURE", str(_DEFAULT_TEMPERATURE))
                ),
            ),
            analysis=ModelConfig(
                model_name=_require_env("ANALYSIS_MODEL_NAME"),
                temperature=float(
                    os.getenv("ANALYSIS_TEMPERATURE", str(_DEFAULT_TEMPERATURE))
                ),
            ),
        ),
    )
