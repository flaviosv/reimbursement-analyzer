import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from agent.consumer import managed_consumer, run
from agent.validation import Dependencies
from confluent_kafka import KafkaException, TopicPartition
from helpers import valid_reimbursement_item
from shared.config import REIMBURSEMENT_TOPIC, Config, load_config
from shared.models import ReimbursementEnvelope
from shared.producer import managed_producer, publish
from shared.reimbursement.repository import insert_pending, managed_pool

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

# One group for the whole module, so each test's run picks up only the
# messages it produced: a fresh group per test would re-read every earlier
# test's message from the shared topic.
GROUP_ID = f"agent-integration-{time.monotonic_ns()}"


def _config() -> Config:
    config = load_config()
    return replace(config, agent=replace(config.agent, consumer_group_id=GROUP_ID))


async def _insert_row(migrated_db: str, request_id: str) -> UUID:
    connection = await asyncpg.connect(migrated_db)
    try:
        return await insert_pending(connection, valid_reimbursement_item(request_id))
    finally:
        await connection.close()


async def _row(migrated_db: str, uuid: UUID) -> asyncpg.Record:
    connection = await asyncpg.connect(migrated_db)
    try:
        return await connection.fetchrow("SELECT * FROM reimbursement WHERE uuid = $1", uuid)
    finally:
        await connection.close()


async def _produce(config: Config, envelope: ReimbursementEnvelope) -> None:
    async with managed_producer(config.kafka.to_producer_config()) as producer:
        await publish(
            producer,
            REIMBURSEMENT_TOPIC,
            envelope.model_dump_json().encode(),
            config.kafka.publish_timeout_seconds,
        )


async def _committed_offset(kafka_consumer: Any, timeout: float = 30.0) -> int:
    """The group's coordinator may still be electing on the first call of a
    session, which surfaces as NOT_COORDINATOR — a transient condition, not
    an offset of zero, so it is retried rather than read as an answer."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            partitions = await kafka_consumer.committed(
                [TopicPartition(REIMBURSEMENT_TOPIC, 0)], timeout=20.0
            )
        except KafkaException:
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(0.5)
            continue
        offset = partitions[0].offset
        return offset if offset >= 0 else 0


async def _run_agent(
    config: Config, migrated_db: str, expected_advance: int, timeout: float = 90.0
) -> None:
    """Drive the real loop until the offset has advanced by
    `expected_advance` (one per message, regardless of outcome), then stop
    it."""
    stopping = asyncio.Event()
    async with (
        managed_pool(replace(config.database, dsn=migrated_db)) as pool,
        managed_producer(config.kafka.to_producer_config()) as producer,
        managed_consumer(config) as kafka_consumer,
    ):
        before = await _committed_offset(kafka_consumer)
        deps = Dependencies(config=config, pool=pool, producer=producer)
        task = asyncio.create_task(run(deps, kafka_consumer, stopping))
        deadline = time.monotonic() + timeout
        try:
            while await _committed_offset(kafka_consumer) - before < expected_advance:
                if time.monotonic() >= deadline:
                    raise AssertionError(
                        f"offset advanced by only "
                        f"{await _committed_offset(kafka_consumer) - before} of {expected_advance}"
                    )
                await asyncio.sleep(0.2)
        finally:
            stopping.set()
            await task


class DescribeTheEndToEndRoundTrip:
    async def it_resolves_a_fresh_message_for_a_real_row(
        self, kafka_bootstrap_server: str, migrated_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", kafka_bootstrap_server)
        config = _config()
        uuid = await _insert_row(migrated_db, "REQ-E2E-RESOLVED")
        envelope = ReimbursementEnvelope(uuid=uuid, retry=0, published_at=datetime.now(UTC))
        await _produce(config, envelope)

        await _run_agent(config, migrated_db, expected_advance=1)

        row = await _row(migrated_db, uuid)
        assert row["status"] == "pending"  # unchanged — no side effect beyond the log

    async def it_drops_a_message_whose_uuid_matches_no_row(
        self, kafka_bootstrap_server: str, migrated_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", kafka_bootstrap_server)
        config = _config()
        ghost_uuid = uuid4()  # never inserted — R-001's dual-write window, simulated directly
        envelope = ReimbursementEnvelope(uuid=ghost_uuid, retry=0, published_at=datetime.now(UTC))
        await _produce(config, envelope)

        # No exception, no hang — the offset still advances for a ghost.
        await _run_agent(config, migrated_db, expected_advance=1)

    async def it_ignores_a_message_older_than_the_rows_last_update(
        self, kafka_bootstrap_server: str, migrated_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", kafka_bootstrap_server)
        config = _config()
        uuid = await _insert_row(migrated_db, "REQ-E2E-STALE")
        row_before = await _row(migrated_db, uuid)
        stale_published_at = row_before["updated_at"] - timedelta(hours=1)
        envelope = ReimbursementEnvelope(uuid=uuid, retry=0, published_at=stale_published_at)
        await _produce(config, envelope)

        await _run_agent(config, migrated_db, expected_advance=1)

        row_after = await _row(migrated_db, uuid)
        assert row_after["status"] == "pending"
        assert row_after["decision_reason"] is None

    async def it_escalates_a_row_at_the_retry_ceiling_to_human_review(
        self, kafka_bootstrap_server: str, migrated_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", kafka_bootstrap_server)
        config = _config()
        uuid = await _insert_row(migrated_db, "REQ-E2E-ESCALATED")
        envelope = ReimbursementEnvelope(uuid=uuid, retry=4, published_at=datetime.now(UTC))
        await _produce(config, envelope)

        await _run_agent(config, migrated_db, expected_advance=1)

        row = await _row(migrated_db, uuid)
        assert row["status"] == "human-review"
        assert row["decision_reason"] is not None
