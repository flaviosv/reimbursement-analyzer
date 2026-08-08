"""Publisher-only configuration — consumer tuning no other service reads.

Kept out of `shared.config`: that `Config` is process-wide across `api` and
`publisher`, but nothing here (`consumer_group_id`, `item_concurrency`,
`consume_timeout_seconds`, `max_poll_interval_ms`) is read by `api`, and
unlike `database`/`failure_log` it has no claim from the future Agent either
— a second consumer group needs its own tuning, not this one's."""

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from shared.config import KAFKA_MAX_MESSAGE_BYTES, KafkaConfig


@dataclass(frozen=True)
class PublisherConfig:
    consumer_group_id: str
    # Configurable, not just a literal, so a deployment that raises this
    # without raising DATABASE_POOL_MAX_SIZE in step is a startup failure
    # (consumer.check_startup_config) rather than silent connection
    # starvation under load (R-005).
    item_concurrency: int = 10
    consume_timeout_seconds: float = 1.0
    # AD-013's "~400x headroom" was derived from a ~15ms happy-path per-item
    # estimate only. The failure path is tighter: 500 items / 10 concurrent
    # * a full 10s publish_timeout_seconds each (broker down, every publish
    # times out) is 500s worst-case — which the previous 300_000 (5min)
    # value did not cover. 900_000 (15min) leaves real headroom above that
    # figure; see R-005 for the full derivation.
    max_poll_interval_ms: int = 900_000

    def to_consumer_config(self, kafka: KafkaConfig) -> dict[str, Any]:
        return {
            "bootstrap.servers": kafka.bootstrap_servers,
            "group.id": self.consumer_group_id,
            "auto.offset.reset": "earliest",
            # At librdkafka's default of true, offsets commit on a ~5s timer
            # regardless of whether their items settled — a crash mid-batch
            # would silently skip unprocessed items.
            "enable.auto.commit": False,
            "max.poll.interval.ms": self.max_poll_interval_ms,
            "fetch.max.bytes": KAFKA_MAX_MESSAGE_BYTES,
            "max.partition.fetch.bytes": KAFKA_MAX_MESSAGE_BYTES,
            **kafka.security_config(),
        }


@lru_cache(maxsize=1)
def load_publisher_config() -> PublisherConfig:
    return PublisherConfig(
        consumer_group_id=os.getenv("PUBLISHER_CONSUMER_GROUP_ID", "publisher"),
        item_concurrency=int(os.getenv("PUBLISHER_ITEM_CONCURRENCY", "10")),
    )
