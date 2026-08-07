from datetime import datetime, timezone
from typing import Any

import psycopg
import pytest
from psycopg import errors

REIMBURSEMENT_COLUMNS = {
    "created_at",
    "currency",
    "decision_reason",
    "human_review_notes",
    "original_payload",
    "receipts_date",
    "receipts_value",
    "request_id",
    "status",
    "submitted_at",
    "submitted_by",
    "updated_at",
    "uuid",
}


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


def columns_of(conn: psycopg.Connection, table: str) -> set[str]:
    return {
        name
        for (name,) in conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = %s",
            (table,),
        ).fetchall()
    }


# --- shape -------------------------------------------------------------------


def test_reimbursement_has_exactly_the_specified_columns(
    conn: psycopg.Connection,
) -> None:
    assert columns_of(conn, "reimbursement") == REIMBURSEMENT_COLUMNS


def test_status_index_exists(conn: psycopg.Connection) -> None:
    indexes = {
        name
        for (name,) in conn.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'reimbursement'"
        ).fetchall()
    }
    assert "reimbursement_status_idx" in indexes


# --- defaults ----------------------------------------------------------------


def test_publisher_shaped_insert_succeeds_with_only_required_fields(
    conn: psycopg.Connection,
) -> None:
    uuid, status, _ = insert(conn)
    assert uuid is not None
    assert status == "pending"


def test_generated_uuids_are_time_ordered(conn: psycopg.Connection) -> None:
    first, _, _ = insert(conn, request_id="REQ-A")
    second, _, _ = insert(conn, request_id="REQ-B")
    # uuidv7 is time-ordered; v4 would fail this roughly half the time.
    assert second > first


def test_request_id_is_required(conn: psycopg.Connection) -> None:
    with pytest.raises(errors.NotNullViolation):
        conn.execute("INSERT INTO reimbursement (original_payload) VALUES ('{}')")


def test_original_payload_is_required(conn: psycopg.Connection) -> None:
    with pytest.raises(errors.NotNullViolation):
        conn.execute("INSERT INTO reimbursement (request_id) VALUES ('REQ-X')")


# --- status ------------------------------------------------------------------


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
def test_every_specified_status_is_accepted(
    conn: psycopg.Connection, status: str
) -> None:
    _, stored, _ = insert(conn, status=status)
    assert stored == status


@pytest.mark.parametrize("status", ["bogus", "approved", "AUTO-APPROVED", ""])
def test_unknown_status_is_rejected(conn: psycopg.Connection, status: str) -> None:
    with pytest.raises(errors.CheckViolation) as exc:
        insert(conn, status=status)
    assert exc.value.diag.constraint_name == "reimbursement_status_check"


# --- unique (request_id, submitted_by) ---------------------------------------


def test_duplicate_request_and_submitter_is_rejected(
    conn: psycopg.Connection,
) -> None:
    insert(conn, request_id="REQ-DUP", submitted_by="ana@company.com")
    with pytest.raises(errors.UniqueViolation) as exc:
        insert(conn, request_id="REQ-DUP", submitted_by="ana@company.com")
    assert exc.value.diag.constraint_name == "reimbursement_request_submitter_key"


def test_duplicate_request_with_null_submitter_is_rejected(
    conn: psycopg.Connection,
) -> None:
    # The NULLS NOT DISTINCT case: standard SQL NULL semantics would allow
    # unlimited duplicates here, which is exactly the leak we must not have.
    insert(conn, request_id="REQ-NULL")
    with pytest.raises(errors.UniqueViolation) as exc:
        insert(conn, request_id="REQ-NULL")
    assert exc.value.diag.constraint_name == "reimbursement_request_submitter_key"


def test_same_request_id_from_different_submitters_is_allowed(
    conn: psycopg.Connection,
) -> None:
    insert(conn, request_id="REQ-SHARED", submitted_by="ana@company.com")
    insert(conn, request_id="REQ-SHARED", submitted_by="carlos@company.com")


# --- email -------------------------------------------------------------------


@pytest.mark.parametrize(
    "email",
    ["not-an-email", "no@domain", "spaces in@mail.com", "@company.com", "a@b."],
)
def test_malformed_submitted_by_is_rejected(
    conn: psycopg.Connection, email: str
) -> None:
    with pytest.raises(errors.CheckViolation) as exc:
        insert(conn, submitted_by=email)
    assert exc.value.diag.constraint_name == "reimbursement_submitted_by_check"


def test_overlong_submitted_by_is_rejected(conn: psycopg.Connection) -> None:
    with pytest.raises(errors.CheckViolation) as exc:
        insert(conn, submitted_by="a" * 250 + "@company.com")
    assert exc.value.diag.constraint_name == "reimbursement_submitted_by_check"


def test_null_submitted_by_is_accepted(conn: psycopg.Connection) -> None:
    insert(conn, submitted_by=None)


# --- currency ----------------------------------------------------------------


@pytest.mark.parametrize("currency", ["brl", "BR", "BRLL", "1X!", " BR"])
def test_invalid_currency_is_rejected(
    conn: psycopg.Connection, currency: str
) -> None:
    with pytest.raises(errors.CheckViolation) as exc:
        insert(conn, currency=currency)
    assert exc.value.diag.constraint_name == "reimbursement_currency_check"


def test_iso_currency_is_accepted(conn: psycopg.Connection) -> None:
    insert(conn, currency="BRL")


# --- request_id length -------------------------------------------------------


def test_overlong_request_id_is_rejected(conn: psycopg.Connection) -> None:
    with pytest.raises(errors.CheckViolation) as exc:
        insert(conn, request_id="x" * 65)
    assert exc.value.diag.constraint_name == "reimbursement_request_id_length_check"


def test_request_id_at_the_limit_is_accepted(conn: psycopg.Connection) -> None:
    insert(conn, request_id="x" * 64)


# --- deliberately unbounded free text ----------------------------------------


def test_free_text_columns_are_unbounded(conn: psycopg.Connection) -> None:
    insert(
        conn,
        decision_reason="a" * 100_000,
        human_review_notes="b" * 100_000,
    )


# --- money and dates ---------------------------------------------------------


def test_receipts_value_keeps_two_decimal_places(conn: psycopg.Connection) -> None:
    insert(conn, receipts_value="93.50")
    stored = conn.execute(
        "SELECT receipts_value FROM reimbursement WHERE request_id = 'REQ-0001'"
    ).fetchone()
    assert stored is not None
    assert str(stored[0]) == "93.50"


# --- updated_at trigger ------------------------------------------------------


def test_updated_at_trigger_overrides_a_caller_supplied_value(
    conn: psycopg.Connection,
) -> None:
    uuid, _, created = insert(conn)
    updated = conn.execute(
        "UPDATE reimbursement SET status = 'human-review',"
        " updated_at = '2000-01-01T00:00:00Z' WHERE uuid = %s"
        " RETURNING updated_at",
        (uuid,),
    ).fetchone()
    assert updated is not None
    # The agent's staleness rule reads updated_at, so a caller must not be able
    # to backdate it. now() is transaction-scoped, so within this one
    # transaction the trigger's value equals the insert's -- the point is that
    # it is the server's clock and not the value the caller passed.
    assert updated[0] != datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert updated[0] == created


def test_updated_at_advances_across_transactions(migrated_db: str) -> None:
    with psycopg.connect(migrated_db) as writer:
        uuid, _, created = insert(writer, request_id="REQ-TXN")
        writer.commit()
        updated = writer.execute(
            "UPDATE reimbursement SET status = 'human-review' WHERE uuid = %s"
            " RETURNING updated_at",
            (uuid,),
        ).fetchone()
        assert updated is not None
        assert updated[0] > created
        writer.execute("DELETE FROM reimbursement WHERE uuid = %s", (uuid,))
        writer.commit()
