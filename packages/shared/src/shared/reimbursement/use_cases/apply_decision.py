"""Write a decision (status + reason) onto an existing `reimbursement` row."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import asyncpg

from shared.models import DecisionStatus
from shared.reimbursement import repository


async def apply_decision(
    conn: asyncpg.Connection,
    uuid: UUID,
    status: DecisionStatus,
    decision_reason: str,
    *,
    receipts_value: Decimal | None = None,
    receipts_date: date | None = None,
    currency: str | None = None,
) -> UUID | None:
    """Returns the uuid on success, None when the uuid is a ghost (0 rows
    affected) — mirrors escalate_existing's own ghost-signaling contract."""
    updated = await repository.update_decision(
        conn,
        uuid,
        status,
        decision_reason,
        receipts_value=receipts_value,
        receipts_date=receipts_date,
        currency=currency,
    )
    return uuid if updated else None
