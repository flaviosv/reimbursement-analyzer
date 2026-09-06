"""Approve/reject a reimbursement — the one piece of real business logic
this feature has: eligibility, the two-write transaction, and 404 vs 400
disambiguation, kept out of route.py and repository.py alike.

Reject has no completeness gate: a reimbursement may be finalized as
human-rejected while receipts_value/receipts_date/currency are still NULL.
Reject's payload may optionally supply those three fields (unlike approve,
where they're required) — if given, they're persisted; if omitted, the
row's existing values (NULL or not) are left untouched.

No explicit row lock (`SELECT ... FOR UPDATE`) before the atomic UPDATE:
Postgres's own row-level write serialization already makes two concurrent
transitions mutually exclusive — the loser's `WHERE status = ANY(...)`
simply matches zero rows once the winner's UPDATE commits, so an explicit
lock would add nothing but reduce concurrency on the (common) uncontended
path. Confirmed with user (2026-08-08).
"""

from datetime import date
from decimal import Decimal
from typing import NoReturn
from uuid import UUID

import asyncpg

from shared.errors import ReimbursementNotEligible, ReimbursementNotFound
from shared.reimbursement.repository import (
    approve,
    find_reimbursement_state,
    record_human_review_decision,
    reject,
)

ELIGIBLE_STATUSES = ["human-review", "auto-rejected", "human-rejected"]


async def _disambiguate(conn: asyncpg.Connection, uuid: UUID) -> NoReturn:
    """Called only on the 0-rows-affected path: distinguishes "no such
    row" (404) from "row exists, but its status is ineligible" (400) —
    always raises, never returns."""
    state = await find_reimbursement_state(conn, uuid)
    if state is None:
        raise ReimbursementNotFound(f"no reimbursement with uuid {uuid}")
    raise ReimbursementNotEligible(f"reimbursement {uuid} is not eligible for this decision")


async def approve_reimbursement(
    conn: asyncpg.Connection,
    uuid: UUID,
    *,
    receipts_value: Decimal,
    receipts_date: date,
    receipts_currency: str,
    reason: str,
    approved_by: str,
) -> asyncpg.Record:
    async with conn.transaction():
        row = await approve(
            conn,
            uuid,
            eligible_statuses=ELIGIBLE_STATUSES,
            receipts_value=receipts_value,
            receipts_date=receipts_date,
            receipts_currency=receipts_currency,
            reason=reason,
        )
        if row is None:
            await _disambiguate(conn, uuid)
        await record_human_review_decision(conn, uuid, "approved", approved_by, reason)
        return row


async def reject_reimbursement(
    conn: asyncpg.Connection,
    uuid: UUID,
    *,
    reason: str,
    approved_by: str,
    receipts_value: Decimal | None = None,
    receipts_date: date | None = None,
    receipts_currency: str | None = None,
) -> asyncpg.Record:
    async with conn.transaction():
        row = await reject(
            conn,
            uuid,
            eligible_statuses=ELIGIBLE_STATUSES,
            reason=reason,
            receipts_value=receipts_value,
            receipts_date=receipts_date,
            receipts_currency=receipts_currency,
        )
        if row is None:
            await _disambiguate(conn, uuid)
        await record_human_review_decision(conn, uuid, "rejected", approved_by, reason)
        return row
