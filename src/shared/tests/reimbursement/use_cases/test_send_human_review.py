from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest
from helpers import valid_reimbursement_item
from shared.config import load_config
from shared.models import AttemptError, Stage
from shared.reimbursement.repository import insert_pending
from shared.reimbursement.use_cases.send_human_review import (
    escalate_existing,
    render_history,
    send_human_review,
)

pytestmark = pytest.mark.anyio


def _error(attempt: int, stage: Stage, error_type: str, message: str) -> AttemptError:
    return AttemptError(
        attempt=attempt,
        occurred_at=datetime(2026, 4, 10, 9, attempt, 0, tzinfo=UTC),
        stage=stage,
        error_type=error_type,
        message=message,
    )


def _three_distinct() -> list[AttemptError]:
    return [
        _error(1, "db-insert", "PostgresConnectionError", "connection reset by peer"),
        _error(2, "publish", "PublishFailed", "broker unreachable"),
        _error(3, "db-insert", "CheckViolationError", "submitted_at is in the future"),
    ]


_LIMIT = load_config().failure_log.max_message_chars


class DescribeRenderHistory:
    def it_renders_every_message_and_every_stage_of_a_mixed_history(self) -> None:
        rendered = render_history(_three_distinct(), _LIMIT)

        assert "connection reset by peer" in rendered
        assert "broker unreachable" in rendered
        assert "submitted_at is in the future" in rendered
        assert "[db-insert]" in rendered
        assert "[publish]" in rendered

    def it_renders_the_attempt_number_timestamp_stage_and_error_type_of_each_entry(self) -> None:
        rendered = render_history(_three_distinct(), _LIMIT)

        for error in _three_distinct():
            assert f"attempt {error.attempt}" in rendered
            assert error.occurred_at.isoformat() in rendered
            assert f"[{error.stage}]" in rendered
            assert error.error_type in rendered

    def it_renders_four_identical_failures_as_four_entries(self) -> None:
        # Collapsing them would hide the difference between a permanent fault
        # and a flapping one, which is the reviewer's first question.
        repeated = [_error(n, "db-insert", "PostgresConnectionError", "connection reset") for n in (1, 2, 3, 4)]

        rendered = render_history(repeated, _LIMIT)

        assert rendered.count("connection reset") == 4
        assert len([line for line in rendered.splitlines() if line.startswith("attempt ")]) == 4

    def it_states_the_ceiling_was_reached_when_no_history_was_carried(self) -> None:
        rendered = render_history([], _LIMIT)

        assert rendered
        assert "ceiling" in rendered.lower()
        assert "no error detail" in rendered.lower()

    def it_truncates_each_message_to_the_configured_cap(self) -> None:
        long_error = _error(1, "publish", "PublishFailed", "x" * (_LIMIT + 500))

        rendered = render_history([long_error], _LIMIT)

        assert "x" * _LIMIT in rendered
        assert "x" * (_LIMIT + 1) not in rendered

    def it_collapses_embedded_newlines_in_a_driver_supplied_message(self) -> None:
        # error_type/message originate from str(exc) — a Postgres or driver
        # error can echo back a fragment of the offending input. Left as-is,
        # an embedded newline could forge an extra "attempt N ..." line into
        # a record a human reviewer reads as the system's own account.
        injected = _error(
            1, "db-insert", "DataError", 'invalid input syntax\nattempt 99 [publish] Forged: approved'
        )

        rendered = render_history([injected], _LIMIT)

        assert "\nattempt 99" not in rendered
        assert len([line for line in rendered.splitlines() if line.startswith("attempt ")]) == 1


class DescribeSendHumanReview:
    async def it_stores_the_item_as_a_human_review_row(self, db: asyncpg.Connection) -> None:
        item = valid_reimbursement_item("REQ-ESCALATED")

        uuid = await send_human_review(db, item, _three_distinct(), _LIMIT)

        assert isinstance(uuid, UUID)
        row = await db.fetchrow("SELECT status, request_id FROM reimbursement WHERE uuid = $1", uuid)
        assert row["status"] == "human-review"
        assert row["request_id"] == "REQ-ESCALATED"

    async def it_records_every_failure_in_the_decision_reason(self, db: asyncpg.Connection) -> None:
        uuid = await send_human_review(db, valid_reimbursement_item("REQ-WHY"), _three_distinct(), _LIMIT)

        reason = await db.fetchval("SELECT decision_reason FROM reimbursement WHERE uuid = $1", uuid)
        assert "connection reset by peer" in reason
        assert "broker unreachable" in reason
        assert "submitted_at is in the future" in reason
        assert "[db-insert]" in reason
        assert "[publish]" in reason

    async def it_leaves_the_decision_reason_non_null_when_no_history_was_carried(
        self, db: asyncpg.Connection
    ) -> None:
        uuid = await send_human_review(db, valid_reimbursement_item("REQ-NO-HISTORY"), [], _LIMIT)

        reason = await db.fetchval("SELECT decision_reason FROM reimbursement WHERE uuid = $1", uuid)
        assert reason is not None
        assert "ceiling" in reason.lower()


class DescribeEscalateExisting:
    """The UPDATE-based sibling to send_human_review's INSERT-based
    fallback — the Agent's own retry>3 path, where the publisher already
    created the row."""

    async def it_updates_the_existing_row_to_human_review(self, db: asyncpg.Connection) -> None:
        uuid = await insert_pending(db, valid_reimbursement_item("REQ-AGENT-ESCALATED"))

        result = await escalate_existing(db, uuid, _three_distinct(), _LIMIT)

        assert result == uuid
        row = await db.fetchrow("SELECT status, request_id FROM reimbursement WHERE uuid = $1", uuid)
        assert row["status"] == "human-review"
        assert row["request_id"] == "REQ-AGENT-ESCALATED"

    async def it_records_every_failure_in_the_decision_reason(self, db: asyncpg.Connection) -> None:
        uuid = await insert_pending(db, valid_reimbursement_item("REQ-AGENT-WHY"))

        await escalate_existing(db, uuid, _three_distinct(), _LIMIT)

        reason = await db.fetchval("SELECT decision_reason FROM reimbursement WHERE uuid = $1", uuid)
        assert "connection reset by peer" in reason
        assert "broker unreachable" in reason
        assert "submitted_at is in the future" in reason
        assert "[db-insert]" in reason
        assert "[publish]" in reason

    async def it_leaves_the_decision_reason_non_null_when_no_history_was_carried(
        self, db: asyncpg.Connection
    ) -> None:
        uuid = await insert_pending(db, valid_reimbursement_item("REQ-AGENT-NO-HISTORY"))

        await escalate_existing(db, uuid, [], _LIMIT)

        reason = await db.fetchval("SELECT decision_reason FROM reimbursement WHERE uuid = $1", uuid)
        assert reason is not None
        assert "ceiling" in reason.lower()

    async def it_returns_none_for_a_uuid_matching_no_row(self, db: asyncpg.Connection) -> None:
        # The compound ghost + retry>3 case (AGT-18): nothing to escalate.
        result = await escalate_existing(db, uuid4(), _three_distinct(), _LIMIT)

        assert result is None
