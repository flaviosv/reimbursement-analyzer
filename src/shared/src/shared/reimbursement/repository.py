"""Pool lifecycle and every SQL statement against the `reimbursement` table."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import asyncpg

from shared.config import DatabaseConfig
from shared.models import ReimbursementRequest

DUPLICATE_CONSTRAINT = "reimbursement_request_submitter_key"

# ::text::timestamptz and ::text::jsonb: the item arrives as decoded JSON, so
# these two values are strings. A bare ::timestamptz would have asyncpg infer
# the parameter as timestamptz and reject the string outright.
_INSERT_PENDING = """
    INSERT INTO reimbursement (request_id, submitted_by, submitted_at, original_payload)
    VALUES ($1, $2, $3::text::timestamptz, $4::text::jsonb)
    RETURNING uuid
"""

_INSERT_HUMAN_REVIEW = """
    INSERT INTO reimbursement (
        request_id, submitted_by, submitted_at, original_payload, status, decision_reason
    )
    VALUES ($1, $2, $3::text::timestamptz, $4::text::jsonb, 'human-review', $5)
    RETURNING uuid
"""

_SELECT_BY_UUID = "SELECT * FROM reimbursement WHERE uuid = $1"

_UPDATE_HUMAN_REVIEW = """
    UPDATE reimbursement
    SET status = 'human-review', decision_reason = $2, updated_at = now()
    WHERE uuid = $1
"""


def _columns(item: dict[str, Any]) -> tuple[Any, ...]:
    # The three identity columns come from the *validated* model, not the
    # raw dict: ReimbursementRequest.request_id strips whitespace pydantic's
    # own validation already lets through, so the raw dict's un-stripped
    # form would silently split one request_id into two on-disk spellings
    # — one of which the (request_id, lower(submitted_by)) dedup index would
    # never catch. All three columns are equally required by this model.
    # Always valid in practice — the caller has already gated on this same
    # validation (processing._accepts) before reaching here.
    validated = ReimbursementRequest.model_validate(item)
    return (
        validated.request_id,
        validated.submitted_by,
        validated.submitted_at.isoformat(),
        json.dumps(item),
    )


@asynccontextmanager
async def managed_pool(config: DatabaseConfig) -> AsyncIterator[asyncpg.Pool]:
    """Construct a connection pool and guarantee `close()` on exit — the same
    construct/yield/close shape as shared.producer.managed_producer, so the
    two resources compose identically in a service's startup.

    Both sizes are always passed: create_pool defaults to min_size=10,
    max_size=10, which exactly equals the publisher's item concurrency and so
    would leave the pool zero headroom (AD-017). command_timeout bounds every
    query issued through this pool — without it, a stuck connection (broker
    failover, network partition, lock contention) hangs the caller forever,
    since neither service's consume loop has its own per-call timeout.
    """
    pool = await asyncpg.create_pool(
        dsn=config.dsn,
        min_size=config.pool_min_size,
        max_size=config.pool_max_size,
        command_timeout=config.command_timeout,
    )
    try:
        yield pool
    finally:
        await pool.close()


async def insert_pending(conn: asyncpg.Connection, item: dict[str, Any]) -> UUID:
    """Insert the item, leaving `status` at its `pending` default. The three
    identity columns are required, not optional: the unique index is on
    (request_id, lower(submitted_by)), so without them it cannot act as the
    dedup guard the duplicate path depends on."""
    return await conn.fetchval(_INSERT_PENDING, *_columns(item))


async def insert_human_review(conn: asyncpg.Connection, item: dict[str, Any], reason: str) -> UUID:
    return await conn.fetchval(_INSERT_HUMAN_REVIEW, *_columns(item), reason)


async def get_by_uuid(conn: asyncpg.Connection, uuid: UUID) -> asyncpg.Record | None:
    """Full row, not a column subset: `updated_at` drives the Agent's
    staleness check and `original_payload` is what the future processing
    feature needs, so there is no projection narrower than * that serves
    every caller. None on no match (via fetchrow) — the ghost case (R-001)
    maps directly onto this, no exception handling needed to detect it."""
    return await conn.fetchrow(_SELECT_BY_UUID, uuid)


async def update_human_review(conn: asyncpg.Connection, uuid: UUID, reason: str) -> bool:
    """Escalate an existing row to human-review. Returns whether exactly one
    row was affected, parsed from asyncpg's `UPDATE n` status string — the
    False branch is what a ghost uuid past the retry ceiling needs, to route
    to the failure log instead of treating the update as having succeeded."""
    status = await conn.execute(_UPDATE_HUMAN_REVIEW, uuid, reason)
    return status == "UPDATE 1"


def is_duplicate(exc: BaseException) -> bool:
    """True only for a collision on (request_id, lower(submitted_by)).

    Matched on the constraint name rather than the bare 23505 sqlstate: a
    primary-key collision is not a business duplicate and must never take the
    silent-drop path. Text matching would break on locale and server version.
    """
    return isinstance(exc, asyncpg.UniqueViolationError) and exc.constraint_name == DUPLICATE_CONSTRAINT
