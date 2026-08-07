from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
import pytest
from psycopg import errors


def insert(conn: psycopg.Connection, **values: Any) -> tuple[Any, ...]:
    values.setdefault("request_id", "REQ-0001")
    values.setdefault("original_payload", "{}")
    columns = ", ".join(values)
    placeholders = ", ".join(f"%({name})s" for name in values)
    row = conn.execute(
        f"INSERT INTO reimbursement ({columns}) VALUES ({placeholders})"
        " RETURNING uuid, status, updated_at",
        values,
    ).fetchone()
    assert row is not None
    return row


def stored(conn: psycopg.Connection, expression: str) -> Any:
    row = conn.execute(
        f"SELECT {expression} FROM reimbursement WHERE request_id = 'REQ-0001'"
    ).fetchone()
    assert row is not None
    return row[0]


def index_definition(conn: psycopg.Connection, name: str) -> str:
    # The definition, not the name: an index keeps passing a name check no
    # matter which columns it actually covers.
    row = conn.execute(
        "SELECT indexdef FROM pg_indexes WHERE indexname = %s", (name,)
    ).fetchone()
    assert row is not None
    return row[0]


class DescribeDefaults:
    def it_accepts_the_publisher_shaped_insert(self, conn: psycopg.Connection) -> None:
        uuid, status, _ = insert(conn)
        assert uuid is not None
        assert status == "pending"

    def it_generates_time_ordered_uuids(self, conn: psycopg.Connection) -> None:
        # Ten rows, not two: a random v4 would sort correctly half the time on
        # a pair, so that version of this test decided nothing.
        uuids = [insert(conn, request_id=f"REQ-{n}")[0] for n in range(10)]
        assert uuids == sorted(uuids)

    def it_requires_a_request_id(self, conn: psycopg.Connection) -> None:
        with pytest.raises(errors.NotNullViolation):
            conn.execute("INSERT INTO reimbursement (original_payload) VALUES ('{}')")

    def it_requires_the_original_payload(self, conn: psycopg.Connection) -> None:
        with pytest.raises(errors.NotNullViolation):
            conn.execute("INSERT INTO reimbursement (request_id) VALUES ('REQ-X')")


class DescribeStatus:
    @pytest.mark.parametrize(
        "status",
        [
            "pending",
            "auto-approved",
            "auto-rejected",
            "human-review",
            "human-approved",
            "human-rejected",
        ],
    )
    def it_accepts_every_specified_status(
        self, conn: psycopg.Connection, status: str
    ) -> None:
        _, stored_status, _ = insert(conn, status=status)
        assert stored_status == status

    @pytest.mark.parametrize("status", ["bogus", "approved", "AUTO-APPROVED", ""])
    def it_rejects_anything_else(self, conn: psycopg.Connection, status: str) -> None:
        with pytest.raises(errors.CheckViolation) as exc:
            insert(conn, status=status)
        assert exc.value.diag.constraint_name == "reimbursement_status_check"


class DescribeUniqueness:
    def it_rejects_a_repeated_request_from_one_submitter(
        self, conn: psycopg.Connection
    ) -> None:
        insert(conn, request_id="REQ-DUP", submitted_by="ana@company.com")
        with pytest.raises(errors.UniqueViolation) as exc:
            insert(conn, request_id="REQ-DUP", submitted_by="ana@company.com")
        assert exc.value.diag.constraint_name == "reimbursement_request_submitter_key"

    def it_rejects_a_repeated_request_with_no_submitter(
        self, conn: psycopg.Connection
    ) -> None:
        # Standard NULL semantics would allow unlimited duplicates here.
        insert(conn, request_id="REQ-NULL")
        with pytest.raises(errors.UniqueViolation):
            insert(conn, request_id="REQ-NULL")

    @pytest.mark.parametrize(
        "variant", ["ANA@COMPANY.COM", "Ana@Company.com", "aNa@company.COM"]
    )
    def it_rejects_a_case_varied_submitter(
        self, conn: psycopg.Connection, variant: str
    ) -> None:
        # Email local-parts are case-insensitive at every real provider, so a
        # byte-exact key would pay one person three times for one request.
        insert(conn, request_id="REQ-CASE", submitted_by="ana@company.com")
        with pytest.raises(errors.UniqueViolation) as exc:
            insert(conn, request_id="REQ-CASE", submitted_by=variant)
        assert exc.value.diag.constraint_name == "reimbursement_request_submitter_key"

    def it_allows_one_request_id_from_different_submitters(
        self, conn: psycopg.Connection
    ) -> None:
        insert(conn, request_id="REQ-SHARED", submitted_by="ana@company.com")
        insert(conn, request_id="REQ-SHARED", submitted_by="carlos@company.com")

    def it_is_keyed_on_the_lowercased_submitter(
        self, conn: psycopg.Connection
    ) -> None:
        assert index_definition(conn, "reimbursement_request_submitter_key") == (
            "CREATE UNIQUE INDEX reimbursement_request_submitter_key ON"
            " public.reimbursement USING btree (request_id, lower(submitted_by))"
            " NULLS NOT DISTINCT"
        )


class DescribeSubmitter:
    @pytest.mark.parametrize(
        "email",
        ["not-an-email", "no@domain", "spaces in@mail.com", "@company.com", "a@b."],
    )
    def it_rejects_a_malformed_address(
        self, conn: psycopg.Connection, email: str
    ) -> None:
        with pytest.raises(errors.CheckViolation) as exc:
            insert(conn, submitted_by=email)
        assert exc.value.diag.constraint_name == "reimbursement_submitted_by_check"

    def it_rejects_an_overlong_address(self, conn: psycopg.Connection) -> None:
        with pytest.raises(errors.CheckViolation):
            insert(conn, submitted_by="a" * 250 + "@company.com")

    def it_accepts_no_address_at_all(self, conn: psycopg.Connection) -> None:
        insert(conn, submitted_by=None)


class DescribeCurrency:
    @pytest.mark.parametrize("currency", ["brl", "BR", "BRLL", "1X!", " BR"])
    def it_rejects_anything_but_an_iso_code(
        self, conn: psycopg.Connection, currency: str
    ) -> None:
        with pytest.raises(errors.CheckViolation) as exc:
            insert(conn, currency=currency)
        assert exc.value.diag.constraint_name == "reimbursement_currency_check"

    def it_accepts_an_iso_code(self, conn: psycopg.Connection) -> None:
        insert(conn, currency="BRL")


class DescribeMoney:
    def it_keeps_two_decimal_places(self, conn: psycopg.Connection) -> None:
        insert(conn, receipts_value="93.50")
        assert str(stored(conn, "receipts_value")) == "93.50"

    def it_holds_the_full_declared_precision(self, conn: psycopg.Connection) -> None:
        # 12 integer digits + 2 decimal: NUMERIC(14,2) exactly. A narrower
        # column passes the 93.50 round-trip while silently capping payouts.
        insert(conn, receipts_value="999999999999.99")
        assert str(stored(conn, "receipts_value")) == "999999999999.99"

    def it_rejects_a_negative_amount(self, conn: psycopg.Connection) -> None:
        # A negative amount inverts the direction the money moves.
        with pytest.raises(errors.CheckViolation) as exc:
            insert(conn, receipts_value="-1.00")
        assert exc.value.diag.constraint_name == "reimbursement_receipts_value_check"

    def it_accepts_zero(self, conn: psycopg.Connection) -> None:
        insert(conn, receipts_value="0.00")

    def it_still_stores_an_implausibly_large_claim(
        self, conn: psycopg.Connection
    ) -> None:
        # Deliberately not a CHECK: the decision layer has to be able to reject
        # this with a justification, which needs the row to exist to be audited.
        insert(conn, receipts_value="500000000.00")


class DescribeDates:
    def it_stores_a_receipt_date_as_a_calendar_date(
        self, conn: psycopg.Connection
    ) -> None:
        insert(conn, receipts_date="2026-01-15")
        assert type(stored(conn, "receipts_date")) is date

    def it_rejects_a_future_receipt_date(self, conn: psycopg.Connection) -> None:
        with pytest.raises(errors.CheckViolation) as exc:
            insert(conn, receipts_date="2999-12-31")
        assert exc.value.diag.constraint_name == "reimbursement_receipts_date_check"

    def it_rejects_a_future_submission(self, conn: psycopg.Connection) -> None:
        with pytest.raises(errors.CheckViolation) as exc:
            insert(conn, submitted_at="2999-12-31T00:00:00Z")
        assert exc.value.diag.constraint_name == "reimbursement_submitted_at_check"

    def it_tolerates_a_slightly_fast_client_clock(
        self, conn: psycopg.Connection
    ) -> None:
        # Rejecting outright over a few minutes of skew would lose the request.
        insert(conn, submitted_at=datetime.now(UTC) + timedelta(minutes=5))


class DescribePayload:
    def it_rejects_text_that_is_not_json(self, conn: psycopg.Connection) -> None:
        with pytest.raises(errors.InvalidTextRepresentation):
            insert(conn, original_payload="this is not json")

    def it_supports_json_traversal(self, conn: psycopg.Connection) -> None:
        insert(conn, original_payload='{"claimed_amount_brl": 150.0}')
        assert stored(conn, "original_payload ->> 'claimed_amount_brl'") == "150.0"


class DescribeFreeText:
    def it_leaves_the_reason_columns_unbounded(self, conn: psycopg.Connection) -> None:
        insert(conn, decision_reason="a" * 100_000, human_review_notes="b" * 100_000)


class DescribeTimestamps:
    def it_stamps_created_and_updated_on_insert(self, conn: psycopg.Connection) -> None:
        _, _, updated = insert(conn)
        assert updated is not None
        assert stored(conn, "created_at") == updated

    def it_keeps_the_updated_at_the_application_sets(
        self, conn: psycopg.Connection
    ) -> None:
        # updated_at is owned by the application, not a trigger: the agent's
        # staleness rule reads it, so whoever writes the row decides its value.
        uuid, _, _ = insert(conn)
        chosen = datetime(2026, 3, 1, 12, 30, tzinfo=UTC)
        row = conn.execute(
            "UPDATE reimbursement SET status = 'human-review', updated_at = %s"
            " WHERE uuid = %s RETURNING updated_at",
            (chosen, uuid),
        ).fetchone()
        assert row is not None
        assert row[0] == chosen


class DescribeListingIndex:
    def it_covers_status_then_recency(self, conn: psycopg.Connection) -> None:
        # Status alone is far too unselective for the planner to prefer over a
        # sequential scan, and the listing pages on created_at.
        assert index_definition(conn, "reimbursement_status_created_idx") == (
            "CREATE INDEX reimbursement_status_created_idx ON public.reimbursement"
            " USING btree (status, created_at DESC)"
        )
