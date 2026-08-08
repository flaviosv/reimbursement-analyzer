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

    def it_rejects_a_stage_outside_the_two_named_ones(self) -> None:
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


class DescribeAttemptErrorFromException:
    def it_records_the_attempt_number_it_is_given(self) -> None:
        entry = AttemptError.from_exception(3, "publish", RuntimeError("boom"))

        assert entry.attempt == 3

    def it_stamps_an_aware_utc_timestamp_by_default(self) -> None:
        entry = AttemptError.from_exception(1, "db-insert", RuntimeError("boom"))

        assert entry.occurred_at.tzinfo is not None
        assert entry.occurred_at.utcoffset() == timedelta(0)

    def it_accepts_an_explicit_occurred_at_instead_of_reading_the_clock(self) -> None:
        # A pure wire-contract module reading the wall clock internally made
        # every caller's result time-dependent (A14); this is the escape
        # hatch — production omits it, a test that cares can pin it.
        stamp = datetime(2026, 4, 10, 9, 15, 0, tzinfo=UTC)

        entry = AttemptError.from_exception(1, "db-insert", RuntimeError("boom"), occurred_at=stamp)

        assert entry.occurred_at == stamp

    def it_takes_the_stage_the_error_type_and_the_message_from_the_exception(self) -> None:
        entry = AttemptError.from_exception(1, "db-insert", ValueError("bad column"))

        assert entry.stage == "db-insert"
        assert entry.error_type == "ValueError"
        assert entry.message == "bad column"

    def it_appends_rather_than_replaces_across_successive_calls(self) -> None:
        history: list[AttemptError] = []

        for stage in ("db-insert", "publish", "db-insert"):
            history.append(AttemptError.from_exception(len(history) + 1, stage, RuntimeError("boom")))

        assert [entry.attempt for entry in history] == [1, 2, 3]
        assert [entry.stage for entry in history] == ["db-insert", "publish", "db-insert"]


class DescribeRequestEnvelopeBounds:
    def it_rejects_a_negative_retry(self) -> None:
        # retry is envelope-level protocol state, not attacker-facing input
        # (the public API always starts a fresh envelope at retry=0) — but a
        # directly-produced or hand-crafted message on the Request topic
        # should not be able to send this negative (S3).
        with pytest.raises(ValidationError):
            RequestEnvelope(retry=-1, published_at=datetime.now(UTC), payload=[])

    def it_rejects_a_payload_longer_than_the_shared_batch_ceiling(self) -> None:
        # The API enforces MAX_BATCH_ITEMS at ingress, but the publisher
        # cannot assume every producer onto this topic is the API — its own
        # requeue path is one, a directly-produced message is another — so
        # it re-asserts the same cap at its own trust boundary (S4/P9).
        from shared.config import MAX_BATCH_ITEMS

        with pytest.raises(ValidationError):
            RequestEnvelope(
                retry=0,
                published_at=datetime.now(UTC),
                payload=[{"request_id": f"REQ-{n}"} for n in range(MAX_BATCH_ITEMS + 1)],
            )


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
