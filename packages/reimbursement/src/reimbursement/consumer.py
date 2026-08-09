"""Composition root and the consume loop.

Owns the consumer's lifecycle and the offset commit; every business branch
lives in validation.py, which needs no Kafka consumer to test.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from confluent_kafka.aio import AIOConsumer
from dotenv import load_dotenv
from shared.config import REIMBURSEMENT_TOPIC, Config, load_config
from shared.db import managed_pool
from shared.producer import managed_producer
from shared.signals import install_shutdown_handlers

from reimbursement.config import AgentConfig, load_agent_config
from reimbursement.validation import Dependencies, handle_message

logger = logging.getLogger(__name__)


def check_startup_config(config: Config) -> None:
    """Refuse to boot rather than fail obscurely on the first message."""
    if config.database.dsn is None:
        raise RuntimeError("DATABASE_URL is not set: the agent cannot reach Postgres without it")


@asynccontextmanager
async def managed_consumer(config: Config, agent: AgentConfig) -> AsyncIterator[AIOConsumer]:
    """Construct a subscribed consumer and guarantee `close()` on exit.

    Constructed here rather than at import time because AIOConsumer.__init__
    calls asyncio.get_event_loop() — building one outside a running loop
    binds its callbacks to the wrong loop.
    """
    consumer = AIOConsumer(agent.to_consumer_config(config.kafka))
    try:
        await consumer.subscribe([REIMBURSEMENT_TOPIC])
        yield consumer
    finally:
        await consumer.close()


async def run(deps: Dependencies, consumer: AIOConsumer, stopping: asyncio.Event) -> None:
    """One message at a time: handle it, then commit. A shutdown signal ends
    the loop only between messages, so the message in hand always finishes
    and commits — or, if interrupted mid-handling, its offset is never
    committed, and redelivery is safe because the read-then-conditionally-
    write shape (resolve, escalate) is naturally idempotent — there is no
    dedicated duplicate-detection mechanism here, unlike the publisher's
    unique-constraint-based one."""
    while not stopping.is_set():
        # consume() over poll(): AIOConsumer's own docs recommend it, and
        # num_messages=1 preserves the one-message-at-a-time semantics the
        # offset model depends on. No fan-out here — a Reimbursement message
        # already carries exactly one logical unit of work.
        messages = await consumer.consume(
            num_messages=1, timeout=deps.agent.consume_timeout_seconds
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
        # and whatever already happened (a ghost drop, a resolved log, an
        # escalation) is safely re-run — the read-then-conditionally-write
        # shape is naturally idempotent, unlike the publisher's INSERT path.
        await consumer.commit(message=message, asynchronous=False)


async def _serve() -> None:
    config = load_config()
    agent = load_agent_config()
    check_startup_config(config)

    stopping = asyncio.Event()
    install_shutdown_handlers(stopping)

    async with (
        managed_pool(config.database) as pool,
        managed_producer(config.kafka.to_producer_config()) as producer,
        managed_consumer(config, agent) as consumer,
    ):
        logger.info("agent consuming %s", REIMBURSEMENT_TOPIC)
        deps = Dependencies(config=config, agent=agent, pool=pool, producer=producer)
        await run(deps, consumer, stopping)


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
