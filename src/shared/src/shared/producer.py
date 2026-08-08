import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from confluent_kafka.aio import AIOProducer

from shared.errors import PublishFailed


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


async def publish(producer: AIOProducer, topic: str, payload: bytes, timeout_seconds: float) -> None:
    """Produce `payload` to `topic` and await the per-message delivery
    Future. Raises PublishFailed on a broker error or on timeout — never a
    bare exception, so every caller's error handling stays uniform.

    Stateless and topic/domain-agnostic on purpose, so every layer that
    needs to publish to Kafka shares this instead of re-implementing it.
    Callers that need failure context beyond the exception's own message
    (e.g. correlating business IDs to a delivery failure) should catch
    PublishFailed and add that context themselves — this function has no
    concept of what it's carrying.
    """
    try:
        delivery_future = await producer.produce(topic=topic, value=payload)
        await asyncio.wait_for(delivery_future, timeout=timeout_seconds)
    except Exception as exc:
        raise PublishFailed(f"{type(exc).__name__}: {exc}") from exc
