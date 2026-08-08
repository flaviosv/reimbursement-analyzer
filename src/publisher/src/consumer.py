"""Composition root and the consume loop.

Owns the consumer's lifecycle and the offset commit; every business branch
lives in processing.py, which needs no Kafka consumer to test.
"""

import asyncio
import logging
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from confluent_kafka.aio import AIOConsumer
from dotenv import load_dotenv
from shared.config import REQUEST_TOPIC, Config, load_config
from shared.producer import managed_producer
from shared.reimbursement.repository import managed_pool

from processing import Dependencies, handle_message

logger = logging.getLogger(__name__)


def check_startup_config(config: Config) -> None:
    """Refuse to boot rather than starve under load.

    Each in-flight item holds a transaction open across a Kafka round-trip, so
    concurrency N pins N connections. A pool below that limit does not fail —
    it degrades into contention that presents as unexplained slowness (R-005).
    """
    if config.database.dsn is None:
        raise RuntimeError(
            "DATABASE_URL is not set: the publisher cannot reach Postgres without it"
        )
    if config.database.pool_max_size < config.publisher.item_concurrency:
        raise RuntimeError(
            f"pool_max_size ({config.database.pool_max_size}) is below item_concurrency "
            f"({config.publisher.item_concurrency}): every in-flight item pins a connection "
            "for the length of a Kafka round-trip"
        )


@asynccontextmanager
async def managed_consumer(config: Config) -> AsyncIterator[AIOConsumer]:
    """Construct a subscribed consumer and guarantee `close()` on exit.

    Constructed here rather than at import time because AIOConsumer.__init__
    calls asyncio.get_event_loop() — building one outside a running loop binds
    its callbacks to the wrong loop.
    """
    consumer = AIOConsumer(config.publisher.to_consumer_config(config.kafka))
    try:
        await consumer.subscribe([REQUEST_TOPIC])
        yield consumer
    finally:
        await consumer.close()


async def run(deps: Dependencies, consumer: AIOConsumer, stopping: asyncio.Event) -> None:
    """One message at a time: fan its items out, wait for every one of them to
    settle, then commit. A shutdown signal ends the loop only between
    messages, so the message in hand always finishes and commits."""
    while not stopping.is_set():
        # consume() over poll(): AIOConsumer's own docs recommend it, and
        # num_messages=1 preserves the one-message-at-a-time semantics the
        # offset model depends on.
        messages = await consumer.consume(
            num_messages=1, timeout=deps.config.publisher.consume_timeout_seconds
        )
        if not messages:
            continue

        message = messages[0]
        error = message.error()
        if error is not None:
            logger.error("consumer error, message skipped: %s", error)
            continue

        await handle_message(deps, message.value())
        # Only now. A crash before this point redelivers the whole message,
        # and whatever already committed is absorbed by the duplicate path —
        # which is why that path is crash-recovery machinery, not just an
        # optimisation.
        await consumer.commit(message=message, asynchronous=False)


def _install_signal_handlers(stopping: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for received in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(received, stopping.set)


async def _serve() -> None:
    config = load_config()
    check_startup_config(config)

    stopping = asyncio.Event()
    _install_signal_handlers(stopping)

    async with (
        managed_pool(config.database) as pool,
        managed_producer(config.kafka.to_producer_config()) as producer,
        managed_consumer(config) as consumer,
    ):
        logger.info("publisher consuming %s", REQUEST_TOPIC)
        await run(Dependencies(config=config, pool=pool, producer=producer), consumer, stopping)


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
