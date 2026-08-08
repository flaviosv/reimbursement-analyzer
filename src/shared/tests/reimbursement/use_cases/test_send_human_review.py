from datetime import UTC, datetime
from uuid import UUID

import asyncpg
import pytest
from helpers import valid_reimbursement_item
from shared.config import load_config
from shared.models import AttemptError, Stage
from shared.reimbursement.use_cases.send_human_review import render_history, send_human_review

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


class DescribeRenderHistory:
    def it_renders_every_message_and_every_stage_of_a_mixed_history(self) -> None:
        rendered = render_history(_three_distinct())

        assert "connection reset by peer" in rendered
        assert "broker unreachable" in rendered
        assert "submitted_at is in the future" in rendered
        assert "[db-insert]" in rendered
        assert "[publish]" in rendered

    def it_renders_the_attempt_number_timestamp_stage_and_error_type_of_each_entry(self) -> None:
        rendered = render_history(_three_distinct())

        for error in _three_distinct():
            assert f"attempt {error.attempt}" in rendered
            assert error.occurred_at.isoformat() in rendered
            assert f"[{error.stage}]" in rendered
            assert error.error_type in rendered

    def it_renders_four_identical_failures_as_four_entries(self) -> None:
        # Collapsing them would hide the difference between a permanent fault
        # and a flapping one, which is the reviewer's first question.
        repeated = [_error(n, "db-insert", "PostgresConnectionError", "connection reset") for n in (1, 2, 3, 4)]

        rendered = render_history(repeated)

        assert rendered.count("connection reset") == 4
        assert len([line for line in rendered.splitlines() if line.startswith("attempt ")]) == 4

    def it_states_the_ceiling_was_reached_when_no_history_was_carried(self) -> None:
        rendered = render_history([])

        assert rendered
        assert "ceiling" in rendered.lower()
        assert "no error detail" in rendered.lower()

    def it_truncates_each_message_to_the_configured_cap(self) -> None:
        limit = load_config().failure_log.max_message_chars
        long_error = _error(1, "publish", "PublishFailed", "x" * (limit + 500))

        rendered = render_history([long_error])

        assert "x" * limit in rendered
        assert "x" * (limit + 1) not in rendered


class DescribeSendHumanReview:
    async def it_stores_the_item_as_a_human_review_row(self, db: asyncpg.Connection) -> None:
        item = valid_reimbursement_item("REQ-ESCALATED")

        uuid = await send_human_review(db, item, _three_distinct())

        assert isinstance(uuid, UUID)
        row = await db.fetchrow("SELECT status, request_id FROM reimbursement WHERE uuid = $1", uuid)
        assert row["status"] == "human-review"
        assert row["request_id"] == "REQ-ESCALATED"

    async def it_records_every_failure_in_the_decision_reason(self, db: asyncpg.Connection) -> None:
        uuid = await send_human_review(db, valid_reimbursement_item("REQ-WHY"), _three_distinct())

        reason = await db.fetchval("SELECT decision_reason FROM reimbursement WHERE uuid = $1", uuid)
        assert "connection reset by peer" in reason
        assert "broker unreachable" in reason
        assert "submitted_at is in the future" in reason
        assert "[db-insert]" in reason
        assert "[publish]" in reason

    async def it_leaves_the_decision_reason_non_null_when_no_history_was_carried(
        self, db: asyncpg.Connection
    ) -> None:
        uuid = await send_human_review(db, valid_reimbursement_item("REQ-NO-HISTORY"), [])

        reason = await db.fetchval("SELECT decision_reason FROM reimbursement WHERE uuid = $1", uuid)
        assert reason is not None
        assert "ceiling" in reason.lower()
