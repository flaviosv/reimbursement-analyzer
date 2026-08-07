from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from confluent_kafka.aio import AIOProducer
from fastapi import FastAPI, Request

from api.config import KAFKA_MAX_MESSAGE_BYTES, MESSAGE_TIMEOUT_MS, bootstrap_servers


def producer_config() -> dict[str, Any]:
    return {
        "bootstrap.servers": bootstrap_servers(),
        "acks": "all",
        "enable.idempotence": True,
        "message.max.bytes": KAFKA_MAX_MESSAGE_BYTES,
        "message.timeout.ms": MESSAGE_TIMEOUT_MS,
    }


@asynccontextmanager
async def lifespan_producer(app: FastAPI) -> AsyncIterator[None]:
    # batch_size=1: the 1000-message/1.0s defaults would add ~1s latency per
    # request and buffer up to 1000 x 25MB messages before flushing.
    # Verified against confluent_kafka.aio's _AIOProducer.py: the buffer
    # flushes as soon as its size reaches batch_size.
    producer = AIOProducer(producer_config(), batch_size=1)
    app.state.producer = producer
    try:
        yield
    finally:
        await producer.close()


def get_producer(request: Request) -> AIOProducer:
    return request.app.state.producer
