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


@dataclass(frozen=True)
class AgentConfig:
    consumer_group_id: str
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
    )
