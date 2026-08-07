from typing import Any

import psycopg
import pytest
from psycopg import errors
from test_reimbursement_schema import index_definition, insert


def insert_review(
    conn: psycopg.Connection, reimbursement_uuid: Any, **values: Any
) -> tuple[Any, ...]:
    values.setdefault("status", "approved")
    values.setdefault("reviewed_by", "reviewer@company.com")
    values.setdefault("reason", "looks fine")
    values["reimbursement_uuid"] = reimbursement_uuid
    columns = ", ".join(values)
    placeholders = ", ".join(f"%({name})s" for name in values)
    row = conn.execute(
        f"INSERT INTO human_review ({columns}) VALUES ({placeholders})"
        " RETURNING uuid, created_at",
        values,
    ).fetchone()
    assert row is not None
    return row


@pytest.fixture
def reimbursement_uuid(conn: psycopg.Connection) -> Any:
    uuid, _, _ = insert(conn, request_id="REQ-REVIEWED")
    return uuid


class DescribeReviewDecision:
    def it_generates_a_uuid(
        self, conn: psycopg.Connection, reimbursement_uuid: Any
    ) -> None:
        uuid, _ = insert_review(conn, reimbursement_uuid)
        assert uuid is not None

    @pytest.mark.parametrize("status", ["approved", "rejected"])
    def it_accepts_the_specified_statuses(
        self, conn: psycopg.Connection, reimbursement_uuid: Any, status: str
    ) -> None:
        insert_review(conn, reimbursement_uuid, status=status)

    @pytest.mark.parametrize("status", ["pending", "human-approved", "APPROVED", ""])
    def it_rejects_any_other_status(
        self, conn: psycopg.Connection, reimbursement_uuid: Any, status: str
    ) -> None:
        with pytest.raises(errors.CheckViolation) as exc:
            insert_review(conn, reimbursement_uuid, status=status)
        assert exc.value.diag.constraint_name == "human_review_status_check"


class DescribeReviewer:
    @pytest.mark.parametrize("email", ["not-an-email", "no@domain", "a b@c.com"])
    def it_rejects_a_malformed_address(
        self, conn: psycopg.Connection, reimbursement_uuid: Any, email: str
    ) -> None:
        with pytest.raises(errors.CheckViolation) as exc:
            insert_review(conn, reimbursement_uuid, reviewed_by=email)
        assert exc.value.diag.constraint_name == "human_review_reviewed_by_check"

    def it_rejects_an_overlong_address(
        self, conn: psycopg.Connection, reimbursement_uuid: Any
    ) -> None:
        with pytest.raises(errors.CheckViolation):
            insert_review(conn, reimbursement_uuid, reviewed_by="a" * 250 + "@c.com")


class DescribeReason:
    def it_is_required(
        self, conn: psycopg.Connection, reimbursement_uuid: Any
    ) -> None:
        with pytest.raises(errors.NotNullViolation):
            insert_review(conn, reimbursement_uuid, reason=None)

    def it_is_unbounded(
        self, conn: psycopg.Connection, reimbursement_uuid: Any
    ) -> None:
        insert_review(conn, reimbursement_uuid, reason="c" * 100_000)


class DescribeReferentialIntegrity:
    def it_rejects_a_review_of_nothing(self, conn: psycopg.Connection) -> None:
        with pytest.raises(errors.ForeignKeyViolation) as exc:
            insert_review(conn, "00000000-0000-0000-0000-000000000000")
        assert exc.value.diag.constraint_name == "human_review_reimbursement_uuid_fkey"

    def it_refuses_to_delete_a_reviewed_reimbursement(
        self, conn: psycopg.Connection, reimbursement_uuid: Any
    ) -> None:
        insert_review(conn, reimbursement_uuid)
        # RESTRICT raises restrict_violation (23001); a NO ACTION fallback would
        # raise foreign_key_violation (23503), so this pins the action itself.
        with pytest.raises(errors.RestrictViolation) as exc:
            conn.execute(
                "DELETE FROM reimbursement WHERE uuid = %s", (reimbursement_uuid,)
            )
        assert exc.value.diag.constraint_name == "human_review_reimbursement_uuid_fkey"


class DescribeAppendOnly:
    def it_refuses_an_update(
        self, conn: psycopg.Connection, reimbursement_uuid: Any
    ) -> None:
        # An audit trail the application can quietly rewrite is not an audit
        # trail, so this is enforced here rather than left as a convention.
        uuid, _ = insert_review(conn, reimbursement_uuid, status="rejected")
        with pytest.raises(errors.RestrictViolation, match="append-only"):
            conn.execute(
                "UPDATE human_review SET status = 'approved' WHERE uuid = %s", (uuid,)
            )

    def it_refuses_a_delete(
        self, conn: psycopg.Connection, reimbursement_uuid: Any
    ) -> None:
        uuid, _ = insert_review(conn, reimbursement_uuid)
        with pytest.raises(errors.RestrictViolation, match="append-only"):
            conn.execute("DELETE FROM human_review WHERE uuid = %s", (uuid,))

    def it_accumulates_decisions_instead_of_overwriting(
        self, conn: psycopg.Connection, reimbursement_uuid: Any
    ) -> None:
        first, _ = insert_review(conn, reimbursement_uuid, status="rejected")
        second, _ = insert_review(conn, reimbursement_uuid, status="approved")
        rows = conn.execute(
            "SELECT uuid, status FROM human_review WHERE reimbursement_uuid = %s"
            " ORDER BY created_at DESC, uuid DESC",
            (reimbursement_uuid,),
        ).fetchall()
        assert [r[0] for r in rows] == [second, first]
        assert rows[0][1] == "approved"

    def it_has_no_updated_at_column(self, conn: psycopg.Connection) -> None:
        columns = {
            name
            for (name,) in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'human_review'"
            ).fetchall()
        }
        assert "updated_at" not in columns


class DescribeLookupIndex:
    def it_orders_the_newest_review_first(self, conn: psycopg.Connection) -> None:
        # The DESC is the point: it serves "return the last Human Review if any".
        assert index_definition(conn, "human_review_reimbursement_created_idx") == (
            "CREATE INDEX human_review_reimbursement_created_idx ON"
            " public.human_review USING btree (reimbursement_uuid, created_at DESC)"
        )
