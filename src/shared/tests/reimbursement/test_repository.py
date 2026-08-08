import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest
from helpers import valid_reimbursement_item
from shared.reimbursement.repository import (
    insert_human_review,
    insert_pending,
    is_duplicate,
)

pytestmark = pytest.mark.anyio

_RAW_INSERT = """
    INSERT INTO reimbursement (uuid, request_id, original_payload)
    VALUES ($1, $2, '{}'::jsonb)
"""


async def _rows_for(db: asyncpg.Connection, request_id: str) -> list[asyncpg.Record]:
    return await db.fetch("SELECT * FROM reimbursement WHERE request_id = $1", request_id)


class DescribeInsertPending:
    async def it_returns_the_uuid_of_the_row_it_created(self, db: asyncpg.Connection) -> None:
        uuid = await insert_pending(db, valid_reimbursement_item("REQ-RETURN"))

        assert isinstance(uuid, UUID)
        assert [row["uuid"] for row in await _rows_for(db, "REQ-RETURN")] == [uuid]

    async def it_populates_the_identity_columns_and_the_original_payload(
        self, db: asyncpg.Connection
    ) -> None:
        item = valid_reimbursement_item("REQ-FIELDS", amount=93.5, currency="BRL")

        uuid = await insert_pending(db, item)

        row = await db.fetchrow("SELECT * FROM reimbursement WHERE uuid = $1", uuid)
        assert row["request_id"] == "REQ-FIELDS"
        assert row["submitted_by"] == "person@example.com"
        assert row["submitted_at"] == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        assert json.loads(row["original_payload"]) == item

    async def it_leaves_the_status_at_its_pending_default(self, db: asyncpg.Connection) -> None:
        uuid = await insert_pending(db, valid_reimbursement_item("REQ-PENDING"))

        status = await db.fetchval("SELECT status FROM reimbursement WHERE uuid = $1", uuid)
        assert status == "pending"

    async def it_stores_no_second_row_for_a_repeated_request_and_submitter(
        self, db: asyncpg.Connection
    ) -> None:
        item = valid_reimbursement_item("REQ-DUP", amount=10.0)
        first_uuid = await insert_pending(db, item)
        resubmitted = valid_reimbursement_item("REQ-DUP", amount=999.0)

        with pytest.raises(asyncpg.UniqueViolationError):
            async with db.transaction():
                await insert_pending(db, resubmitted)

        rows = await _rows_for(db, "REQ-DUP")
        assert [row["uuid"] for row in rows] == [first_uuid]
        # The rejected insert leaves the row it collided with untouched — it is
        # still the first submission, not a silent overwrite by the second.
        assert json.loads(rows[0]["original_payload"]) == item
        assert rows[0]["status"] == "pending"
        assert rows[0]["decision_reason"] is None

    async def it_stores_the_validated_request_id_not_the_raw_whitespace_padded_one(
        self, db: asyncpg.Connection
    ) -> None:
        # request_id has strip_whitespace=True on the pydantic model; the raw
        # dict does not carry that normalisation. Storing the raw form would
        # let " REQ-PAD " and "REQ-PAD" coexist as two rows the dedup index
        # was supposed to treat as the same request (S5).
        item = valid_reimbursement_item(" REQ-PAD ")

        uuid = await insert_pending(db, item)

        row = await db.fetchrow("SELECT request_id FROM reimbursement WHERE uuid = $1", uuid)
        assert row["request_id"] == "REQ-PAD"

    async def it_stores_no_second_row_when_only_the_submitter_case_differs(
        self, db: asyncpg.Connection
    ) -> None:
        # The index is on lower(submitted_by): a byte-exact key would let one
        # person file the same request under ana@, Ana@ and ANA@.
        first_uuid = await insert_pending(
            db, valid_reimbursement_item("REQ-CASE", submitted_by="ana@company.com")
        )

        with pytest.raises(asyncpg.UniqueViolationError):
            async with db.transaction():
                await insert_pending(
                    db, valid_reimbursement_item("REQ-CASE", submitted_by="ANA@Company.com")
                )

        rows = await _rows_for(db, "REQ-CASE")
        assert [row["uuid"] for row in rows] == [first_uuid]
        assert rows[0]["submitted_by"] == "ana@company.com"


class DescribeInsertHumanReview:
    async def it_stores_the_row_at_human_review_with_its_reason(self, db: asyncpg.Connection) -> None:
        item = valid_reimbursement_item("REQ-REVIEW")
        reason = "attempt 1 [db-insert] RuntimeError: connection reset"

        uuid = await insert_human_review(db, item, reason)

        row = await db.fetchrow("SELECT * FROM reimbursement WHERE uuid = $1", uuid)
        assert row["status"] == "human-review"
        assert row["decision_reason"] == reason

    async def it_populates_the_identity_columns_and_the_original_payload(
        self, db: asyncpg.Connection
    ) -> None:
        item = valid_reimbursement_item("REQ-REVIEW-FIELDS", amount=12.0)

        uuid = await insert_human_review(db, item, "ceiling reached")

        row = await db.fetchrow("SELECT * FROM reimbursement WHERE uuid = $1", uuid)
        assert row["request_id"] == "REQ-REVIEW-FIELDS"
        assert row["submitted_by"] == "person@example.com"
        assert row["submitted_at"] == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        assert json.loads(row["original_payload"]) == item


class DescribeIsDuplicate:
    async def it_classifies_a_request_and_submitter_collision_as_a_duplicate(
        self, db: asyncpg.Connection
    ) -> None:
        item = valid_reimbursement_item("REQ-IS-DUP")
        await insert_pending(db, item)

        with pytest.raises(asyncpg.UniqueViolationError) as caught:
            async with db.transaction():
                await insert_pending(db, item)

        assert caught.value.constraint_name == "reimbursement_request_submitter_key"
        assert is_duplicate(caught.value) is True

    async def it_does_not_classify_a_primary_key_collision_as_a_duplicate(
        self, db: asyncpg.Connection
    ) -> None:
        # A PK collision is a different failure entirely and must not take the
        # silent-drop path — which a bare 23505 sqlstate check would let it.
        uuid = uuid4()
        await db.execute(_RAW_INSERT, uuid, "REQ-PK-FIRST")

        with pytest.raises(asyncpg.UniqueViolationError) as caught:
            async with db.transaction():
                await db.execute(_RAW_INSERT, uuid, "REQ-PK-SECOND")

        assert caught.value.constraint_name == "reimbursement_pkey"
        assert is_duplicate(caught.value) is False

    async def it_does_not_classify_an_unrelated_error_as_a_duplicate(self) -> None:
        assert is_duplicate(RuntimeError("connection reset by peer")) is False
