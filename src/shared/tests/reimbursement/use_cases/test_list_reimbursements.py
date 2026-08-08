import asyncpg
import pytest
from helpers import valid_reimbursement_item
from shared.config import MAX_LIST_LIMIT
from shared.errors import ReimbursementFilterInvalid
from shared.reimbursement.repository import insert_pending
from shared.reimbursement.use_cases.list_reimbursements import list_reimbursements

pytestmark = pytest.mark.anyio


class DescribeListReimbursements:
    async def it_raises_when_a_status_is_outside_the_client_facing_whitelist(self) -> None:
        # "pending" is the documented edge case (AD-003): internal-only,
        # never client-facing, even though it's a real column value.
        with pytest.raises(ReimbursementFilterInvalid):
            await list_reimbursements(None, statuses=["pending"], limit=100, offset=0)

    async def it_raises_when_limit_exceeds_the_ceiling(self) -> None:
        with pytest.raises(ReimbursementFilterInvalid):
            await list_reimbursements(None, statuses=None, limit=MAX_LIST_LIMIT + 1, offset=0)

    async def it_raises_when_limit_is_negative(self) -> None:
        with pytest.raises(ReimbursementFilterInvalid):
            await list_reimbursements(None, statuses=None, limit=-1, offset=0)

    async def it_raises_when_offset_is_negative(self) -> None:
        with pytest.raises(ReimbursementFilterInvalid):
            await list_reimbursements(None, statuses=None, limit=100, offset=-1)

    async def it_delegates_to_fetch_reimbursement_page_and_returns_real_rows_unchanged(
        self, db: asyncpg.Connection
    ) -> None:
        # Scoped by a status no other test in the full suite commits outside
        # its own rolled-back transaction (see DescribeFetchReimbursementPage's
        # same note in test_repository.py) — an exclusive-ownership assumption
        # over the whole table would be flaky.
        uuid = await insert_pending(db, valid_reimbursement_item("REQ-LIST-DELEGATE"))
        await db.execute("UPDATE reimbursement SET status = 'human-review' WHERE uuid = $1", uuid)

        rows = await list_reimbursements(db, statuses=["human-review"], limit=10, offset=0)

        assert [row["uuid"] for row in rows] == [uuid]
        assert rows[0]["request_id"] == "REQ-LIST-DELEGATE"
