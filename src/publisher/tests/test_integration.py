import asyncio
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from confluent_kafka import Consumer, KafkaException, TopicPartition
from config import PublisherConfig, load_publisher_config
from consumer import managed_consumer, run
from helpers import valid_reimbursement_item
from processing import Dependencies
from shared.config import (
    KAFKA_MAX_MESSAGE_BYTES,
    REIMBURSEMENT_TOPIC,
    REQUEST_TOPIC,
    Config,
    load_config,
)
from shared.db import managed_pool
from shared.models import RequestEnvelope
from shared.producer import managed_producer, publish

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

# One group for the whole module, so each test's run picks up only the message
# it produced: a fresh group per test would re-read every earlier test's
# message from the shared topic.
GROUP_ID = f"publisher-integration-{time.monotonic_ns()}"

# Room for the record framing librdkafka adds on top of the value, so the
# message stays under the broker's ceiling. Sized from the constant the
# broker, the API's producer and the consumer all share -- never retyped.
FRAMING_HEADROOM = 4096


def _config() -> tuple[Config, PublisherConfig]:
    return load_config(), replace(load_publisher_config(), consumer_group_id=GROUP_ID)


def _envelope(items: list[dict[str, Any]]) -> bytes:
    return (
        RequestEnvelope(retry=0, published_at=datetime.now(UTC), payload=items)
        .model_dump_json()
        .encode()
    )


async def _produce_request(config: Config, raw: bytes) -> None:
    async with managed_producer(config.kafka.to_producer_config()) as producer:
        await publish(producer, REQUEST_TOPIC, raw, config.kafka.publish_timeout_seconds)


async def _committed_offset(kafka_consumer: Any, timeout: float = 30.0) -> int:
    """The group's coordinator may still be electing on the first call of a
    session, which surfaces as NOT_COORDINATOR — a transient condition, not an
    offset of zero, so it is retried rather than read as an answer."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            partitions = await kafka_consumer.committed(
                [TopicPartition(REQUEST_TOPIC, 0)], timeout=20.0
            )
        except KafkaException:
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(0.5)
            continue
        offset = partitions[0].offset
        # Kafka reports an unset offset as a large negative sentinel.
        return offset if offset >= 0 else 0


async def _await_rows(pool: asyncpg.Pool, request_ids: list[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stored = await pool.fetchval(
            "SELECT count(*) FROM reimbursement WHERE request_id = ANY($1)", request_ids
        )
        if stored == len(request_ids):
            return
        await asyncio.sleep(0.2)
    raise AssertionError(f"only {stored} of {len(request_ids)} rows landed within {timeout}s")


async def _run_publisher(
    config: Config,
    publisher: PublisherConfig,
    migrated_db: str,
    request_ids: list[str],
    timeout: float = 90.0,
) -> int:
    """Drive the real loop until every expected row lands, then stop it.
    Returns how far the source offset advanced."""
    stopping = asyncio.Event()
    async with (
        managed_pool(replace(config.database, dsn=migrated_db)) as pool,
        managed_producer(config.kafka.to_producer_config()) as producer,
        managed_consumer(config, publisher) as kafka_consumer,
    ):
        before = await _committed_offset(kafka_consumer)
        deps = Dependencies(config=config, publisher=publisher, pool=pool, producer=producer)
        task = asyncio.create_task(run(deps, kafka_consumer, stopping))
        try:
            await _await_rows(pool, request_ids, timeout)
        finally:
            stopping.set()
            await task
        return await _committed_offset(kafka_consumer) - before


def _drain_reimbursements(
    bootstrap_server: str, uuids: set[str], timeout: float, grace_seconds: float = 2.0
) -> list[dict]:
    """Collect the Reimbursement messages carrying this test's uuids. Drains
    from the beginning with a throwaway group, since the topic is shared by
    every test in the session.

    Stopping the instant `len(collected) == len(uuids)` (as this used to)
    made every "exactly one message" assertion in this module unable to
    detect an over-publish by construction — it can only prove "at least
    this many showed up in time." Once the target count is reached, this
    keeps draining for `grace_seconds` more before returning,
    so a genuine double-publish within that window still shows up — without
    taxing the common (correct) case with the full `timeout`.
    """
    kafka_consumer = Consumer(
        {
            "bootstrap.servers": bootstrap_server,
            "group.id": f"drain-{time.monotonic_ns()}",
            "auto.offset.reset": "earliest",
            "fetch.max.bytes": KAFKA_MAX_MESSAGE_BYTES,
            "max.partition.fetch.bytes": KAFKA_MAX_MESSAGE_BYTES,
        }
    )
    kafka_consumer.subscribe([REIMBURSEMENT_TOPIC])
    collected: list[dict] = []
    satisfied_at: float | None = None
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if satisfied_at is not None and time.monotonic() >= satisfied_at:
                break
            message = kafka_consumer.poll(1.0)
            if message is None:
                continue
            if message.error():
                raise AssertionError(f"consumer error: {message.error()}")
            decoded = json.loads(message.value())
            if decoded["uuid"] in uuids:
                collected.append(decoded)
                if satisfied_at is None and len(collected) >= len(uuids):
                    satisfied_at = time.monotonic() + grace_seconds
        return collected
    finally:
        kafka_consumer.close()


async def _rows(migrated_db: str, request_ids: list[str]) -> list[asyncpg.Record]:
    connection = await asyncpg.connect(migrated_db)
    try:
        return await connection.fetch(
            "SELECT uuid, request_id, status FROM reimbursement "
            "WHERE request_id = ANY($1) ORDER BY request_id",
            request_ids,
        )
    finally:
        await connection.close()


class DescribeTheEndToEndRoundTrip:
    async def it_turns_every_item_into_one_committed_row_and_one_reimbursement_message(
        self, kafka_bootstrap_server: str, migrated_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", kafka_bootstrap_server)
        config, publisher = _config()
        request_ids = [f"REQ-E2E-BATCH-{n}" for n in range(3)]
        await _produce_request(
            config, _envelope([valid_reimbursement_item(rid) for rid in request_ids])
        )

        await _run_publisher(config, publisher, migrated_db, request_ids)

        rows = await _rows(migrated_db, request_ids)
        assert [row["request_id"] for row in rows] == request_ids
        assert [row["status"] for row in rows] == ["pending"] * 3
        uuids = {str(row["uuid"]) for row in rows}
        published = _drain_reimbursements(kafka_bootstrap_server, uuids, timeout=60.0)
        assert {message["uuid"] for message in published} == uuids
        assert len(published) == 3

    async def it_publishes_the_uuid_of_a_stored_row_and_carries_no_payload(
        self, kafka_bootstrap_server: str, migrated_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", kafka_bootstrap_server)
        config, publisher = _config()
        request_ids = ["REQ-E2E-UUID"]
        await _produce_request(
            config, _envelope([valid_reimbursement_item("REQ-E2E-UUID", amount=93.5)])
        )

        await _run_publisher(config, publisher, migrated_db, request_ids)

        rows = await _rows(migrated_db, request_ids)
        uuids = {str(row["uuid"]) for row in rows}
        published = _drain_reimbursements(kafka_bootstrap_server, uuids, timeout=60.0)
        assert len(published) == 1
        # The Agent resolves the payload from the row by uuid (AD-015), so the
        # message must carry the identifier and nothing of the request itself.
        assert set(published[0]) == {"uuid", "retry", "published_at", "errors"}
        assert published[0]["retry"] == 0
        assert "93.5" not in json.dumps(published[0])

    async def it_advances_the_source_offset_exactly_once_for_the_message(
        self, kafka_bootstrap_server: str, migrated_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", kafka_bootstrap_server)
        config, publisher = _config()
        request_ids = [f"REQ-E2E-OFFSET-{n}" for n in range(2)]
        await _produce_request(
            config, _envelope([valid_reimbursement_item(rid) for rid in request_ids])
        )

        advanced = await _run_publisher(config, publisher, migrated_db, request_ids)

        # Two items, one message: the offset moves once, not once per item.
        assert advanced == 1

    async def it_consumes_and_processes_a_message_at_the_size_ceiling(
        self, kafka_bootstrap_server: str, migrated_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", kafka_bootstrap_server)
        config, publisher = _config()
        request_ids = ["REQ-E2E-CEILING"]
        item = valid_reimbursement_item("REQ-E2E-CEILING", padding="")
        target = KAFKA_MAX_MESSAGE_BYTES - FRAMING_HEADROOM
        item["padding"] = "x" * (target - len(_envelope([item])))
        raw = _envelope([item])
        assert len(raw) == target
        # Genuinely past librdkafka's 1 MiB default fetch, so the round trip is
        # at the ceiling and not merely near it. It does not prove the fetch
        # sizing: KIP-74 makes fetch.max.bytes a soft limit and the broker
        # returns one record regardless — see PUB-35's Assumptions row.
        assert len(raw) > 1_048_576

        await _produce_request(config, raw)
        await _run_publisher(config, publisher, migrated_db, request_ids)

        rows = await _rows(migrated_db, request_ids)
        assert [row["status"] for row in rows] == ["pending"]
        uuids = {str(row["uuid"]) for row in rows}
        assert len(_drain_reimbursements(kafka_bootstrap_server, uuids, timeout=60.0)) == 1
