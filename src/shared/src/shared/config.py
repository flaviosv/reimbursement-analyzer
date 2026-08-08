"""Centralised application configuration — single source of truth for every env-var read."""

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

# Wire-protocol constants tied to the API contract itself, not to
# deployment — every service reads these directly, they never come from
# Config/load_config below.
REQUEST_TOPIC = "Request"
REIMBURSEMENT_TOPIC = "Reimbursement"
MAX_BATCH_ITEMS = 500
MAX_BODY_BYTES = 1_048_576
KAFKA_MAX_MESSAGE_BYTES = 2_097_152
MAX_RETRY = 3


@dataclass(frozen=True)
class KafkaConfig:
    bootstrap_servers: str
    message_max_bytes: int
    message_timeout_ms: int
    publish_timeout_seconds: float
    queue_buffering_max_kbytes: int
    security_protocol: str | None = None
    sasl_mechanism: str | None = None
    sasl_username: str | None = None
    sasl_password: str | None = None
    ssl_ca_location: str | None = None

    def security_config(self) -> dict[str, Any]:
        """The optional SASL/TLS half of a client config, shared by producers
        and consumers alike — neither re-derives it."""
        options = {
            "security.protocol": self.security_protocol,
            "sasl.mechanism": self.sasl_mechanism,
            "sasl.username": self.sasl_username,
            "sasl.password": self.sasl_password,
            "ssl.ca.location": self.ssl_ca_location,
        }
        return {key: value for key, value in options.items() if value}

    def to_producer_config(self) -> dict[str, Any]:
        return {
            "bootstrap.servers": self.bootstrap_servers,
            "acks": "all",
            "enable.idempotence": True,
            "message.max.bytes": self.message_max_bytes,
            "message.timeout.ms": self.message_timeout_ms,
            "queue.buffering.max.kbytes": self.queue_buffering_max_kbytes,
            **self.security_config(),
        }


@dataclass(frozen=True)
class DatabaseConfig:
    dsn: str | None
    pool_min_size: int
    pool_max_size: int


@dataclass(frozen=True)
class FailureLogConfig:
    logger_name: str
    max_message_chars: int


@dataclass(frozen=True)
class PublisherConfig:
    consumer_group_id: str
    item_concurrency: int = 10
    consume_timeout_seconds: float = 1.0
    # librdkafka's own default, stated rather than inherited: AD-013 sizes the
    # 500-item cap and the concurrency of 10 against this budget, so the
    # number it is sized against must be visible here (PUB-42).
    max_poll_interval_ms: int = 300_000

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


@dataclass(frozen=True)
class Config:
    kafka: KafkaConfig
    database: DatabaseConfig
    failure_log: FailureLogConfig
    publisher: PublisherConfig
    agent: AgentConfig


@lru_cache(maxsize=1)
def load_config() -> Config:
    return Config(
        kafka=KafkaConfig(
            bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            message_max_bytes=KAFKA_MAX_MESSAGE_BYTES,
            message_timeout_ms=8000,
            publish_timeout_seconds=10,
            queue_buffering_max_kbytes=200 * (KAFKA_MAX_MESSAGE_BYTES // 1024),
            security_protocol=os.getenv("KAFKA_SECURITY_PROTOCOL"),
            sasl_mechanism=os.getenv("KAFKA_SASL_MECHANISM"),
            sasl_username=os.getenv("KAFKA_SASL_USERNAME"),
            sasl_password=os.getenv("KAFKA_SASL_PASSWORD"),
            ssl_ca_location=os.getenv("KAFKA_SSL_CA_LOCATION"),
        ),
        # os.getenv, never os.environ[...]: this loader is process-wide and
        # cached, so an unconditional read would make DATABASE_URL mandatory
        # for api too, which never touches Postgres. The publisher validates
        # presence at its own startup.
        database=DatabaseConfig(
            dsn=os.getenv("DATABASE_URL"),
            pool_min_size=2,
            pool_max_size=20,
        ),
        failure_log=FailureLogConfig(
            logger_name="reimbursementanalyzer.failures",
            max_message_chars=2000,
        ),
        publisher=PublisherConfig(
            consumer_group_id=os.getenv("PUBLISHER_CONSUMER_GROUP_ID", "publisher"),
        ),
        agent=AgentConfig(
            consumer_group_id=os.getenv("AGENT_CONSUMER_GROUP_ID", "agent"),
        ),
    )
