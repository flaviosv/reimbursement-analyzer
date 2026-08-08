"""Pool lifecycle and every SQL statement against the `reimbursement` table."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import asyncpg

from shared.config import DatabaseConfig

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


def _columns(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item["request_id"],
        item.get("submitted_by"),
        item.get("submitted_at"),
        json.dumps(item),
    )


@asynccontextmanager
async def managed_pool(config: DatabaseConfig) -> AsyncIterator[asyncpg.Pool]:
    """Construct a connection pool and guarantee `close()` on exit — the same
    construct/yield/close shape as shared.producer.managed_producer, so the
    two resources compose identically in a service's startup.

    Both sizes are always passed: create_pool defaults to min_size=10,
    max_size=10, which exactly equals the publisher's item concurrency and so
    would leave the pool zero headroom (AD-017).
    """
    pool = await asyncpg.create_pool(
        dsn=config.dsn,
        min_size=config.pool_min_size,
        max_size=config.pool_max_size,
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


def is_duplicate(exc: BaseException) -> bool:
    """True only for a collision on (request_id, lower(submitted_by)).

    Matched on the constraint name rather than the bare 23505 sqlstate: a
    primary-key collision is not a business duplicate and must never take the
    silent-drop path. Text matching would break on locale and server version.
    """
    return isinstance(exc, asyncpg.UniqueViolationError) and exc.constraint_name == DUPLICATE_CONSTRAINT
