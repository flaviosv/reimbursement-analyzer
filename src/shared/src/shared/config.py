"""Centralised application configuration — single source of truth for every env-var read."""

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

# Wire-protocol constants tied to the API contract itself, not to
# deployment — every service reads these directly, they never come from
# Config/load_config below.
REQUEST_TOPIC = "Request"
MAX_BATCH_ITEMS = 500
MAX_BODY_BYTES = 1_048_576
KAFKA_MAX_MESSAGE_BYTES = 2_097_152


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

    def to_producer_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "bootstrap.servers": self.bootstrap_servers,
            "acks": "all",
            "enable.idempotence": True,
            "message.max.bytes": self.message_max_bytes,
            "message.timeout.ms": self.message_timeout_ms,
            "queue.buffering.max.kbytes": self.queue_buffering_max_kbytes,
        }
        if self.security_protocol:
            config["security.protocol"] = self.security_protocol
        if self.sasl_mechanism:
            config["sasl.mechanism"] = self.sasl_mechanism
        if self.sasl_username:
            config["sasl.username"] = self.sasl_username
        if self.sasl_password:
            config["sasl.password"] = self.sasl_password
        if self.ssl_ca_location:
            config["ssl.ca.location"] = self.ssl_ca_location
        return config


@dataclass(frozen=True)
class Config:
    kafka: KafkaConfig


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
    )
