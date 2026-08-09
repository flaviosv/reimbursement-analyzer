from uuid import uuid4

import asyncpg
import pytest
from shared.errors import ReimbursementNotFound
from shared.reimbursement.use_cases.get_reimbursement import get_reimbursement
from shared.testing import seed_reimbursement

pytestmark = pytest.mark.anyio


class DescribeGetReimbursement:
    async def it_returns_the_row_when_the_uuid_matches(self, db: asyncpg.Connection) -> None:
        uuid = await seed_reimbursement(db, "REQ-GET-UC-FOUND", status="human-review")

        row = await get_reimbursement(db, uuid)

        assert row["uuid"] == uuid
        assert row["status"] == "human-review"

    async def it_raises_reimbursement_not_found_when_the_uuid_matches_no_row(
        self, db: asyncpg.Connection
    ) -> None:
        with pytest.raises(ReimbursementNotFound):
            await get_reimbursement(db, uuid4())
