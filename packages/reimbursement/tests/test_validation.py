import logging
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from agent_fakes import FakePool, FakeProducer
from reimbursement.agent import agent
from reimbursement.config import load_agent_config
from reimbursement.models import Reimbursement
from reimbursement.validation import Dependencies, MessageOutcome, handle_message
from shared.config import REIMBURSEMENT_TOPIC, load_config
from shared.models import AttemptError, ReimbursementEnvelope

pytestmark = pytest.mark.anyio


async def _stub_decide(reimbursement: Reimbursement, conn: object) -> dict[str, object]:
    return {"status": "auto-approved", "decision_reason": "stub", "persisted": True}


@pytest.fixture(autouse=True)
def _fake_agent_decide(monkeypatch: pytest.MonkeyPatch) -> None:
    # A default stub so every test that reaches the RESOLVED path's
    # agent.decide() call (T14) never builds a real graph or calls Ollama.
    # DescribeDecideIntegration's own cases below override this per test.
    monkeypatch.setattr(agent, "decide", _stub_decide)


def _envelope(**overrides: object) -> ReimbursementEnvelope:
    defaults: dict[str, object] = dict(
        uuid=uuid4(), retry=0, published_at=datetime.now(UTC), errors=[]
    )
    defaults.update(overrides)
    return ReimbursementEnvelope(**defaults)  # type: ignore[arg-type]


def _deps(pool: FakePool | None = None, producer: FakeProducer | None = None) -> Dependencies:
    return Dependencies(
        config=load_config(),
        agent=load_agent_config(),
        pool=pool or FakePool(),
        producer=producer or FakeProducer(),
    )


def _error(attempt: int) -> AttemptError:
    return AttemptError(
        attempt=attempt,
        occurred_at=datetime(2026, 4, 10, 9, attempt, 0, tzinfo=UTC),
        stage="resolve",
        error_type="RuntimeError",
        message="db unreachable",
    )


def _row(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "status": "pending",
        "updated_at": datetime(2026, 4, 10, 9, 0, 0, tzinfo=UTC),
        # JSON text, matching Reimbursement.from_record's json.loads decode
        # (T1) — only the RESOLVED path (_decide) ever reads this key.
        "original_payload": "{}",
    }
    defaults.update(overrides)
    return defaults


class DescribeHandleMessageParsing:
    async def it_writes_malformed_json_to_the_failure_log_and_returns_invalid(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        pool = FakePool()
        producer = FakeProducer()
        deps = _deps(pool=pool, producer=producer)

        with caplog.at_level(logging.CRITICAL, logger="reimbursementanalyzer.failures"):
            outcome = await handle_message(deps, b"not json")

        assert outcome == MessageOutcome.INVALID
        assert any("reimbursement.malformed_message" in record.message for record in caplog.records)
        assert pool.acquisitions == 0  # no DB attempt was made
        assert producer.produced == []  # AGT-20: no republish either

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
        producer = FakeProducer()
        deps = _deps(pool=pool, producer=producer)
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
        # AGT-17: on success, no further action — no republish.
        assert producer.produced == []

    async def it_does_not_escalate_at_exactly_the_retry_ceiling(self) -> None:
        # The boundary itself: SCOPE.md's rule is "retry > 3", so retry == 3
        # (MAX_RETRY) must still take the normal resolve path, not escalate.
        uuid = uuid4()
        row_updated_at = datetime(2026, 4, 10, 9, 0, 0, tzinfo=UTC)
        pool = FakePool(rows={uuid: _row(uuid=uuid, updated_at=row_updated_at)})
        deps = _deps(pool=pool)
        envelope = _envelope(uuid=uuid, retry=3, published_at=row_updated_at, errors=[_error(1)])

        outcome = await handle_message(deps, envelope.model_dump_json().encode())

        assert outcome == MessageOutcome.RESOLVED
        assert pool.rows[uuid]["status"] == "pending"  # never touched by escalation

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


class DescribeResolveByUuid:
    async def it_resolves_a_fresh_row_and_returns_resolved(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()
        row_updated_at = datetime(2026, 4, 10, 9, 0, 0, tzinfo=UTC)
        pool = FakePool(rows={uuid: _row(uuid=uuid, updated_at=row_updated_at)})
        deps = _deps(pool=pool)
        envelope = _envelope(uuid=uuid, retry=0, published_at=row_updated_at)

        with caplog.at_level(logging.INFO):
            outcome = await handle_message(deps, envelope.model_dump_json().encode())

        assert outcome == MessageOutcome.RESOLVED
        assert any(
            "reimbursement.resolved" in record.message and record.levelno == logging.INFO
            for record in caplog.records
        )

    async def it_treats_a_uuid_with_no_row_as_a_ghost_and_drops_it(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        pool = FakePool(rows={})
        producer = FakeProducer()
        deps = _deps(pool=pool, producer=producer)
        envelope = _envelope(uuid=uuid4(), retry=0)

        with caplog.at_level(logging.INFO):
            outcome = await handle_message(deps, envelope.model_dump_json().encode())

        assert outcome == MessageOutcome.GHOST
        # Informational, not ERROR — a ghost is an accepted, non-error
        # condition (R-001), not a failure.
        ghost_records = [r for r in caplog.records if "reimbursement.ghost_dropped" in r.message]
        assert ghost_records
        assert all(r.levelno < logging.ERROR for r in ghost_records)
        # AGT-03: no republish, no retry, no failure-log entry.
        assert producer.produced == []
        assert not any(r.levelno >= logging.CRITICAL for r in caplog.records)

    async def it_ignores_a_message_older_than_the_rows_last_update(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()
        row_updated_at = datetime(2026, 4, 10, 9, 30, 0, tzinfo=UTC)
        pool = FakePool(rows={uuid: _row(updated_at=row_updated_at)})
        deps = _deps(pool=pool)
        stale_published_at = datetime(2026, 4, 10, 9, 0, 0, tzinfo=UTC)
        envelope = _envelope(uuid=uuid, retry=0, published_at=stale_published_at)

        with caplog.at_level(logging.INFO):
            outcome = await handle_message(deps, envelope.model_dump_json().encode())

        assert outcome == MessageOutcome.STALE
        assert pool.rows[uuid]["status"] == "pending"  # unchanged

    async def it_treats_an_exactly_equal_timestamp_as_fresh_not_stale(self) -> None:
        uuid = uuid4()
        same_instant = datetime(2026, 4, 10, 9, 0, 0, tzinfo=UTC)
        pool = FakePool(rows={uuid: _row(uuid=uuid, updated_at=same_instant)})
        deps = _deps(pool=pool)
        envelope = _envelope(uuid=uuid, retry=0, published_at=same_instant)

        outcome = await handle_message(deps, envelope.model_dump_json().encode())

        assert outcome == MessageOutcome.RESOLVED


class DescribeResolveTransientFailureRequeue:
    async def it_republishes_with_incremented_retry_and_an_appended_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()
        pool = FakePool(get_errors={uuid: RuntimeError("connection reset")})
        producer = FakeProducer()
        deps = _deps(pool=pool, producer=producer)
        envelope = _envelope(uuid=uuid, retry=0, errors=[])

        with caplog.at_level(logging.ERROR):
            outcome = await handle_message(deps, envelope.model_dump_json().encode())

        assert outcome == MessageOutcome.REQUEUED
        [requeued] = producer.messages(REIMBURSEMENT_TOPIC)
        assert requeued["retry"] == 1
        assert len(requeued["errors"]) == 1
        assert requeued["errors"][0]["stage"] == "resolve"
        assert requeued["errors"][0]["error_type"] == "RuntimeError"
        assert any(
            f"uuid={uuid}" in record.message and record.levelno == logging.ERROR
            for record in caplog.records
        )

    async def it_sanitizes_the_stdout_log_and_omits_driver_detail(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()

        class _FakeDriverError(Exception):
            constraint_name = "reimbursement_pkey"

        exc = _FakeDriverError(
            "duplicate key value violates unique constraint "
            "DETAIL: Key (uuid)=(...) already exists submitted_by=person@example.com"
        )
        pool = FakePool(get_errors={uuid: exc})
        deps = _deps(pool=pool)
        envelope = _envelope(uuid=uuid, retry=0)

        with caplog.at_level(logging.ERROR):
            await handle_message(deps, envelope.model_dump_json().encode())

        error_lines = [r.message for r in caplog.records if r.levelno == logging.ERROR]
        assert error_lines
        assert not any("person@example.com" in line for line in error_lines)
        assert not any("DETAIL" in line for line in error_lines)

    async def it_carries_two_entries_in_order_on_a_second_failure(self) -> None:
        uuid = uuid4()
        pool = FakePool(get_errors={uuid: RuntimeError("still down")})
        producer = FakeProducer()
        deps = _deps(pool=pool, producer=producer)
        envelope = _envelope(uuid=uuid, retry=1, errors=[_error(1)])

        await handle_message(deps, envelope.model_dump_json().encode())

        [requeued] = producer.messages(REIMBURSEMENT_TOPIC)
        assert requeued["retry"] == 2
        assert len(requeued["errors"]) == 2
        assert requeued["errors"][0]["attempt"] == 1
        assert requeued["errors"][1]["attempt"] == 2

    async def it_writes_to_the_failure_log_when_the_requeue_itself_fails(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()
        pool = FakePool(get_errors={uuid: RuntimeError("connection reset")})
        producer = FakeProducer(errors={REIMBURSEMENT_TOPIC: Exception("broker unreachable")})
        deps = _deps(pool=pool, producer=producer)
        envelope = _envelope(uuid=uuid, retry=0)

        with caplog.at_level(logging.CRITICAL, logger="reimbursementanalyzer.failures"):
            outcome = await handle_message(deps, envelope.model_dump_json().encode())

        assert outcome == MessageOutcome.LOGGED
        assert any("reimbursement.resolve_failed" in record.message for record in caplog.records)


class DescribeDecideIntegration:
    """T14: `_resolve` invokes `agent.decide()` on the same connection the
    row was fetched with, and applies the R-011 interim floor — a `decide()`
    failure is caught, durably logged, and returns LOGGED, never
    propagates."""

    async def it_logs_the_decided_status_and_returns_resolved_on_success(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()
        row_updated_at = datetime(2026, 4, 10, 9, 0, 0, tzinfo=UTC)
        pool = FakePool(rows={uuid: _row(uuid=uuid, updated_at=row_updated_at)})
        deps = _deps(pool=pool)
        envelope = _envelope(uuid=uuid, retry=0, published_at=row_updated_at)

        async def _fake_decide(reimbursement: Reimbursement, conn: object) -> dict[str, object]:
            assert reimbursement.uuid == uuid
            return {"status": "auto-approved", "decision_reason": "value 150 <= 200", "persisted": True}

        monkeypatch.setattr(agent, "decide", _fake_decide)

        with caplog.at_level(logging.INFO):
            outcome = await handle_message(deps, envelope.model_dump_json().encode())

        assert outcome == MessageOutcome.RESOLVED
        assert any(
            "reimbursement.decided" in r.message and '"status": "auto-approved"' in r.message
            for r in caplog.records
        )

    async def it_logs_persisted_false_distinctly_on_the_ghost_write_case(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()
        row_updated_at = datetime(2026, 4, 10, 9, 0, 0, tzinfo=UTC)
        pool = FakePool(rows={uuid: _row(uuid=uuid, updated_at=row_updated_at)})
        deps = _deps(pool=pool)
        envelope = _envelope(uuid=uuid, retry=0, published_at=row_updated_at)

        async def _fake_decide(reimbursement: Reimbursement, conn: object) -> dict[str, object]:
            return {"status": "human-review", "decision_reason": "unresolved value", "persisted": False}

        monkeypatch.setattr(agent, "decide", _fake_decide)

        with caplog.at_level(logging.INFO):
            outcome = await handle_message(deps, envelope.model_dump_json().encode())

        # A ghost apply_decision write is a genuine RESOLVED outcome from
        # the message-handling perspective (the graph ran and decided) --
        # distinguished in the log, not by a different MessageOutcome.
        assert outcome == MessageOutcome.RESOLVED
        assert any(
            "reimbursement.decided" in r.message and '"persisted": false' in r.message
            for r in caplog.records
        )

    async def it_catches_a_decide_failure_writes_the_failure_log_and_returns_logged_without_propagating(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()
        row_updated_at = datetime(2026, 4, 10, 9, 0, 0, tzinfo=UTC)
        pool = FakePool(rows={uuid: _row(uuid=uuid, updated_at=row_updated_at)})
        deps = _deps(pool=pool)
        envelope = _envelope(uuid=uuid, retry=0, published_at=row_updated_at)

        async def _failing_decide(reimbursement: Reimbursement, conn: object) -> dict[str, object]:
            raise RuntimeError("ollama unreachable")

        monkeypatch.setattr(agent, "decide", _failing_decide)

        with caplog.at_level(logging.CRITICAL, logger="reimbursementanalyzer.failures"):
            # R-011 interim floor: this must not raise -- handle_message's
            # own "never raises" invariant (AGT-20) depends on it.
            outcome = await handle_message(deps, envelope.model_dump_json().encode())

        assert outcome == MessageOutcome.LOGGED
        assert any("reimbursement.decision_failed" in r.message for r in caplog.records)
        assert any("ollama unreachable" in r.message for r in caplog.records)
        # No retry, no auto-escalation: the row itself is never touched.
        assert pool.rows[uuid]["status"] == "pending"
