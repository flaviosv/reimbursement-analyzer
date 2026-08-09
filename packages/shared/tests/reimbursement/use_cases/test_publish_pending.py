import json
import logging
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
import pytest
from fakes import FakeProducer
from shared.testing import valid_reimbursement_item
from shared.config import REIMBURSEMENT_TOPIC, load_config
from shared.errors import PublishFailed
from shared.models import AttemptError
from shared.reimbursement.use_cases.publish_pending import (
    COMPENSATING_DELETE_EVENT,
    COMPENSATING_DELETE_FAILED_EVENT,
    COMPENSATING_DELETE_NOOP_EVENT,
    publish_pending,
)
import shared.reimbursement.use_cases.publish_pending as publish_pending_module

pytestmark = pytest.mark.anyio

_FAILURE_LOG_CONFIG = load_config().failure_log


def _error(attempt: int) -> AttemptError:
    return AttemptError(
        attempt=attempt,
        occurred_at=datetime(2026, 4, 10, 9, attempt, 0, tzinfo=UTC),
        stage="db-insert",
        error_type="PostgresConnectionError",
        message="connection reset by peer",
    )


def _info_events(caplog: pytest.LogCaptureFixture) -> list[dict]:
    return [
        json.loads(record.message)
        for record in caplog.records
        if record.name == publish_pending_module.__name__ and record.levelno == logging.INFO
    ]


def _failure_records(caplog: pytest.LogCaptureFixture) -> list[dict]:
    return [
        json.loads(record.message)
        for record in caplog.records
        if record.name == _FAILURE_LOG_CONFIG.logger_name and record.levelno == logging.CRITICAL
    ]


class DescribePublishPending:
    async def it_stores_the_item_as_a_pending_row(self, db: asyncpg.Connection) -> None:
        item = valid_reimbursement_item("REQ-PUBLISH-PENDING")
        producer = FakeProducer()

        await publish_pending(
            db,
            producer,
            item,
            [],
            publish_timeout_seconds=5.0,
            failure_log_config=_FAILURE_LOG_CONFIG,
            retry=0,
        )

        row = await db.fetchrow(
            "SELECT status, request_id FROM reimbursement WHERE request_id = $1",
            "REQ-PUBLISH-PENDING",
        )
        assert row["status"] == "pending"

    async def it_publishes_the_stored_rows_uuid(self, db: asyncpg.Connection) -> None:
        item = valid_reimbursement_item("REQ-PUBLISH-UUID")
        producer = FakeProducer()

        await publish_pending(
            db,
            producer,
            item,
            [],
            publish_timeout_seconds=5.0,
            failure_log_config=_FAILURE_LOG_CONFIG,
            retry=0,
        )

        row = await db.fetchrow(
            "SELECT uuid FROM reimbursement WHERE request_id = $1", "REQ-PUBLISH-UUID"
        )
        published = producer.messages(REIMBURSEMENT_TOPIC)[0]
        assert UUID(published["uuid"]) == row["uuid"]

    async def it_forwards_the_error_history_onto_the_published_envelope(
        self, db: asyncpg.Connection
    ) -> None:
        # The one construction site of ReimbursementEnvelope previously never
        # forwarded this field at all — an item that failed then succeeded
        # handed the Agent an empty history at exactly the handoff this
        # field exists to make informative.
        item = valid_reimbursement_item("REQ-PUBLISH-HISTORY")
        producer = FakeProducer()
        history = [_error(1), _error(2)]

        await publish_pending(
            db,
            producer,
            item,
            history,
            publish_timeout_seconds=5.0,
            failure_log_config=_FAILURE_LOG_CONFIG,
            retry=0,
        )

        published = producer.messages(REIMBURSEMENT_TOPIC)[0]
        assert len(published["errors"]) == 2
        assert published["errors"][0]["message"] == "connection reset by peer"


class DescribeTheCompensatingDelete:
    async def it_deletes_the_row_when_the_publish_fails(self, db: asyncpg.Connection) -> None:
        item = valid_reimbursement_item("REQ-COMPENSATE")
        producer = FakeProducer(errors={REIMBURSEMENT_TOPIC: RuntimeError("broker unreachable")})

        with pytest.raises(PublishFailed):
            await publish_pending(
                db,
                producer,
                item,
                [],
                publish_timeout_seconds=5.0,
                failure_log_config=_FAILURE_LOG_CONFIG,
                retry=1,
            )

        row = await db.fetchrow(
            "SELECT * FROM reimbursement WHERE request_id = $1", "REQ-COMPENSATE"
        )
        assert row is None

    async def it_logs_the_compensating_delete_with_uuid_request_id_retry_and_error_type(
        self, db: asyncpg.Connection, caplog: pytest.LogCaptureFixture
    ) -> None:
        item = valid_reimbursement_item("REQ-DELETE-LOG")
        producer = FakeProducer(errors={REIMBURSEMENT_TOPIC: RuntimeError("broker unreachable")})

        with caplog.at_level(logging.INFO, logger=publish_pending_module.__name__):
            with pytest.raises(PublishFailed):
                await publish_pending(
                    db,
                    producer,
                    item,
                    [],
                    publish_timeout_seconds=5.0,
                    failure_log_config=_FAILURE_LOG_CONFIG,
                    retry=3,
                )

        published = producer.messages(REIMBURSEMENT_TOPIC)[0]
        events = _info_events(caplog)
        assert len(events) == 1
        assert events[0] == {
            "event": COMPENSATING_DELETE_EVENT,
            "uuid": published["uuid"],
            "request_id": "REQ-DELETE-LOG",
            "retry": 3,
            "publish_error_type": "PublishFailed",
        }

    async def it_logs_a_distinct_anomaly_and_still_raises_when_the_delete_affects_no_rows(
        self,
        db: asyncpg.Connection,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _no_op_delete(conn: asyncpg.Connection, uuid: UUID) -> bool:
            return False

        monkeypatch.setattr(
            "shared.reimbursement.use_cases.publish_pending.delete_pending", _no_op_delete
        )
        item = valid_reimbursement_item("REQ-NOOP")
        producer = FakeProducer(errors={REIMBURSEMENT_TOPIC: RuntimeError("broker unreachable")})

        with caplog.at_level(logging.INFO, logger=publish_pending_module.__name__):
            with pytest.raises(PublishFailed):
                await publish_pending(
                    db,
                    producer,
                    item,
                    [],
                    publish_timeout_seconds=5.0,
                    failure_log_config=_FAILURE_LOG_CONFIG,
                    retry=0,
                )

        events = _info_events(caplog)
        assert len(events) == 1
        assert events[0]["event"] == COMPENSATING_DELETE_NOOP_EVENT
        assert events[0]["request_id"] == "REQ-NOOP"

    async def it_writes_a_failure_log_record_and_still_raises_when_the_delete_itself_fails(
        self,
        db: asyncpg.Connection,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        delete_exc = asyncpg.PostgresConnectionError("connection reset")

        async def _raising_delete(conn: asyncpg.Connection, uuid: UUID) -> bool:
            raise delete_exc

        monkeypatch.setattr(
            "shared.reimbursement.use_cases.publish_pending.delete_pending", _raising_delete
        )
        item = valid_reimbursement_item("REQ-DELETE-FAILS")
        producer = FakeProducer(errors={REIMBURSEMENT_TOPIC: RuntimeError("broker unreachable")})

        with caplog.at_level(logging.CRITICAL, logger=_FAILURE_LOG_CONFIG.logger_name):
            with pytest.raises(PublishFailed):
                await publish_pending(
                    db,
                    producer,
                    item,
                    [],
                    publish_timeout_seconds=5.0,
                    failure_log_config=_FAILURE_LOG_CONFIG,
                    retry=2,
                )

        records = _failure_records(caplog)
        assert len(records) == 1
        record = records[0]
        assert record["event"] == COMPENSATING_DELETE_FAILED_EVENT
        assert record["request_id"] == "REQ-DELETE-FAILS"
        assert record["item"] == item
        assert record["retry"] == 2
        assert record["publish_error_type"] == "PublishFailed"
        assert record["delete_error_type"] == "PostgresConnectionError"
        assert "connection reset" in record["delete_error"]
