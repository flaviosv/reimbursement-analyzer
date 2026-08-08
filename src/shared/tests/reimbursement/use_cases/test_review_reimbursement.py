import asyncio
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest
from shared.errors import ReimbursementNotEligible, ReimbursementNotFound
from shared.reimbursement.use_cases.review_reimbursement import (
    approve_reimbursement,
    reject_reimbursement,
)

pytestmark = pytest.mark.anyio

_SEED_REIMBURSEMENT = """
    INSERT INTO reimbursement (uuid, request_id, original_payload, status)
    VALUES ($1, $2, '{}'::jsonb, $3)
"""

_SEED_REIMBURSEMENT_WITH_RECEIPTS = """
    INSERT INTO reimbursement (
        uuid, request_id, original_payload, status, receipts_value, receipts_date, currency
    )
    VALUES ($1, $2, '{}'::jsonb, $3, $4, $5, $6)
"""


async def _seed(db: asyncpg.Connection, request_id: str, *, status: str = "human-review") -> UUID:
    uuid = uuid4()
    await db.execute(_SEED_REIMBURSEMENT, uuid, request_id, status)
    return uuid


async def _seed_with_receipts(db: asyncpg.Connection, request_id: str, *, status: str = "human-review") -> UUID:
    uuid = uuid4()
    await db.execute(
        _SEED_REIMBURSEMENT_WITH_RECEIPTS, uuid, request_id, status, Decimal("100.00"), date(2026, 1, 1), "BRL"
    )
    return uuid


class DescribeApproveReimbursement:
    async def it_approves_an_eligible_row_and_records_the_decision(self, db: asyncpg.Connection) -> None:
        uuid = await _seed(db, "REQ-UC-APPROVE-OK")

        row = await approve_reimbursement(
            db,
            uuid,
            receipts_value=Decimal("50.00"),
            receipts_date=date(2026, 1, 5),
            receipts_currency="BRL",
            reason="looks good",
            approved_by="reviewer@example.com",
        )

        assert row["status"] == "human-approved"
        assert row["receipts_value"] == Decimal("50.00")
        hr = await db.fetchrow("SELECT * FROM human_review WHERE reimbursement_uuid = $1", uuid)
        assert hr["status"] == "approved"
        assert hr["reviewed_by"] == "reviewer@example.com"

    async def it_raises_reimbursement_not_found_for_an_unknown_uuid(self, db: asyncpg.Connection) -> None:
        with pytest.raises(ReimbursementNotFound):
            await approve_reimbursement(
                db,
                uuid4(),
                receipts_value=Decimal("1"),
                receipts_date=date(2026, 1, 1),
                receipts_currency="BRL",
                reason="x",
                approved_by="a@example.com",
            )

        count = await db.fetchval("SELECT count(*) FROM human_review")
        assert count == 0

    async def it_raises_reimbursement_not_eligible_for_an_ineligible_status(
        self, db: asyncpg.Connection
    ) -> None:
        uuid = await _seed(db, "REQ-UC-APPROVE-INELIGIBLE", status="human-approved")

        with pytest.raises(ReimbursementNotEligible):
            await approve_reimbursement(
                db,
                uuid,
                receipts_value=Decimal("1"),
                receipts_date=date(2026, 1, 1),
                receipts_currency="BRL",
                reason="x",
                approved_by="a@example.com",
            )

        status = await db.fetchval("SELECT status FROM reimbursement WHERE uuid = $1", uuid)
        assert status == "human-approved"
        count = await db.fetchval("SELECT count(*) FROM human_review WHERE reimbursement_uuid = $1", uuid)
        assert count == 0


class DescribeRejectReimbursement:
    async def it_rejects_an_eligible_complete_row_and_records_the_decision(
        self, db: asyncpg.Connection
    ) -> None:
        uuid = await _seed_with_receipts(db, "REQ-UC-REJECT-OK")

        row = await reject_reimbursement(db, uuid, reason="bad receipt", approved_by="reviewer@example.com")

        assert row["status"] == "human-rejected"
        hr = await db.fetchrow("SELECT * FROM human_review WHERE reimbursement_uuid = $1", uuid)
        assert hr["status"] == "rejected"

    async def it_raises_reimbursement_not_found_for_an_unknown_uuid(self, db: asyncpg.Connection) -> None:
        with pytest.raises(ReimbursementNotFound):
            await reject_reimbursement(db, uuid4(), reason="x", approved_by="a@example.com")

    async def it_raises_reimbursement_not_eligible_for_an_ineligible_status(
        self, db: asyncpg.Connection
    ) -> None:
        uuid = await _seed_with_receipts(db, "REQ-UC-REJECT-INELIGIBLE", status="human-approved")

        with pytest.raises(ReimbursementNotEligible):
            await reject_reimbursement(db, uuid, reason="x", approved_by="a@example.com")

    async def it_raises_reimbursement_not_eligible_when_the_entity_is_incomplete(
        self, db: asyncpg.Connection
    ) -> None:
        uuid = await _seed(db, "REQ-UC-REJECT-INCOMPLETE")

        with pytest.raises(ReimbursementNotEligible):
            await reject_reimbursement(db, uuid, reason="x", approved_by="a@example.com")

        count = await db.fetchval("SELECT count(*) FROM human_review WHERE reimbursement_uuid = $1", uuid)
        assert count == 0


class DescribeReviewReimbursementConcurrency:
    async def it_lets_exactly_one_of_two_concurrent_decisions_win(self, migrated_db: str) -> None:
        # Two genuinely separate real connections, not the rolled-back `db`
        # fixture's single connection: the REVIEW-09 guarantee rests on
        # Postgres's own row-level write serialization between two
        # in-flight transactions, which a single-connection fixture cannot
        # exercise (asyncpg rejects concurrent operations on one connection).
        conn_a = await asyncpg.connect(migrated_db)
        conn_b = await asyncpg.connect(migrated_db)
        try:
            uuid = uuid4()
            await conn_a.execute(_SEED_REIMBURSEMENT, uuid, "REQ-UC-CONCURRENT", "human-review")

            results = await asyncio.gather(
                approve_reimbursement(
                    conn_a,
                    uuid,
                    receipts_value=Decimal("10.00"),
                    receipts_date=date(2026, 1, 1),
                    receipts_currency="BRL",
                    reason="a",
                    approved_by="a@example.com",
                ),
                approve_reimbursement(
                    conn_b,
                    uuid,
                    receipts_value=Decimal("20.00"),
                    receipts_date=date(2026, 1, 2),
                    receipts_currency="BRL",
                    reason="b",
                    approved_by="b@example.com",
                ),
                return_exceptions=True,
            )

            successes = [result for result in results if not isinstance(result, BaseException)]
            failures = [result for result in results if isinstance(result, BaseException)]
            assert len(successes) == 1
            assert len(failures) == 1
            assert isinstance(failures[0], ReimbursementNotEligible)
            count = await conn_a.fetchval(
                "SELECT count(*) FROM human_review WHERE reimbursement_uuid = $1", uuid
            )
            assert count == 1
        finally:
            await conn_a.close()
            await conn_b.close()
