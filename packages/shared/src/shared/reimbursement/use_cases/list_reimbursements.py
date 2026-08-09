"""List reimbursements — the single point of entry for the filter/pagination gates."""

import asyncpg

from shared.config import MAX_LIST_LIMIT
from shared.errors import ReimbursementFilterInvalid
from shared.reimbursement.repository import fetch_reimbursement_page

VALID_STATUSES = {
    "auto-approved",
    "human-approved",
    "human-review",
    "auto-rejected",
    "human-rejected",
}  # deliberately excludes "pending" — internal-only, AD-003


async def list_reimbursements(
    conn: asyncpg.Connection, *, statuses: list[str] | None, limit: int, offset: int
) -> list[asyncpg.Record]:
    """GATE 1: every entry in `statuses`, if given, must be client-facing.
    GATE 2: 0 <= limit <= MAX_LIST_LIMIT and offset >= 0.
    Raises ReimbursementFilterInvalid on either violation; never touches
    the DB before both gates pass."""
    if statuses is not None and any(status not in VALID_STATUSES for status in statuses):
        raise ReimbursementFilterInvalid(f"status must be one of {sorted(VALID_STATUSES)}")
    if not (0 <= limit <= MAX_LIST_LIMIT):
        raise ReimbursementFilterInvalid(f"limit must be between 0 and {MAX_LIST_LIMIT}")
    if offset < 0:
        raise ReimbursementFilterInvalid("offset must not be negative")

    return await fetch_reimbursement_page(conn, statuses=statuses, limit=limit, offset=offset)
