"""Fetch a single reimbursement enriched with its last human review."""

from uuid import UUID

import asyncpg

from shared.errors import ReimbursementNotFound
from shared.reimbursement.repository import fetch_reimbursement_by_uuid


async def get_reimbursement(conn: asyncpg.Connection, uuid: UUID) -> asyncpg.Record:
    """Returns the row, or raises ReimbursementNotFound — same message
    convention as review_reimbursement.py's _disambiguate."""
    row = await fetch_reimbursement_by_uuid(conn, uuid)
    if row is None:
        raise ReimbursementNotFound(f"no reimbursement with uuid {uuid}")
    return row
