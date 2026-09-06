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
from shared.db import managed_pool
from shared.logging import configure_logging
from shared.producer import managed_producer

from publisher.config import PublisherConfig, load_publisher_config
from publisher.processing import MESSAGE_HANDLED_EVENT, Dependencies, _LazyJSON, handle_message

logger = logging.getLogger(__name__)


def check_startup_config(config: Config, publisher: PublisherConfig) -> None:
    """Refuse to boot rather than starve under load.

    Each in-flight item holds a transaction open across a Kafka round-trip, so
    concurrency N pins N connections. A pool below that limit does not fail —
    it degrades into contention that presents as unexplained slowness (R-005).
    """
    if config.database.dsn is None:
        raise RuntimeError(
            "DATABASE_URL is not set: the publisher cannot reach Postgres without it"
        )
    if config.database.pool_max_size < publisher.item_concurrency:
        raise RuntimeError(
            f"pool_max_size ({config.database.pool_max_size}) is below item_concurrency "
            f"({publisher.item_concurrency}): every in-flight item pins a connection "
            "for the length of a Kafka round-trip"
        )


@asynccontextmanager
async def managed_consumer(config: Config, publisher: PublisherConfig) -> AsyncIterator[AIOConsumer]:
    """Construct a subscribed consumer and guarantee `close()` on exit.

    Constructed here rather than at import time because AIOConsumer.__init__
    calls asyncio.get_event_loop() — building one outside a running loop binds
    its callbacks to the wrong loop.
    """
    consumer = AIOConsumer(publisher.to_consumer_config(config.kafka))
    try:
        await consumer.subscribe([REQUEST_TOPIC])
        yield consumer
    finally:
        await consumer.close()


async def run(deps: Dependencies, consumer: AIOConsumer, stopping: asyncio.Event) -> None:
    """One message at a time: fan its items out, wait for every one of them to
    settle, then commit. Matches PUB-33 AC5 precisely: on a termination
    signal, the message currently being processed completes and commits its
    offset, and the loop then exits without consuming another message —
    the `while not stopping.is_set()` check runs only between messages,
    never mid-message."""
    while not stopping.is_set():
        # num_messages=1 preserves the one-message-at-a-time semantics the
        # offset model depends on. Note this gains none of consume()'s usual
        # advantage over poll(): AIOConsumer's own docstring says that
        # benefit comes specifically from amortizing ThreadPoolExecutor
        # overhead "across the entire batch" — with num_messages=1 there is
        # no batch, so this call pays the identical per-call overhead poll()
        # would. consume() is kept anyway rather than swapped for poll() to
        # avoid a FakeConsumer/test rewrite for a change with no behavioral
        # difference; a real throughput reason to raise num_messages would
        # be the natural trigger to revisit this.
        messages = await consumer.consume(
            num_messages=1, timeout=deps.publisher.consume_timeout_seconds
        )
        if not messages:
            continue

        message = messages[0]
        error = message.error()
        if error is not None:
            logger.error("consumer error, message skipped: %s", error)
            continue

        try:
            outcomes = await handle_message(deps, message.value())
        except Exception:
            # handle_message is documented never to raise (PUB-32) — this is
            # a defence against that contract being broken, not the expected
            # path. Still commits: without it, a handler bug would redeliver
            # the same poisoned message forever instead of surfacing once.
            logger.exception("handle_message raised despite its never-raises contract")
            outcomes = []
        else:
            logger.info(
                "%s", _LazyJSON({"event": MESSAGE_HANDLED_EVENT, "outcomes": [o.value for o in outcomes]})
            )
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
    publisher = load_publisher_config()
    check_startup_config(config, publisher)

    stopping = asyncio.Event()
    _install_signal_handlers(stopping)

    async with (
        managed_pool(config.database) as pool,
        # max_workers matched to item_concurrency: AIOProducer's own default
        # of 4 would otherwise cap real publish parallelism below the
        # 10-item semaphore, silencing the concurrency this feature was
        # built and sized around (R-005's `500÷10` derivation).
        managed_producer(
            config.kafka.to_producer_config(), max_workers=publisher.item_concurrency
        ) as producer,
        managed_consumer(config, publisher) as consumer,
    ):
        logger.info("publisher consuming %s", REQUEST_TOPIC)
        deps = Dependencies(config=config, publisher=publisher, pool=pool, producer=producer)
        await run(deps, consumer, stopping)


def main() -> None:
    load_dotenv()
    configure_logging()
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
