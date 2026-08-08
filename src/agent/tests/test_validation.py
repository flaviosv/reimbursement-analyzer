import logging
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from agent_fakes import FakePool, FakeProducer
from shared.config import load_config
from shared.models import AttemptError, ReimbursementEnvelope
from agent.validation import Dependencies, MessageOutcome, handle_message

pytestmark = pytest.mark.anyio


def _envelope(**overrides: object) -> ReimbursementEnvelope:
    defaults: dict[str, object] = dict(
        uuid=uuid4(), retry=0, published_at=datetime.now(UTC), errors=[]
    )
    defaults.update(overrides)
    return ReimbursementEnvelope(**defaults)  # type: ignore[arg-type]


def _deps(pool: FakePool | None = None, producer: FakeProducer | None = None) -> Dependencies:
    return Dependencies(
        config=load_config(), pool=pool or FakePool(), producer=producer or FakeProducer()
    )


def _error(attempt: int) -> AttemptError:
    return AttemptError(
        attempt=attempt,
        occurred_at=datetime(2026, 4, 10, 9, attempt, 0, tzinfo=UTC),
        stage="resolve",
        error_type="RuntimeError",
        message="db unreachable",
    )


class DescribeHandleMessageParsing:
    async def it_writes_malformed_json_to_the_failure_log_and_returns_invalid(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        pool = FakePool()
        deps = _deps(pool=pool)

        with caplog.at_level(logging.CRITICAL, logger="reimbursementanalyzer.failures"):
            outcome = await handle_message(deps, b"not json")

        assert outcome == MessageOutcome.INVALID
        assert any("reimbursement.malformed_message" in record.message for record in caplog.records)
        assert pool.acquisitions == 0  # no DB attempt was made

    async def it_writes_a_schema_mismatched_message_to_the_failure_log_and_returns_invalid(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        deps = _deps()
        raw = b'{"retry": "not-an-int"}'

        with caplog.at_level(logging.CRITICAL, logger="reimbursementanalyzer.failures"):
            outcome = await handle_message(deps, raw)

        assert outcome == MessageOutcome.INVALID
        assert any("reimbursement.malformed_message" in record.message for record in caplog.records)


class DescribeRetryCeilingEscalation:
    async def it_escalates_a_real_row_and_returns_escalated(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()
        pool = FakePool(rows={uuid: {"status": "pending"}})
        deps = _deps(pool=pool)
        envelope = _envelope(uuid=uuid, retry=4, errors=[_error(1), _error(2), _error(3), _error(4)])

        with caplog.at_level(logging.ERROR):
            outcome = await handle_message(deps, envelope.model_dump_json().encode())

        assert outcome == MessageOutcome.ESCALATED
        assert pool.rows[uuid]["status"] == "human-review"
        assert "db unreachable" in pool.updated[uuid]
        assert any(
            "reimbursement.escalated" in record.message and str(uuid) in record.message
            for record in caplog.records
        )

    async def it_writes_to_the_failure_log_when_the_uuid_is_a_ghost(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Compound case (AGT-18): retry>3 climbed via transient failures,
        # but the uuid never resolves to a row — nothing to escalate.
        pool = FakePool(rows={})
        deps = _deps(pool=pool)
        envelope = _envelope(uuid=uuid4(), retry=4, errors=[_error(1)])

        with caplog.at_level(logging.CRITICAL, logger="reimbursementanalyzer.failures"):
            outcome = await handle_message(deps, envelope.model_dump_json().encode())

        assert outcome == MessageOutcome.LOGGED
        assert any("reimbursement.escalation_failed" in record.message for record in caplog.records)

    async def it_writes_to_the_failure_log_when_escalation_hits_a_genuine_db_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()
        pool = FakePool(
            rows={uuid: {"status": "pending"}}, update_errors={uuid: RuntimeError("connection reset")}
        )
        deps = _deps(pool=pool)
        envelope = _envelope(uuid=uuid, retry=4, errors=[_error(1)])

        with caplog.at_level(logging.CRITICAL, logger="reimbursementanalyzer.failures"):
            outcome = await handle_message(deps, envelope.model_dump_json().encode())

        assert outcome == MessageOutcome.LOGGED
        assert any("reimbursement.escalation_failed" in record.message for record in caplog.records)
