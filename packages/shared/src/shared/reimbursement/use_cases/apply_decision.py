"""Write a decision (status + reason) onto an existing `reimbursement` row."""

from uuid import UUID

import asyncpg

from shared.reimbursement import repository


async def apply_decision(
    conn: asyncpg.Connection, uuid: UUID, status: str, decision_reason: str
) -> UUID | None:
    """Returns the uuid on success, None when the uuid is a ghost (0 rows
    affected) — mirrors escalate_existing's own ghost-signaling contract."""
    updated = await repository.update_decision(conn, uuid, status, decision_reason)
    return uuid if updated else None
