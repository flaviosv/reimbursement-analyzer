import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from confluent_kafka import KafkaException
from confluent_kafka.aio import AIOProducer

from shared.errors import PublishFailed
from shared.tracing import inject_headers


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
    """Produce `payload` to `topic`, with the current trace context injected
    as Kafka message headers, and await its delivery verdict. Raises
    PublishFailed on a broker error or on timeout — never a bare exception,
    so every caller's error handling stays uniform.

    Bypasses `AIOProducer.produce()`'s batched async path — it raises
    `NotImplementedError` unconditionally when `headers` is passed (see
    `confluent_kafka.aio.producer._AIOProducer`, any batch size, this
    project's locked `confluent-kafka==2.15.0`). Instead this drives the
    same underlying synchronous `producer._producer` via `producer.executor`
    (the `ThreadPoolExecutor` `AIOProducer` already uses internally for
    every one of its own blocking calls — `poll`, `flush`, `purge`,
    `list_topics`), which is the only path that can carry headers at all.
    `batch_size` is hard-set to 1 everywhere in this codebase already, so
    today's async batched path already flushes on every single message —
    this changes only how the flush is triggered, not the observable
    latency/throughput.

    Stateless and topic/domain-agnostic on purpose, so every layer that
    needs to publish to Kafka shares this instead of re-implementing it.
    Header injection applies uniformly to every call site — there is
    exactly one `publish` function, so no caller special-cases this.
    Callers that need failure context beyond the exception's own message
    (e.g. correlating business IDs to a delivery failure) should catch
    PublishFailed and add that context themselves — this function has no
    concept of what it's carrying.
    """
    headers = inject_headers()

    def produce_and_flush() -> None:
        delivery_error: BaseException | None = None

        def on_delivery(err: Any, _msg: Any) -> None:
            nonlocal delivery_error
            if err is not None:
                delivery_error = KafkaException(err)

        producer._producer.produce(topic=topic, value=payload, headers=headers, on_delivery=on_delivery)
        pending = producer._producer.flush(timeout_seconds)
        if pending > 0:
            raise TimeoutError(f"{pending} message(s) still undelivered after {timeout_seconds}s")
        if delivery_error is not None:
            raise delivery_error

    try:
        await asyncio.get_running_loop().run_in_executor(producer.executor, produce_and_flush)
    except Exception as exc:
        raise PublishFailed(f"{type(exc).__name__}: {exc}") from exc
