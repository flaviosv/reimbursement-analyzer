"""Every SQL statement against the `reimbursement` table."""

import json
from typing import Any
from uuid import UUID

import asyncpg

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

# hr_*-prefixed columns: r.* already carries its own status/created_at, so
# the LATERAL side needs distinct aliases to avoid a name collision on the
# returned Record. $1::text[] IS NULL takes the no-filter path; ANY($1)
# matches any status in the caller's whitelist-gated list, single or multi.
_FETCH_REIMBURSEMENT_PAGE = """
    SELECT
        r.*,
        hr.status AS hr_status,
        hr.reviewed_by AS hr_reviewed_by,
        hr.reason AS hr_reason,
        hr.created_at AS hr_created_at
    FROM reimbursement r
    LEFT JOIN LATERAL (
        SELECT status, reviewed_by, reason, created_at
        FROM human_review
        WHERE reimbursement_uuid = r.uuid
        ORDER BY created_at DESC
        LIMIT 1
    ) hr ON true
    WHERE ($1::text[] IS NULL OR r.status = ANY($1))
    ORDER BY r.created_at DESC
    LIMIT $2 OFFSET $3
"""


def _columns(item: dict[str, Any]) -> tuple[Any, ...]:
    # The three identity columns come from the *validated* model, not the
    # raw dict: ReimbursementRequest.request_id strips whitespace pydantic's
    # own validation already lets through, so the raw dict's un-stripped
    # form would silently split one request_id into two on-disk spellings
    # — one of which the (request_id, lower(submitted_by)) dedup index would
    # never catch (S5). Also resolves the previous subscript-vs-.get()
    # inconsistency: all three are equally required by this model (Q15).
    # Always valid in practice — the caller has already gated on this same
    # validation (processing._accepts) before reaching here.
    validated = ReimbursementRequest.model_validate(item)
    return (
        validated.request_id,
        validated.submitted_by,
        validated.submitted_at.isoformat(),
        json.dumps(item),
    )


async def insert_pending(conn: asyncpg.Connection, item: dict[str, Any]) -> UUID:
    """Insert the item, leaving `status` at its `pending` default. The three
    identity columns are required, not optional: the unique index is on
    (request_id, lower(submitted_by)), so without them it cannot act as the
    dedup guard the duplicate path depends on."""
    return await conn.fetchval(_INSERT_PENDING, *_columns(item))


async def insert_human_review(conn: asyncpg.Connection, item: dict[str, Any], reason: str) -> UUID:
    return await conn.fetchval(_INSERT_HUMAN_REVIEW, *_columns(item), reason)


async def fetch_reimbursement_page(
    conn: asyncpg.Connection, *, statuses: list[str] | None, limit: int, offset: int
) -> list[asyncpg.Record]:
    """The one read query this feature needs: a `created_at DESC` page,
    optionally filtered to `statuses`, each row paired with its most recent
    `human_review` (if any) via a LATERAL join. Pure SQL, no validation —
    trusts its caller to have already gated `statuses`/`limit`/`offset`."""
    return await conn.fetch(_FETCH_REIMBURSEMENT_PAGE, statuses, limit, offset)


def is_duplicate(exc: BaseException) -> bool:
    """True only for a collision on (request_id, lower(submitted_by)).

    Matched on the constraint name rather than the bare 23505 sqlstate: a
    primary-key collision is not a business duplicate and must never take the
    silent-drop path. Text matching would break on locale and server version.
    """
    return isinstance(exc, asyncpg.UniqueViolationError) and exc.constraint_name == DUPLICATE_CONSTRAINT
