import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
import publisher.processing as processing
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from publisher.config import load_publisher_config
from fakes import FakePool, FakeProducer, RealPool
from shared.testing import valid_reimbursement_item
from publisher.processing import (
    DUPLICATE_DROPPED_EVENT,
    EMPTY_PAYLOAD_EVENT,
    Dependencies,
    ItemOutcome,
    _failure_record,
    handle_message,
    process_item,
)
from shared.config import (
    MAX_RETRY,
    REIMBURSEMENT_TOPIC,
    REQUEST_TOPIC,
    load_config,
)
from shared.logging import get_correlation_id
from shared.models import AttemptError, RequestEnvelope, Stage
from shared.reimbursement.repository import insert_pending
import shared.reimbursement.use_cases.publish_pending as publish_pending_module

pytestmark = pytest.mark.anyio


def _deps(pool: Any, producer: Any) -> Dependencies:
    return Dependencies(
        config=load_config(), publisher=load_publisher_config(), pool=pool, producer=producer
    )


def _envelope(
    items: list[dict[str, Any]],
    *,
    retry: int = 0,
    errors: Sequence[AttemptError] = (),
    correlation_id: str | None = None,
) -> RequestEnvelope:
    return RequestEnvelope(
        retry=retry,
        published_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        correlation_id=correlation_id,
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


def _requeued_raw(producer: FakeProducer) -> bytes:
    """The exact bytes a requeue put back on `Request`, ready to be consumed
    again — the round trip PUB-14 is about."""
    return next(value for topic, value in producer.produced if topic == REQUEST_TOPIC)


def _stdout_log(caplog: pytest.LogCaptureFixture) -> str:
    """Only the module's own records. The failure log is a separate logger and
    deliberately carries the payload verbatim (PUB-15 bounds stdout, not it)."""
    return "\n".join(
        record.getMessage() for record in caplog.records if record.name == processing.__name__
    )


def _failures(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    logger_name = load_config().failure_log.logger_name
    return [json.loads(record.message) for record in caplog.records if record.name == logger_name]


class DescribeHandleMessage:
    async def it_logs_and_skips_a_null_valued_record_rather_than_crashing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A tombstone/null-value record crashed the consumer permanently
        # via `raw.decode(...)` on None.
        pool, producer = FakePool(), FakeProducer()

        with caplog.at_level(logging.CRITICAL, logger=load_config().failure_log.logger_name):
            outcomes = await handle_message(_deps(pool, producer), None)

        assert outcomes == [ItemOutcome.LOGGED]
        assert pool.inserted == []
        assert producer.produced == []

    async def it_processes_every_item_as_its_own_insert_and_publish(self) -> None:
        items = [valid_reimbursement_item(f"REQ-{n}") for n in range(3)]
        pool, producer = FakePool(), FakeProducer()

        outcomes = await handle_message(
            _deps(pool, producer), _envelope(items).model_dump_json().encode()
        )

        assert outcomes == [ItemOutcome.PUBLISHED] * 3
        # Sorted, not positional: `inserted` is appended on completion, and
        # completion order is explicitly non-deterministic (PUB-39).
        assert sorted(row[0] for row in pool.inserted) == ["REQ-0", "REQ-1", "REQ-2"]
        assert len(producer.messages(REIMBURSEMENT_TOPIC)) == 3

    async def it_holds_at_most_the_configured_number_of_items_in_flight(self) -> None:
        items = [valid_reimbursement_item(f"REQ-{n}") for n in range(500)]
        pool, producer = FakePool(), FakeProducer()
        limit = load_publisher_config().item_concurrency

        await handle_message(_deps(pool, producer), _envelope(items).model_dump_json().encode())

        assert limit == 10
        assert pool.max_in_flight <= limit
        # Saturation, not merely a bound: a sequential implementation would
        # also satisfy "never exceeds 10" while breaking AD-013's timing.
        # (A wall-clock timing variant of this test previously lived here
        # too — removed as flaky under real contention and redundant: this
        # assertion already proves the same saturation guarantee
        # deterministically, with no clock involved.)
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
        assert sorted(row[0] for row in pool.inserted) == ["REQ-0", "REQ-2"]
        assert [message["uuid"] for message in producer.messages(REIMBURSEMENT_TOPIC)] != []
        assert len(producer.messages(REIMBURSEMENT_TOPIC)) == 2

    async def it_settles_every_item_correctly_when_they_complete_out_of_order(self) -> None:
        items = [valid_reimbursement_item(f"REQ-{n}") for n in range(3)]
        pool = FakePool(
            insert_errors={"REQ-0": asyncpg.PostgresConnectionError("reset")},
            insert_turns={"REQ-0": 4, "REQ-1": 2},
        )
        producer = FakeProducer()

        outcomes = await handle_message(
            _deps(pool, producer), _envelope(items).model_dump_json().encode()
        )

        # Completion runs REQ-2, REQ-1, REQ-0 — the reverse of submission — so
        # an outcome list built in completion order would read
        # [PUBLISHED, PUBLISHED, REQUEUED] instead.
        assert [row[0] for row in pool.inserted] == ["REQ-2", "REQ-1"]
        assert outcomes == [ItemOutcome.REQUEUED, ItemOutcome.PUBLISHED, ItemOutcome.PUBLISHED]
        assert [
            message["payload"][0]["request_id"] for message in producer.messages(REQUEST_TOPIC)
        ] == ["REQ-0"]


class DescribeHandleMessageCorrelationId:
    async def it_sets_the_correlation_id_from_the_envelope_for_the_duration_of_processing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str | None] = []

        async def _spy_process_item(deps: Any, envelope: Any, index: int, item: Any) -> ItemOutcome:
            seen.append(get_correlation_id())
            return ItemOutcome.PUBLISHED

        monkeypatch.setattr(processing, "process_item", _spy_process_item)
        item = valid_reimbursement_item("REQ-CORR-1")

        await handle_message(
            _deps(FakePool(), FakeProducer()),
            _envelope([item], correlation_id="corr-envelope-1").model_dump_json().encode(),
        )

        assert seen == ["corr-envelope-1"]

    async def it_resets_the_correlation_id_once_handling_completes(self) -> None:
        item = valid_reimbursement_item("REQ-CORR-2")

        await handle_message(
            _deps(FakePool(), FakeProducer()),
            _envelope([item], correlation_id="corr-envelope-2").model_dump_json().encode(),
        )

        assert get_correlation_id() is None

    async def it_leaves_the_correlation_id_absent_when_the_envelope_carries_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str | None] = []

        async def _spy_process_item(deps: Any, envelope: Any, index: int, item: Any) -> ItemOutcome:
            seen.append(get_correlation_id())
            return ItemOutcome.PUBLISHED

        monkeypatch.setattr(processing, "process_item", _spy_process_item)
        item = valid_reimbursement_item("REQ-CORR-3")

        await handle_message(
            _deps(FakePool(), FakeProducer()), _envelope([item]).model_dump_json().encode()
        )

        assert seen == [None]

    async def it_never_leaks_one_messages_correlation_id_into_the_next_sequential_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str | None] = []

        async def _spy_process_item(deps: Any, envelope: Any, index: int, item: Any) -> ItemOutcome:
            seen.append(get_correlation_id())
            return ItemOutcome.PUBLISHED

        monkeypatch.setattr(processing, "process_item", _spy_process_item)
        deps = _deps(FakePool(), FakeProducer())

        await handle_message(
            deps,
            _envelope([valid_reimbursement_item("REQ-CORR-4")], correlation_id="corr-first")
            .model_dump_json()
            .encode(),
        )
        await handle_message(
            deps, _envelope([valid_reimbursement_item("REQ-CORR-5")]).model_dump_json().encode()
        )

        assert seen == ["corr-first", None]


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
        assert set(published) == {"uuid", "retry", "published_at", "correlation_id", "errors"}
        assert "93.5" not in json.dumps(published)

    async def it_carries_the_request_envelopes_correlation_id_onto_the_published_message(
        self,
    ) -> None:
        item = valid_reimbursement_item("REQ-CORR-FORWARD")
        pool, producer = FakePool(), FakeProducer()

        await process_item(
            _deps(pool, producer), _envelope([item], correlation_id="corr-request-1"), 0, item
        )

        published = producer.messages(REIMBURSEMENT_TOPIC)[0]
        assert published["correlation_id"] == "corr-request-1"

    async def it_omits_the_correlation_id_field_as_null_when_the_request_envelope_had_none(
        self,
    ) -> None:
        item = valid_reimbursement_item("REQ-NO-CORR-FORWARD")
        pool, producer = FakePool(), FakeProducer()

        await process_item(_deps(pool, producer), _envelope([item]), 0, item)

        published = producer.messages(REIMBURSEMENT_TOPIC)[0]
        assert published["correlation_id"] is None

    async def it_forwards_the_envelopes_error_history_even_when_this_attempt_succeeds(
        self,
    ) -> None:
        # An item that failed twice then succeeds on the third attempt must
        # still hand the Agent that history — this was the only construction
        # site, and it silently dropped `errors`.
        item = valid_reimbursement_item("REQ-HANDOFF")
        pool, producer = FakePool(), FakeProducer()
        history = _three_failures()[:2]

        await process_item(_deps(pool, producer), _envelope([item], retry=2, errors=history), 0, item)

        published = producer.messages(REIMBURSEMENT_TOPIC)[0]
        assert len(published["errors"]) == 2
        assert [entry["stage"] for entry in published["errors"]] == [error.stage for error in history]


class DescribeInsertThenPublish:
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

    async def it_stamps_reimbursement_uuid_on_the_current_span_once_published(self) -> None:
        item = valid_reimbursement_item("REQ-SPAN-ATTR")
        pool, producer = FakePool(), FakeProducer()
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer(__name__)

        with tracer.start_as_current_span("process_message"):
            outcome = await process_item(_deps(pool, producer), _envelope([item]), 0, item)

        assert outcome is ItemOutcome.PUBLISHED
        published_uuid = producer.messages(REIMBURSEMENT_TOPIC)[0]["uuid"]
        span = exporter.get_finished_spans()[0]
        assert span.attributes["reimbursement.uuid"] == published_uuid

    async def it_leaves_no_row_behind_when_the_publish_fails(self, db: asyncpg.Connection) -> None:
        # No transaction to roll back anymore (AD-033) — the row is gone via
        # publish_pending's explicit compensating delete instead. The
        # observable outcome PUB-09 names (no row survives a publish
        # failure) is unchanged, only the mechanism is.
        item = valid_reimbursement_item("REQ-ROLLBACK")
        producer = FakeProducer(errors={REIMBURSEMENT_TOPIC: RuntimeError("broker unreachable")})

        outcome = await process_item(_deps(RealPool(db), producer), _envelope([item]), 0, item)

        assert outcome is ItemOutcome.REQUEUED
        assert await _row_count(db, "REQ-ROLLBACK") == 0

    async def it_still_requeues_when_the_compensating_delete_itself_fails(self) -> None:
        item = valid_reimbursement_item("REQ-DELETE-FAILS")
        pool = FakePool(delete_errors={"REQ-DELETE-FAILS": asyncpg.PostgresConnectionError("reset")})
        producer = FakeProducer(errors={REIMBURSEMENT_TOPIC: RuntimeError("broker unreachable")})

        outcome = await process_item(_deps(pool, producer), _envelope([item]), 0, item)

        assert outcome is ItemOutcome.REQUEUED

    async def it_forwards_the_envelopes_retry_into_the_compensating_delete_log(
        self, db: asyncpg.Connection, caplog: pytest.LogCaptureFixture
    ) -> None:
        # publish_pending's retry parameter is now threaded through from
        # envelope.retry, not a hardcoded default (processing.py:299) — this
        # proves the real wiring, not just that a directly-supplied retry
        # kwarg reaches the log (already covered at the shared-package level).
        item = valid_reimbursement_item("REQ-RETRY-WIRING")
        producer = FakeProducer(errors={REIMBURSEMENT_TOPIC: RuntimeError("broker unreachable")})

        with caplog.at_level(logging.INFO, logger=publish_pending_module.__name__):
            outcome = await process_item(
                _deps(RealPool(db), producer), _envelope([item], retry=2), 0, item
            )

        assert outcome is ItemOutcome.REQUEUED
        events = [
            json.loads(record.message)
            for record in caplog.records
            if record.name == publish_pending_module.__name__ and record.levelno == logging.INFO
        ]
        assert len(events) == 1
        assert events[0]["retry"] == 2

    async def it_does_not_publish_a_reimbursement_message_when_the_insert_fails(self) -> None:
        item = valid_reimbursement_item("REQ-NO-PUBLISH")
        pool = FakePool(insert_errors={"REQ-NO-PUBLISH": asyncpg.PostgresConnectionError("reset")})
        producer = FakeProducer()

        await process_item(_deps(pool, producer), _envelope([item]), 0, item)

        assert producer.messages(REIMBURSEMENT_TOPIC) == []


class DescribeADuplicateItem:
    async def it_drops_the_item_without_storing_a_second_row(self, db: asyncpg.Connection) -> None:
        stored = valid_reimbursement_item("REQ-DUP", submitted_by="ana@company.com", amount=10.0)
        await insert_pending(db, stored)
        resubmitted = valid_reimbursement_item(
            "REQ-DUP", submitted_by="ANA@Company.com", amount=999.0
        )
        producer = FakeProducer()

        outcome = await process_item(
            _deps(RealPool(db), producer), _envelope([resubmitted]), 0, resubmitted
        )

        assert outcome is ItemOutcome.DUPLICATE
        assert await _row_count(db, "REQ-DUP") == 1
        # The surviving row is the one already committed, unchanged (PUB-21).
        row = await db.fetchrow("SELECT * FROM reimbursement WHERE request_id = $1", "REQ-DUP")
        assert json.loads(row["original_payload"]) == stored
        assert row["submitted_by"] == "ana@company.com"
        assert row["status"] == "pending"

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

        with caplog.at_level(logging.CRITICAL, logger=load_config().failure_log.logger_name):
            await process_item(_deps(RealPool(db), FakeProducer()), _envelope([item]), 0, item)

        assert _failures(caplog) == []

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
    async def it_carries_forward_the_original_correlation_id_unchanged(self) -> None:
        item = valid_reimbursement_item("REQ-CORR-REQUEUE")
        pool = FakePool(insert_errors={"REQ-CORR-REQUEUE": asyncpg.PostgresConnectionError("reset")})
        producer = FakeProducer()

        await process_item(
            _deps(pool, producer),
            _envelope([item], correlation_id="corr-original"),
            0,
            item,
        )

        requeued = producer.messages(REQUEST_TOPIC)[0]
        assert requeued["correlation_id"] == "corr-original"

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

        logged = _stdout_log(caplog)
        assert "ana@company.com" not in logged
        assert "PostgresConnectionError" in logged

    async def it_reprocesses_the_message_it_requeued_like_any_other(self) -> None:
        item = valid_reimbursement_item("REQ-ROUND-TRIP")
        first = FakeProducer()
        await handle_message(
            _deps(
                FakePool(insert_errors={"REQ-ROUND-TRIP": asyncpg.PostgresConnectionError("reset")}),
                first,
            ),
            _envelope([item]).model_dump_json().encode(),
        )
        pool, second = FakePool(), FakeProducer()

        outcomes = await handle_message(_deps(pool, second), _requeued_raw(first))

        assert outcomes == [ItemOutcome.PUBLISHED]
        assert [row[0] for row in pool.inserted] == ["REQ-ROUND-TRIP"]
        assert len(second.messages(REIMBURSEMENT_TOPIC)) == 1
        assert second.messages(REQUEST_TOPIC) == []

    async def it_carries_the_requeued_history_into_the_attempt_after_it(self) -> None:
        item = valid_reimbursement_item("REQ-ROUND-TRIP-HISTORY")
        insert_errors = {"REQ-ROUND-TRIP-HISTORY": asyncpg.PostgresConnectionError("reset")}
        first = FakeProducer()
        await handle_message(
            _deps(FakePool(insert_errors=insert_errors), first),
            _envelope([item]).model_dump_json().encode(),
        )
        second = FakeProducer()

        await handle_message(
            _deps(FakePool(insert_errors=insert_errors), second), _requeued_raw(first)
        )

        requeued = second.messages(REQUEST_TOPIC)[0]
        assert requeued["retry"] == 2
        assert [entry["attempt"] for entry in requeued["errors"]] == [1, 2]
        assert requeued["payload"] == [item]

    async def it_identifies_the_failed_item_by_its_request_id(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        item = valid_reimbursement_item(
            "REQ-IDENTIFIED", submitted_by="ana@company.com", amount=93.5
        )
        pool = FakePool(insert_errors={"REQ-IDENTIFIED": asyncpg.PostgresConnectionError("reset")})

        with caplog.at_level(logging.ERROR, logger=processing.__name__):
            await process_item(_deps(pool, FakeProducer()), _envelope([item]), 0, item)

        logged = _stdout_log(caplog)
        assert "REQ-IDENTIFIED" in logged
        assert "ana@company.com" not in logged
        assert "93.5" not in logged

    async def it_falls_back_to_the_failure_log_when_the_requeue_itself_fails(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        item = valid_reimbursement_item("REQ-LAST-RESORT")
        pool = FakePool(insert_errors={"REQ-LAST-RESORT": asyncpg.PostgresConnectionError("reset")})
        producer = FakeProducer(errors={REQUEST_TOPIC: RuntimeError("broker unreachable")})

        with caplog.at_level(logging.CRITICAL, logger=load_config().failure_log.logger_name):
            outcome = await process_item(_deps(pool, producer), _envelope([item]), 0, item)

        assert outcome is ItemOutcome.LOGGED
        written = _failures(caplog)
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


def _invalid_item(request_id: str = "REQ-INVALID") -> dict[str, Any]:
    return {
        "request_id": request_id,
        "submitted_by": "not-an-email-at-all",
        "submitted_at": "2026-01-01T12:00:00Z",
    }


def _three_failures() -> list[AttemptError]:
    return [
        AttemptError(
            attempt=n,
            occurred_at=datetime(2026, 1, 1, 11, n, 0, tzinfo=UTC),
            stage=stage,
            error_type=error_type,
            message=message,
        )
        for n, stage, error_type, message in (
            (1, "db-insert", "PostgresConnectionError", "connection reset by peer"),
            (2, "publish", "PublishFailed", "broker unreachable"),
            (3, "db-insert", "CheckViolationError", "submitted_at is in the future"),
        )
    ]


class DescribeTheRetryCeilingBoundary:
    """`retry > 3`, not `>= 3`. The two neighbouring values are the whole
    difference between a fourth attempt and escalating one attempt early."""

    async def it_takes_the_normal_path_at_the_ceiling_itself(self) -> None:
        item = valid_reimbursement_item("REQ-AT-CEILING")
        pool, producer = FakePool(), FakeProducer()

        outcomes = await handle_message(
            _deps(pool, producer),
            _envelope([item], retry=MAX_RETRY, errors=_three_failures()).model_dump_json().encode(),
        )

        assert MAX_RETRY == 3
        assert outcomes == [ItemOutcome.PUBLISHED]
        assert [row[0] for row in pool.inserted] == ["REQ-AT-CEILING"]
        assert len(producer.messages(REIMBURSEMENT_TOPIC)) == 1

    async def it_escalates_one_attempt_later(self, db: asyncpg.Connection) -> None:
        item = valid_reimbursement_item("REQ-PAST-CEILING")
        producer = FakeProducer()

        outcomes = await handle_message(
            _deps(RealPool(db), producer),
            _envelope([item], retry=MAX_RETRY + 1, errors=_three_failures())
            .model_dump_json()
            .encode(),
        )

        assert outcomes == [ItemOutcome.ESCALATED]
        status = await db.fetchval(
            "SELECT status FROM reimbursement WHERE request_id = $1", "REQ-PAST-CEILING"
        )
        assert status == "human-review"
        assert producer.produced == []

    async def it_stamps_reimbursement_uuid_on_the_current_span_once_escalated(
        self, db: asyncpg.Connection
    ) -> None:
        item = valid_reimbursement_item("REQ-ESCALATE-SPAN-ATTR")
        producer = FakeProducer()
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer(__name__)

        with tracer.start_as_current_span("process_message"):
            outcomes = await handle_message(
                _deps(RealPool(db), producer),
                _envelope([item], retry=MAX_RETRY + 1, errors=_three_failures())
                .model_dump_json()
                .encode(),
            )

        assert outcomes == [ItemOutcome.ESCALATED]
        escalated_uuid = await db.fetchval(
            "SELECT uuid FROM reimbursement WHERE request_id = $1", "REQ-ESCALATE-SPAN-ATTR"
        )
        span = exporter.get_finished_spans()[0]
        assert span.attributes["reimbursement.uuid"] == str(escalated_uuid)


class DescribeAMessagePastTheRetryCeiling:
    async def it_preserves_every_item_as_a_human_review_row_instead_of_the_normal_path(
        self, db: asyncpg.Connection
    ) -> None:
        items = [valid_reimbursement_item(f"REQ-CEIL-{n}") for n in range(3)]
        producer = FakeProducer()

        outcomes = await handle_message(
            _deps(RealPool(db), producer),
            _envelope(items, retry=MAX_RETRY + 1, errors=_three_failures()).model_dump_json().encode(),
        )

        assert outcomes == [ItemOutcome.ESCALATED] * 3
        rows = await db.fetch(
            "SELECT request_id, status FROM reimbursement WHERE request_id = ANY($1) ORDER BY request_id",
            [item["request_id"] for item in items],
        )
        assert [(row["request_id"], row["status"]) for row in rows] == [
            ("REQ-CEIL-0", "human-review"),
            ("REQ-CEIL-1", "human-review"),
            ("REQ-CEIL-2", "human-review"),
        ]

    async def it_publishes_nothing_and_requeues_nothing(self, db: asyncpg.Connection) -> None:
        item = valid_reimbursement_item("REQ-CEIL-QUIET")
        producer = FakeProducer()

        await handle_message(
            _deps(RealPool(db), producer),
            _envelope([item], retry=MAX_RETRY + 1, errors=_three_failures()).model_dump_json().encode(),
        )

        assert producer.produced == []

    async def it_records_the_full_error_history_in_the_decision_reason(
        self, db: asyncpg.Connection
    ) -> None:
        item = valid_reimbursement_item("REQ-CEIL-WHY")

        await handle_message(
            _deps(RealPool(db), FakeProducer()),
            _envelope([item], retry=MAX_RETRY + 1, errors=_three_failures()).model_dump_json().encode(),
        )

        reason = await db.fetchval(
            "SELECT decision_reason FROM reimbursement WHERE request_id = $1", "REQ-CEIL-WHY"
        )
        assert "connection reset by peer" in reason
        assert "broker unreachable" in reason
        assert "submitted_at is in the future" in reason

    async def it_writes_the_item_and_its_history_to_the_failure_log_when_the_insert_fails(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        item = valid_reimbursement_item("REQ-CEIL-DOWN")
        pool = FakePool(insert_errors={"REQ-CEIL-DOWN": asyncpg.PostgresConnectionError("reset")})
        producer = FakeProducer()

        with caplog.at_level(logging.CRITICAL, logger=load_config().failure_log.logger_name):
            outcomes = await handle_message(
                _deps(pool, producer),
                _envelope([item], retry=MAX_RETRY + 1, errors=_three_failures()).model_dump_json().encode(),
            )

        assert outcomes == [ItemOutcome.LOGGED]
        written = _failures(caplog)
        assert len(written) == 1
        assert written[0]["item"] == item
        assert [entry["message"] for entry in written[0]["errors"]] == [
            "connection reset by peer",
            "broker unreachable",
            "submitted_at is in the future",
        ]

    async def it_identifies_the_item_it_could_not_escalate_by_its_request_id(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        item = valid_reimbursement_item(
            "REQ-CEIL-IDENTIFIED", submitted_by="ana@company.com", amount=93.5
        )
        pool = FakePool(
            insert_errors={"REQ-CEIL-IDENTIFIED": asyncpg.PostgresConnectionError("reset")}
        )

        with caplog.at_level(logging.ERROR, logger=processing.__name__):
            await handle_message(
                _deps(pool, FakeProducer()),
                _envelope([item], retry=MAX_RETRY + 1).model_dump_json().encode(),
            )

        logged = _stdout_log(caplog)
        assert "REQ-CEIL-IDENTIFIED" in logged
        assert "ana@company.com" not in logged
        assert "93.5" not in logged

    async def it_publishes_nothing_when_the_escalation_insert_fails(self) -> None:
        item = valid_reimbursement_item("REQ-CEIL-DOWN-QUIET")
        pool = FakePool(
            insert_errors={"REQ-CEIL-DOWN-QUIET": asyncpg.PostgresConnectionError("reset")}
        )
        producer = FakeProducer()

        await handle_message(
            _deps(pool, producer), _envelope([item], retry=MAX_RETRY + 1).model_dump_json().encode()
        )

        assert producer.produced == []

    async def it_drops_the_item_as_a_duplicate_when_the_escalation_collides(
        self, db: asyncpg.Connection
    ) -> None:
        await insert_pending(db, valid_reimbursement_item("REQ-CEIL-DUP"))
        item = valid_reimbursement_item("REQ-CEIL-DUP")

        outcomes = await handle_message(
            _deps(RealPool(db), FakeProducer()),
            _envelope([item], retry=MAX_RETRY + 1, errors=_three_failures()).model_dump_json().encode(),
        )

        assert outcomes == [ItemOutcome.DUPLICATE]
        assert await _row_count(db, "REQ-CEIL-DUP") == 1

    async def it_writes_no_failure_log_entry_for_that_duplicate(
        self, db: asyncpg.Connection, caplog: pytest.LogCaptureFixture
    ) -> None:
        await insert_pending(db, valid_reimbursement_item("REQ-CEIL-DUP-NOLOG"))
        item = valid_reimbursement_item("REQ-CEIL-DUP-NOLOG")

        with caplog.at_level(logging.CRITICAL, logger=load_config().failure_log.logger_name):
            await handle_message(
                _deps(RealPool(db), FakeProducer()),
                _envelope([item], retry=MAX_RETRY + 1).model_dump_json().encode(),
            )

        assert _failures(caplog) == []


class DescribeAMalformedMessage:
    async def it_logs_a_body_that_is_not_json_at_all(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        pool, producer = FakePool(), FakeProducer()

        with caplog.at_level(logging.CRITICAL, logger=load_config().failure_log.logger_name):
            outcomes = await handle_message(_deps(pool, producer), b"this is not json")

        assert outcomes == [ItemOutcome.LOGGED]
        assert _failures(caplog)[0]["message"] == "this is not json"

    async def it_logs_an_envelope_that_does_not_match_the_schema(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        pool, producer = FakePool(), FakeProducer()

        with caplog.at_level(logging.CRITICAL, logger=load_config().failure_log.logger_name):
            outcomes = await handle_message(_deps(pool, producer), b'{"retry": 0}')

        assert outcomes == [ItemOutcome.LOGGED]
        assert len(_failures(caplog)) == 1

    async def it_logs_each_item_separately_when_the_envelope_shape_is_still_recoverable(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A flat `{"message": <entire raw envelope>}` record truncates to one
        # blob of max_message_chars total — losing everything past the first
        # cut for a ceiling-sized batch. When the bytes are still valid JSON
        # with a payload list, each item gets logged (and truncated)
        # separately instead (R1).
        pool, producer = FakePool(), FakeProducer()
        raw = json.dumps(
            {
                "retry": "not-an-int",
                "published_at": "2026-01-01T00:00:00Z",
                "payload": [{"request_id": "REQ-RECOVERABLE"}],
            }
        ).encode()

        with caplog.at_level(logging.CRITICAL, logger=load_config().failure_log.logger_name):
            outcomes = await handle_message(_deps(pool, producer), raw)

        assert outcomes == [ItemOutcome.LOGGED]
        record = _failures(caplog)[0]
        assert "message" not in record
        assert record["items"] == [{"request_id": "REQ-RECOVERABLE"}]

    async def it_touches_neither_the_database_nor_the_broker(self) -> None:
        pool, producer = FakePool(), FakeProducer()

        await handle_message(_deps(pool, producer), b"this is not json")

        assert pool.acquisitions == 0
        assert pool.inserted == []
        assert producer.produced == []

    async def it_lets_the_next_valid_message_process_normally(self) -> None:
        pool, producer = FakePool(), FakeProducer()
        good = valid_reimbursement_item("REQ-AFTER-BAD")

        await handle_message(_deps(pool, producer), b"this is not json")
        outcomes = await handle_message(
            _deps(pool, producer), _envelope([good]).model_dump_json().encode()
        )

        assert outcomes == [ItemOutcome.PUBLISHED]
        assert [row[0] for row in pool.inserted] == ["REQ-AFTER-BAD"]


class DescribeAnInvalidItem:
    async def it_is_neither_retried_nor_published(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        pool, producer = FakePool(), FakeProducer()

        with caplog.at_level(logging.CRITICAL, logger=load_config().failure_log.logger_name):
            outcomes = await handle_message(
                _deps(pool, producer), _envelope([_invalid_item()]).model_dump_json().encode()
            )

        assert outcomes == [ItemOutcome.INVALID]
        assert producer.produced == []
        assert pool.inserted == []

    async def it_is_written_to_the_failure_log(self, caplog: pytest.LogCaptureFixture) -> None:
        item = _invalid_item("REQ-INVALID-LOGGED")

        with caplog.at_level(logging.CRITICAL, logger=load_config().failure_log.logger_name):
            await handle_message(
                _deps(FakePool(), FakeProducer()), _envelope([item]).model_dump_json().encode()
            )

        written = _failures(caplog)
        assert len(written) == 1
        assert written[0]["item"] == item
        assert written[0]["outcome"] == ItemOutcome.INVALID.value

    async def it_is_identified_by_its_request_id_in_the_stdout_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        item = _invalid_item("REQ-INVALID-IDENTIFIED")

        with caplog.at_level(logging.ERROR, logger=processing.__name__):
            await handle_message(
                _deps(FakePool(), FakeProducer()), _envelope([item]).model_dump_json().encode()
            )

        logged = _stdout_log(caplog)
        assert "REQ-INVALID-IDENTIFIED" in logged
        assert "not-an-email-at-all" not in logged

    async def it_gets_no_human_review_row_past_the_retry_ceiling(
        self, db: asyncpg.Connection
    ) -> None:
        item = _invalid_item("REQ-INVALID-CEIL")

        outcomes = await handle_message(
            _deps(RealPool(db), FakeProducer()),
            _envelope([item], retry=MAX_RETRY + 1, errors=_three_failures()).model_dump_json().encode(),
        )

        assert outcomes == [ItemOutcome.INVALID]
        assert await _row_count(db, "REQ-INVALID-CEIL") == 0

    async def it_leaves_the_valid_items_beside_it_unaffected(self) -> None:
        items = [valid_reimbursement_item("REQ-GOOD"), _invalid_item()]
        pool, producer = FakePool(), FakeProducer()

        outcomes = await handle_message(
            _deps(pool, producer), _envelope(items).model_dump_json().encode()
        )

        assert outcomes == [ItemOutcome.PUBLISHED, ItemOutcome.INVALID]
        assert [row[0] for row in pool.inserted] == ["REQ-GOOD"]


class DescribeAnEmptyPayload:
    async def it_is_dropped_as_a_logged_no_op(self, caplog: pytest.LogCaptureFixture) -> None:
        pool, producer = FakePool(), FakeProducer()

        with caplog.at_level(logging.INFO, logger=processing.__name__):
            outcomes = await handle_message(
                _deps(pool, producer), _envelope([], retry=1).model_dump_json().encode()
            )

        assert outcomes == []
        assert _events(caplog, logging.INFO) == [{"event": EMPTY_PAYLOAD_EVENT, "retry": 1}]
        assert pool.acquisitions == 0
        assert producer.produced == []

    async def it_is_not_treated_as_an_error(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.CRITICAL, logger=load_config().failure_log.logger_name):
            await handle_message(
                _deps(FakePool(), FakeProducer()), _envelope([]).model_dump_json().encode()
            )

        assert _failures(caplog) == []
        assert [record for record in caplog.records if record.levelno >= logging.ERROR] == []


class DescribeFailureRecord:
    def it_carries_the_event_index_request_id_item_and_errors(self) -> None:
        error = _error(1, "db-insert")
        item = {"request_id": "REQ-1", "submitted_by": "person@example.com"}

        record = _failure_record("some.event", 2, item, [error], outcome="logged")

        assert record["event"] == "some.event"
        assert record["item_index"] == 2
        assert record["request_id"] == "REQ-1"
        assert record["item"] == item
        assert record["errors"] == [error.model_dump(mode="json")]
        assert record["outcome"] == "logged"

    def it_returns_none_for_request_id_when_the_item_is_not_a_dict(self) -> None:
        record = _failure_record("some.event", 0, "not-a-dict", [])

        assert record["request_id"] is None
