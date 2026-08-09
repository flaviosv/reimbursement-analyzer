"""Agent-only configuration — consumer tuning no other service reads.

Kept out of `shared.config`: that `Config` is process-wide across every
service, but nothing here (`consumer_group_id`, `consume_timeout_seconds`)
is read by `api` or `publisher` — mirrors `src/publisher/src/config.py`'s
own reasoning for `PublisherConfig`."""

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from shared.config import KafkaConfig

_DEFAULT_OLLAMA_MODEL = "llama3.2"
_DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
_DEFAULT_OLLAMA_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class AgentConfig:
    consumer_group_id: str
    consume_timeout_seconds: float = 1.0
    # Ollama-only: single-service tuning, not process-wide, mirrors this
    # same file's own reasoning for keeping consumer_group_id out of
    # shared.config — Ollama is reimbursement-only today.
    ollama_model: str = _DEFAULT_OLLAMA_MODEL
    ollama_base_url: str = _DEFAULT_OLLAMA_BASE_URL
    # Bounds each LLM call so a hung Ollama server can't hold the decision
    # graph (and, transitively, a checked-out pool connection) open forever.
    ollama_timeout_seconds: float = _DEFAULT_OLLAMA_TIMEOUT_SECONDS

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
        ollama_model=os.getenv("OLLAMA_MODEL", _DEFAULT_OLLAMA_MODEL),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", _DEFAULT_OLLAMA_BASE_URL),
        ollama_timeout_seconds=float(
            os.getenv("OLLAMA_TIMEOUT_SECONDS", str(_DEFAULT_OLLAMA_TIMEOUT_SECONDS))
        ),
    )
