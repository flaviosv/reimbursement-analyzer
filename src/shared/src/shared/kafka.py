from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from confluent_kafka.aio import AIOProducer


def producer_config(
    bootstrap_servers: str,
    message_max_bytes: int,
    message_timeout_ms: int,
    queue_buffering_max_kbytes: int | None = None,
    security: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the librdkafka config dict shared by every producer in this
    project. Callers own their own topic/size/timeout/security values —
    nothing here hardcodes a feature-specific number."""
    config: dict[str, Any] = {
        "bootstrap.servers": bootstrap_servers,
        "acks": "all",
        "enable.idempotence": True,
        "message.max.bytes": message_max_bytes,
        "message.timeout.ms": message_timeout_ms,
    }
    if queue_buffering_max_kbytes is not None:
        config["queue.buffering.max.kbytes"] = queue_buffering_max_kbytes
    if security:
        config.update(security)
    return config


@asynccontextmanager
async def managed_producer(
    config: dict[str, Any], batch_size: int = 1, max_workers: int = 4
) -> AsyncIterator[AIOProducer]:
    """Construct an AIOProducer and guarantee `close()` on exit — the
    framework-agnostic half of producer lifecycle management. Callers (e.g.
    api's FastAPI lifespan) own wiring this into their own app/request
    objects.

    batch_size=1: the 1000-message/1.0s defaults would buffer up to 1000
    unflushed messages before flushing. Verified against
    confluent_kafka.aio's _AIOProducer.py source: the buffer flushes as soon
    as its size reaches batch_size.
    """
    producer = AIOProducer(config, batch_size=batch_size, max_workers=max_workers)
    try:
        yield producer
    finally:
        await producer.close()
