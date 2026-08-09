import asyncpg
import pytest
from shared.testing import valid_reimbursement_item
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

    async def it_accepts_limit_zero_the_lower_inclusive_boundary(
        self, db: asyncpg.Connection
    ) -> None:
        rows = await list_reimbursements(db, statuses=None, limit=0, offset=0)

        assert rows == []

    async def it_accepts_limit_at_the_ceiling_the_upper_inclusive_boundary(
        self, db: asyncpg.Connection
    ) -> None:
        rows = await list_reimbursements(db, statuses=None, limit=MAX_LIST_LIMIT, offset=0)

        assert isinstance(rows, list)

    async def it_delegates_to_fetch_reimbursement_page_and_returns_real_rows_unchanged(
        self, db: asyncpg.Connection
    ) -> None:
        # Membership, not exact-set: other pre-existing integration tests
        # (e.g. agent's, REVIEW-09) commit real, undeletable human-review
        # rows to this same session-scoped migrated_db.
        uuid = await insert_pending(db, valid_reimbursement_item("REQ-LIST-DELEGATE"))
        await db.execute("UPDATE reimbursement SET status = 'human-review' WHERE uuid = $1", uuid)

        rows = await list_reimbursements(db, statuses=["human-review"], limit=10, offset=0)

        by_uuid = {row["uuid"]: row for row in rows}
        assert uuid in by_uuid
        assert by_uuid[uuid]["request_id"] == "REQ-LIST-DELEGATE"
