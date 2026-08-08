import os
from dataclasses import dataclass
from typing import Any

# --- topics ---------------------------------------------------------------
# Shared across every producer/consumer that touches this topic — the
# envelope byte-shape in shared.models.RequestEnvelope is this contract's
# other half.
REQUEST_TOPIC = "Request"

# --- batch cardinality (AD-013) ---------------------------------------------
# 500 items: at MAX_BODY_BYTES (1 MiB) this leaves ~2,097 bytes/item of
# headroom — ~5.5x the average item size in docs/original/sample.json —
# while independently bounding per-item validation cost (EmailStr regex,
# AwareDatetime parsing) that the byte-size cap doesn't limit. A cross-service
# wire constant, not api-local: the publisher's own item-concurrency timing
# (AD-013) is computed against this same number.
MAX_BATCH_ITEMS = 500

# --- http body ceiling ------------------------------------------------------
# 1 MiB, inclusive: a body of exactly this many bytes is accepted; one byte
# more is rejected. Sized against docs/original/sample.json (AD-020).
MAX_BODY_BYTES = 1_048_576

# --- kafka wire ceiling ------------------------------------------------------
# 1 MiB above MAX_BODY_BYTES so the envelope prefix and protocol framing
# always fit inside what the broker will accept. librdkafka's
# message.max.bytes default is 1_000_000 (range 1_000-1_000_000_000); this is
# raised on the broker, replica fetch, and producer sides so a
# MAX_BODY_BYTES-sized envelope is never rejected.
KAFKA_MAX_MESSAGE_BYTES = 2_097_152

# Everything above is a wire-protocol constant tied to the API contract
# itself — fixed regardless of where this service is deployed. KafkaConfig
# below is the opposite: the values that legitimately vary per deployment
# (AD-022).


@dataclass(frozen=True)
class KafkaConfig:
    bootstrap_servers: str = "localhost:9092"
    message_max_bytes: int = KAFKA_MAX_MESSAGE_BYTES
    message_timeout_ms: int = 8000
    publish_timeout_seconds: float = 10
    queue_buffering_max_kbytes: int = 200 * (KAFKA_MAX_MESSAGE_BYTES // 1024)
    security_protocol: str | None = None
    sasl_mechanism: str | None = None
    sasl_username: str | None = None
    sasl_password: str | None = None
    ssl_ca_location: str | None = None

    @classmethod
    def from_env(cls) -> KafkaConfig:
        return cls(
            bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", cls.bootstrap_servers),
            security_protocol=os.environ.get("KAFKA_SECURITY_PROTOCOL"),
            sasl_mechanism=os.environ.get("KAFKA_SASL_MECHANISM"),
            sasl_username=os.environ.get("KAFKA_SASL_USERNAME"),
            sasl_password=os.environ.get("KAFKA_SASL_PASSWORD"),
            ssl_ca_location=os.environ.get("KAFKA_SSL_CA_LOCATION"),
        )

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
