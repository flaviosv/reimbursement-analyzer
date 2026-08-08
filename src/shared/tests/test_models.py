from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from shared.models import AttemptError, ReimbursementEnvelope, RequestEnvelope


def _entry(attempt: int) -> AttemptError:
    return AttemptError(
        attempt=attempt,
        occurred_at=datetime(2026, 4, 10, 9, 15, 0, tzinfo=UTC),
        stage="db-insert",
        error_type="RuntimeError",
        message="boom",
    )


class DescribeAttemptError:
    def it_records_the_attempt_number_timestamp_stage_type_and_message(self) -> None:
        occurred_at = datetime(2026, 4, 10, 9, 15, 0, tzinfo=UTC)

        entry = AttemptError(
            attempt=2,
            occurred_at=occurred_at,
            stage="publish",
            error_type="PublishFailed",
            message="broker unreachable",
        )

        assert entry.attempt == 2
        assert entry.occurred_at == occurred_at
        assert entry.stage == "publish"
        assert entry.error_type == "PublishFailed"
        assert entry.message == "broker unreachable"

    def it_rejects_a_stage_outside_the_named_ones(self) -> None:
        with pytest.raises(ValidationError):
            AttemptError(
                attempt=1,
                occurred_at=datetime.now(UTC),
                stage="something-else",
                error_type="RuntimeError",
                message="boom",
            )

    def it_rejects_a_naive_occurred_at(self) -> None:
        with pytest.raises(ValidationError):
            AttemptError(
                attempt=1,
                occurred_at=datetime(2026, 4, 10, 9, 15, 0),
                stage="publish",
                error_type="RuntimeError",
                message="boom",
            )


class DescribeAttemptErrorNext:
    def it_numbers_the_entry_one_past_the_existing_history(self) -> None:
        history = [_entry(1), _entry(2)]

        entry = AttemptError.next(history, "publish", RuntimeError("boom"))

        assert entry.attempt == 3

    def it_stamps_an_aware_utc_timestamp(self) -> None:
        entry = AttemptError.next([], "db-insert", RuntimeError("boom"))

        assert entry.occurred_at.tzinfo is not None
        assert entry.occurred_at.utcoffset() == timedelta(0)

    def it_takes_the_stage_the_error_type_and_the_message_from_the_exception(self) -> None:
        entry = AttemptError.next([], "db-insert", ValueError("bad column"))

        assert entry.stage == "db-insert"
        assert entry.error_type == "ValueError"
        assert entry.message == "bad column"

    def it_appends_rather_than_replaces_across_successive_calls(self) -> None:
        history: list[AttemptError] = []

        for stage in ("db-insert", "publish", "db-insert"):
            history.append(AttemptError.next(history, stage, RuntimeError("boom")))

        assert [entry.attempt for entry in history] == [1, 2, 3]
        assert [entry.stage for entry in history] == ["db-insert", "publish", "db-insert"]

    def it_accepts_the_agent_resolve_stage(self) -> None:
        entry = AttemptError.next([], "resolve", RuntimeError("boom"))

        assert entry.stage == "resolve"


class DescribeRequestEnvelopeErrors:
    def it_defaults_to_an_empty_history(self) -> None:
        envelope = RequestEnvelope(retry=0, published_at=datetime.now(UTC), payload=[])

        assert envelope.errors == []

    def it_parses_a_message_with_no_errors_key_as_an_empty_history(self) -> None:
        raw = b'{"retry":0,"published_at":"2026-04-10T09:15:00+00:00","payload":[]}'

        envelope = RequestEnvelope.model_validate_json(raw)

        assert envelope.errors == []

    def it_parses_a_carried_history_into_attempt_errors(self) -> None:
        raw = (
            b'{"retry":1,"published_at":"2026-04-10T09:15:00+00:00","errors":['
            b'{"attempt":1,"occurred_at":"2026-04-10T09:14:00+00:00","stage":"publish",'
            b'"error_type":"PublishFailed","message":"broker unreachable"}],"payload":[]}'
        )

        envelope = RequestEnvelope.model_validate_json(raw)

        assert len(envelope.errors) == 1
        assert envelope.errors[0].attempt == 1
        assert envelope.errors[0].stage == "publish"
        assert envelope.errors[0].error_type == "PublishFailed"
        assert envelope.errors[0].message == "broker unreachable"


class DescribeReimbursementEnvelope:
    def it_carries_the_row_uuid_retry_and_published_at(self) -> None:
        uuid = uuid4()
        published_at = datetime(2026, 4, 10, 9, 15, 0, tzinfo=UTC)

        envelope = ReimbursementEnvelope(uuid=uuid, retry=0, published_at=published_at)

        assert envelope.uuid == uuid
        assert isinstance(envelope.uuid, UUID)
        assert envelope.retry == 0
        assert envelope.published_at == published_at

    def it_carries_no_payload_field(self) -> None:
        assert "payload" not in ReimbursementEnvelope.model_fields

    def it_defaults_to_an_empty_history(self) -> None:
        envelope = ReimbursementEnvelope(uuid=uuid4(), retry=0, published_at=datetime.now(UTC))

        assert envelope.errors == []
