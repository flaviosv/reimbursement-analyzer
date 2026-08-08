from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from confluent_kafka.aio import AIOProducer
from fastapi import FastAPI, Request
from shared.config import (
    KAFKA_MAX_MESSAGE_BYTES,
    MESSAGE_TIMEOUT_MS,
    bootstrap_servers,
    kafka_security_config,
)
from shared.kafka import managed_producer, producer_config

# Sized for ~200 concurrent max-size (1 MiB) in-flight messages before
# librdkafka's own BufferError kicks in — explicit rather than relying on
# the 1 GiB default, which at today's ceiling would silently tolerate far
# more concurrency than anyone has actually reasoned about.
QUEUE_BUFFERING_MAX_KBYTES = 200 * (KAFKA_MAX_MESSAGE_BYTES // 1024)


@asynccontextmanager
async def lifespan_producer(app: FastAPI) -> AsyncIterator[None]:
    config = producer_config(
        bootstrap_servers=bootstrap_servers(),
        message_max_bytes=KAFKA_MAX_MESSAGE_BYTES,
        message_timeout_ms=MESSAGE_TIMEOUT_MS,
        queue_buffering_max_kbytes=QUEUE_BUFFERING_MAX_KBYTES,
        security=kafka_security_config(),
    )
    async with managed_producer(config) as producer:
        app.state.producer = producer
        yield


def get_producer(request: Request) -> AIOProducer:
    return request.app.state.producer
