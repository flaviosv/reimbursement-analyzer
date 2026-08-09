from uuid import UUID, uuid4

import asyncpg
import pytest
from shared.testing import valid_reimbursement_item
from shared.reimbursement.repository import insert_pending
from shared.reimbursement.use_cases.apply_decision import apply_decision

pytestmark = pytest.mark.anyio


class DescribeApplyDecision:
    async def it_updates_the_row_and_returns_its_uuid(self, db: asyncpg.Connection) -> None:
        uuid = await insert_pending(db, valid_reimbursement_item("REQ-APPLY-DECISION"))

        result = await apply_decision(db, uuid, "auto-approved", "value 150 <= 200 threshold")

        assert result == uuid
        assert isinstance(result, UUID)
        row = await db.fetchrow("SELECT * FROM reimbursement WHERE uuid = $1", uuid)
        assert row["status"] == "auto-approved"
        assert row["decision_reason"] == "value 150 <= 200 threshold"

    async def it_returns_none_for_a_uuid_matching_no_row(self, db: asyncpg.Connection) -> None:
        result = await apply_decision(db, uuid4(), "human-review", "unreachable reason")

        assert result is None
