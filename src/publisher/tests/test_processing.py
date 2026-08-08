import asyncio
import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID, uuid4

import asyncpg
import processing
import pytest
from helpers import valid_reimbursement_item
from processing import (
    DUPLICATE_DROPPED_EVENT,
    Dependencies,
    ItemOutcome,
    handle_message,
    process_item,
)
from shared.config import REIMBURSEMENT_TOPIC, REQUEST_TOPIC, load_config
from shared.models import AttemptError, RequestEnvelope, Stage
from shared.reimbursement.repository import insert_pending

pytestmark = pytest.mark.anyio


class FakeProducer:
    """Records every produced message. `errors` maps a topic to the exception
    its delivery future carries, so a Reimbursement failure and a requeue
    failure can be injected independently."""

    def __init__(self, *, errors: dict[str, Exception] | None = None) -> None:
        self.errors = errors or {}
        self.produced: list[tuple[str, bytes]] = []

    async def produce(self, topic: str, value: bytes, **kwargs: object) -> asyncio.Future:
        await asyncio.sleep(0)
        self.produced.append((topic, value))
        future = asyncio.get_running_loop().create_future()
        error = self.errors.get(topic)
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(object())
        return future

    def messages(self, topic: str) -> list[dict[str, Any]]:
        return [json.loads(value) for produced, value in self.produced if produced == topic]


class _FakeAcquisition:
    def __init__(self, pool: "FakePool") -> None:
        self.pool = pool

    async def __aenter__(self) -> "FakeConnection":
        if self.pool.acquire_error is not None:
            raise self.pool.acquire_error
        self.pool.in_flight += 1
        self.pool.max_in_flight = max(self.pool.max_in_flight, self.pool.in_flight)
        return FakeConnection(self.pool)

    async def __aexit__(self, *exc_info: object) -> bool:
        self.pool.in_flight -= 1
        return False


class _NullTransaction:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> bool:
        return False


class FakeConnection:
    def __init__(self, pool: "FakePool") -> None:
        self.pool = pool

    def transaction(self) -> _NullTransaction:
        return _NullTransaction()

    async def fetchval(self, statement: str, *args: Any) -> UUID:
        return await self.pool.insert(args)


class FakePool:
    """Stands in for an asyncpg pool. `insert_errors` fails the insert of the
    named request_ids only, so per-item independence can be exercised; the
    in-flight counters record how many items hold a connection at once."""

    def __init__(
        self,
        *,
        insert_errors: dict[str, Exception] | None = None,
        acquire_error: Exception | None = None,
    ) -> None:
        self.insert_errors = insert_errors or {}
        self.acquire_error = acquire_error
        self.inserted: list[tuple[Any, ...]] = []
        self.in_flight = 0
        self.max_in_flight = 0

    def acquire(self) -> _FakeAcquisition:
        return _FakeAcquisition(self)

    async def insert(self, args: tuple[Any, ...]) -> UUID:
        await asyncio.sleep(0)
        error = self.insert_errors.get(args[0])
        if error is not None:
            raise error
        self.inserted.append(args)
        return uuid4()


class _RealAcquisition:
    def __init__(self, connection: asyncpg.Connection) -> None:
        self.connection = connection

    async def __aenter__(self) -> asyncpg.Connection:
        return self.connection

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class RealPool:
    """Hands out the one real connection the `db` fixture owns, so the
    rollback and duplicate branches run against real asyncpg transaction
    semantics instead of a fake's own idea of them."""

    def __init__(self, connection: asyncpg.Connection) -> None:
        self.connection = connection

    def acquire(self) -> _RealAcquisition:
        return _RealAcquisition(self.connection)


def _deps(pool: Any, producer: Any) -> Dependencies:
    return Dependencies(config=load_config(), pool=pool, producer=producer)


def _envelope(
    items: list[dict[str, Any]], *, retry: int = 0, errors: Sequence[AttemptError] = ()
) -> RequestEnvelope:
    return RequestEnvelope(
        retry=retry,
        published_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        errors=list(errors),
        payload=items,
    )


def _error(attempt: int, stage: Stage) -> AttemptError:
    return AttemptError(
        attempt=attempt,
        occurred_at=datetime(2026, 1, 1, 11, attempt, 0, tzinfo=UTC),
        stage=stage,
        error_type="PostgresConnectionError",
        message="connection reset by peer",
    )


async def _row_count(db: asyncpg.Connection, request_id: str) -> int:
    return await db.fetchval("SELECT count(*) FROM reimbursement WHERE request_id = $1", request_id)


def _events(caplog: pytest.LogCaptureFixture, level: int) -> list[dict[str, Any]]:
    return [
        json.loads(record.message)
        for record in caplog.records
        if record.name == processing.__name__ and record.levelno == level
    ]


class DescribeHandleMessage:
    async def it_processes_every_item_as_its_own_insert_and_publish(self) -> None:
        items = [valid_reimbursement_item(f"REQ-{n}") for n in range(3)]
        pool, producer = FakePool(), FakeProducer()

        outcomes = await handle_message(
            _deps(pool, producer), _envelope(items).model_dump_json().encode()
        )

        assert outcomes == [ItemOutcome.PUBLISHED] * 3
        assert [row[0] for row in pool.inserted] == ["REQ-0", "REQ-1", "REQ-2"]
        assert len(producer.messages(REIMBURSEMENT_TOPIC)) == 3

    async def it_holds_at_most_the_configured_number_of_items_in_flight(self) -> None:
        items = [valid_reimbursement_item(f"REQ-{n}") for n in range(500)]
        pool, producer = FakePool(), FakeProducer()
        limit = load_config().publisher.item_concurrency

        await handle_message(_deps(pool, producer), _envelope(items).model_dump_json().encode())

        assert limit == 10
        assert pool.max_in_flight <= limit
        # Saturation, not merely a bound: a sequential implementation would
        # also satisfy "never exceeds 10" while breaking AD-013's timing.
        assert pool.max_in_flight == limit

    async def it_starts_every_item_rather_than_dropping_those_past_the_limit(self) -> None:
        items = [valid_reimbursement_item(f"REQ-{n}") for n in range(500)]
        pool, producer = FakePool(), FakeProducer()

        outcomes = await handle_message(
            _deps(pool, producer), _envelope(items).model_dump_json().encode()
        )

        assert outcomes == [ItemOutcome.PUBLISHED] * 500
        assert len(pool.inserted) == 500
        assert len(producer.messages(REIMBURSEMENT_TOPIC)) == 500

    async def it_leaves_the_other_items_outcomes_unchanged_when_one_fails(self) -> None:
        items = [valid_reimbursement_item(f"REQ-{n}") for n in range(3)]
        pool = FakePool(insert_errors={"REQ-1": asyncpg.PostgresConnectionError("reset")})
        producer = FakeProducer()

        outcomes = await handle_message(
            _deps(pool, producer), _envelope(items).model_dump_json().encode()
        )

        assert outcomes == [ItemOutcome.PUBLISHED, ItemOutcome.REQUEUED, ItemOutcome.PUBLISHED]
        assert [row[0] for row in pool.inserted] == ["REQ-0", "REQ-2"]
        assert [message["uuid"] for message in producer.messages(REIMBURSEMENT_TOPIC)] != []
        assert len(producer.messages(REIMBURSEMENT_TOPIC)) == 2


class DescribeTheReimbursementMessage:
    async def it_carries_the_uuid_of_the_row_just_inserted(self) -> None:
        item = valid_reimbursement_item("REQ-UUID")
        pool, producer = FakePool(), FakeProducer()

        await process_item(_deps(pool, producer), _envelope([item]), 0, item)

        published = producer.messages(REIMBURSEMENT_TOPIC)[0]
        assert UUID(published["uuid"])

    async def it_carries_retry_zero_and_an_aware_utc_publish_timestamp(self) -> None:
        item = valid_reimbursement_item("REQ-STAMP")
        pool, producer = FakePool(), FakeProducer()

        await process_item(_deps(pool, producer), _envelope([item], retry=2), 0, item)

        published = producer.messages(REIMBURSEMENT_TOPIC)[0]
        assert published["retry"] == 0
        published_at = datetime.fromisoformat(published["published_at"])
        assert published_at.utcoffset() == UTC.utcoffset(None)

    async def it_carries_no_request_payload(self) -> None:
        item = valid_reimbursement_item("REQ-NO-PAYLOAD", amount=93.5)
        pool, producer = FakePool(), FakeProducer()

        await process_item(_deps(pool, producer), _envelope([item]), 0, item)

        published = producer.messages(REIMBURSEMENT_TOPIC)[0]
        assert set(published) == {"uuid", "retry", "published_at", "errors"}
        assert "93.5" not in json.dumps(published)


class DescribeTheItemTransaction:
    async def it_commits_exactly_one_pending_row_when_both_steps_succeed(
        self, db: asyncpg.Connection
    ) -> None:
        item = valid_reimbursement_item("REQ-COMMITTED")
        producer = FakeProducer()

        outcome = await process_item(_deps(RealPool(db), producer), _envelope([item]), 0, item)

        assert outcome is ItemOutcome.PUBLISHED
        assert await _row_count(db, "REQ-COMMITTED") == 1
        published = producer.messages(REIMBURSEMENT_TOPIC)[0]
        row = await db.fetchrow(
            "SELECT status, submitted_by FROM reimbursement WHERE uuid = $1", UUID(published["uuid"])
        )
        assert row["status"] == "pending"
        assert row["submitted_by"] == "person@example.com"

    async def it_leaves_no_row_behind_when_the_publish_fails(self, db: asyncpg.Connection) -> None:
        item = valid_reimbursement_item("REQ-ROLLBACK")
        producer = FakeProducer(errors={REIMBURSEMENT_TOPIC: RuntimeError("broker unreachable")})

        outcome = await process_item(_deps(RealPool(db), producer), _envelope([item]), 0, item)

        assert outcome is ItemOutcome.REQUEUED
        assert await _row_count(db, "REQ-ROLLBACK") == 0

    async def it_does_not_publish_a_reimbursement_message_when_the_insert_fails(self) -> None:
        item = valid_reimbursement_item("REQ-NO-PUBLISH")
        pool = FakePool(insert_errors={"REQ-NO-PUBLISH": asyncpg.PostgresConnectionError("reset")})
        producer = FakeProducer()

        await process_item(_deps(pool, producer), _envelope([item]), 0, item)

        assert producer.messages(REIMBURSEMENT_TOPIC) == []


class DescribeADuplicateItem:
    async def it_drops_the_item_without_storing_a_second_row(self, db: asyncpg.Connection) -> None:
        await insert_pending(db, valid_reimbursement_item("REQ-DUP", submitted_by="ana@company.com"))
        resubmitted = valid_reimbursement_item("REQ-DUP", submitted_by="ANA@Company.com")
        producer = FakeProducer()

        outcome = await process_item(
            _deps(RealPool(db), producer), _envelope([resubmitted]), 0, resubmitted
        )

        assert outcome is ItemOutcome.DUPLICATE
        assert await _row_count(db, "REQ-DUP") == 1

    async def it_neither_republishes_it_nor_publishes_a_reimbursement_message(
        self, db: asyncpg.Connection
    ) -> None:
        await insert_pending(db, valid_reimbursement_item("REQ-DUP-QUIET"))
        item = valid_reimbursement_item("REQ-DUP-QUIET")
        producer = FakeProducer()

        await process_item(_deps(RealPool(db), producer), _envelope([item]), 0, item)

        assert producer.produced == []

    async def it_writes_no_failure_log_entry(
        self, db: asyncpg.Connection, caplog: pytest.LogCaptureFixture
    ) -> None:
        await insert_pending(db, valid_reimbursement_item("REQ-DUP-NOLOG"))
        item = valid_reimbursement_item("REQ-DUP-NOLOG")
        logger_name = load_config().failure_log.logger_name

        with caplog.at_level(logging.CRITICAL, logger=logger_name):
            await process_item(_deps(RealPool(db), FakeProducer()), _envelope([item]), 0, item)

        assert [record for record in caplog.records if record.name == logger_name] == []

    async def it_emits_a_countable_event_naming_the_constraint_and_the_envelope_retry(
        self, db: asyncpg.Connection, caplog: pytest.LogCaptureFixture
    ) -> None:
        await insert_pending(db, valid_reimbursement_item("REQ-DUP-EVENT"))
        item = valid_reimbursement_item("REQ-DUP-EVENT")

        with caplog.at_level(logging.INFO, logger=processing.__name__):
            await process_item(
                _deps(RealPool(db), FakeProducer()), _envelope([item], retry=2), 0, item
            )

        emitted = _events(caplog, logging.INFO)
        assert len(emitted) == 1
        assert emitted[0] == {
            "event": DUPLICATE_DROPPED_EVENT,
            "request_id": "REQ-DUP-EVENT",
            "constraint": "reimbursement_request_submitter_key",
            "retry": 2,
        }


class DescribeTheRequeue:
    async def it_republishes_only_the_failed_item_with_the_retry_incremented(self) -> None:
        items = [valid_reimbursement_item("REQ-A"), valid_reimbursement_item("REQ-B")]
        pool = FakePool(insert_errors={"REQ-B": asyncpg.PostgresConnectionError("reset")})
        producer = FakeProducer()

        outcome = await process_item(_deps(pool, producer), _envelope(items, retry=1), 1, items[1])

        assert outcome is ItemOutcome.REQUEUED
        requeued = producer.messages(REQUEST_TOPIC)[0]
        assert requeued["retry"] == 2
        assert [entry["request_id"] for entry in requeued["payload"]] == ["REQ-B"]

    async def it_republishes_the_item_with_the_same_keys_in_the_same_order(self) -> None:
        item = valid_reimbursement_item("REQ-VERBATIM", amount=93.5, currency="BRL")
        pool = FakePool(insert_errors={"REQ-VERBATIM": asyncpg.PostgresConnectionError("reset")})
        producer = FakeProducer()

        await process_item(_deps(pool, producer), _envelope([item]), 0, item)

        requeued = producer.messages(REQUEST_TOPIC)[0]
        assert requeued["payload"] == [item]
        assert list(requeued["payload"][0]) == list(item)

    async def it_appends_one_entry_naming_the_db_insert_stage_when_the_insert_fails(self) -> None:
        item = valid_reimbursement_item("REQ-DB-STAGE")
        pool = FakePool(insert_errors={"REQ-DB-STAGE": asyncpg.PostgresConnectionError("reset")})
        producer = FakeProducer()

        await process_item(_deps(pool, producer), _envelope([item]), 0, item)

        errors = producer.messages(REQUEST_TOPIC)[0]["errors"]
        assert len(errors) == 1
        assert errors[0]["stage"] == "db-insert"
        assert errors[0]["attempt"] == 1
        assert errors[0]["error_type"] == "PostgresConnectionError"
        assert "reset" in errors[0]["message"]
        assert datetime.fromisoformat(errors[0]["occurred_at"]).tzinfo is not None

    async def it_appends_one_entry_naming_the_publish_stage_when_the_publish_fails(self) -> None:
        item = valid_reimbursement_item("REQ-PUB-STAGE")
        pool = FakePool()
        producer = FakeProducer(errors={REIMBURSEMENT_TOPIC: RuntimeError("broker unreachable")})

        await process_item(_deps(pool, producer), _envelope([item]), 0, item)

        errors = producer.messages(REQUEST_TOPIC)[0]["errors"]
        assert len(errors) == 1
        assert errors[0]["stage"] == "publish"
        assert errors[0]["error_type"] == "PublishFailed"

    async def it_appends_to_the_carried_history_rather_than_replacing_it(self) -> None:
        item = valid_reimbursement_item("REQ-HISTORY")
        pool = FakePool(insert_errors={"REQ-HISTORY": asyncpg.PostgresConnectionError("reset")})
        producer = FakeProducer()
        carried = [_error(1, "publish")]

        await process_item(_deps(pool, producer), _envelope([item], errors=carried), 0, item)

        errors = producer.messages(REQUEST_TOPIC)[0]["errors"]
        assert [entry["attempt"] for entry in errors] == [1, 2]
        assert [entry["stage"] for entry in errors] == ["publish", "db-insert"]

    async def it_keeps_the_drivers_value_bearing_detail_out_of_the_stdout_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        item = valid_reimbursement_item("REQ-PII", submitted_by="ana@company.com")
        leaky = asyncpg.PostgresConnectionError(
            "Key (request_id, lower(submitted_by))=(REQ-PII, ana@company.com) already exists"
        )
        pool = FakePool(insert_errors={"REQ-PII": leaky})

        with caplog.at_level(logging.ERROR, logger=processing.__name__):
            await process_item(_deps(pool, FakeProducer()), _envelope([item]), 0, item)

        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert "ana@company.com" not in logged
        assert "PostgresConnectionError" in logged

    async def it_falls_back_to_the_failure_log_when_the_requeue_itself_fails(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        item = valid_reimbursement_item("REQ-LAST-RESORT")
        pool = FakePool(insert_errors={"REQ-LAST-RESORT": asyncpg.PostgresConnectionError("reset")})
        producer = FakeProducer(errors={REQUEST_TOPIC: RuntimeError("broker unreachable")})
        logger_name = load_config().failure_log.logger_name

        with caplog.at_level(logging.CRITICAL, logger=logger_name):
            outcome = await process_item(_deps(pool, producer), _envelope([item]), 0, item)

        assert outcome is ItemOutcome.LOGGED
        written = [
            json.loads(record.message) for record in caplog.records if record.name == logger_name
        ]
        assert len(written) == 1
        assert written[0]["request_id"] == "REQ-LAST-RESORT"
        assert written[0]["item"] == item
        assert [entry["stage"] for entry in written[0]["errors"]] == ["db-insert"]

    async def it_returns_an_outcome_rather_than_propagating_an_unexpected_exception(self) -> None:
        item = valid_reimbursement_item("REQ-UNEXPECTED")
        pool = FakePool(acquire_error=RuntimeError("pool is closed"))
        producer = FakeProducer()

        outcome = await process_item(_deps(pool, producer), _envelope([item]), 0, item)

        assert outcome is ItemOutcome.REQUEUED
        errors = producer.messages(REQUEST_TOPIC)[0]["errors"]
        assert errors[0]["error_type"] == "RuntimeError"
