"""Every SQL statement against the `reimbursement` table."""

import json
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from shared.models import DecisionStatus, ReimbursementRequest

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

_UPDATE_DECISION = """
    UPDATE reimbursement
    SET status = $2,
        decision_reason = $3,
        receipts_value = COALESCE($4, receipts_value),
        receipts_date = COALESCE($5, receipts_date),
        currency = COALESCE($6, currency),
        updated_at = now()
    WHERE uuid = $1
"""

# hr_*-prefixed columns: r.* already carries its own status/created_at, so
# the LATERAL side needs distinct aliases to avoid a name collision on the
# returned Record. Shared by both queries below via f-string interpolation
# so the enrichment logic can't drift between them.
_HR_LATERAL_ENRICHMENT = """r.*,
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
    ) hr ON true"""

# $1::text[] IS NULL takes the no-filter path; ANY($1)
# matches any status in the caller's whitelist-gated list, single or multi.
_FETCH_REIMBURSEMENT_PAGE = f"""
    SELECT
        {_HR_LATERAL_ENRICHMENT}
    WHERE ($1::text[] IS NULL OR r.status = ANY($1))
    ORDER BY r.created_at DESC
    LIMIT $2 OFFSET $3
"""

# Kept as its own statement (rather than a uuid-filtered branch of the page
# query above) so each statement stays single-purpose.
_FETCH_REIMBURSEMENT_BY_UUID = f"""
    SELECT
        {_HR_LATERAL_ENRICHMENT}
    WHERE r.uuid = $1
"""

# Column is `currency`, not `receipts_currency` — the parameter/keyword stays
# `receipts_currency` to match the payload field name (SCOPE.md/spec.md), the
# repository maps it onto the real column here.
_APPROVE = """
    UPDATE reimbursement
    SET status = 'human-approved',
        receipts_value = $3,
        receipts_date = $4,
        currency = $5,
        decision_reason = $6,
        updated_at = now()
    WHERE uuid = $1 AND status = ANY($2::text[])
    RETURNING *
"""

_REJECT = """
    UPDATE reimbursement
    SET status = 'human-rejected',
        decision_reason = $3,
        receipts_value = COALESCE($4, receipts_value),
        receipts_date = COALESCE($5, receipts_date),
        currency = COALESCE($6, currency),
        updated_at = now()
    WHERE uuid = $1 AND status = ANY($2::text[])
    RETURNING *
"""

_FIND_REIMBURSEMENT_STATE = """
    SELECT uuid, status, receipts_value, receipts_date, currency
    FROM reimbursement
    WHERE uuid = $1
"""

_RECORD_HUMAN_REVIEW_DECISION = """
    INSERT INTO human_review (reimbursement_uuid, status, reviewed_by, reason)
    VALUES ($1, $2, $3, $4)
    RETURNING uuid
"""

_DELETE_PENDING = "DELETE FROM reimbursement WHERE uuid = $1 AND status = 'pending'"


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


async def insert_pending(conn: asyncpg.Connection, item: dict[str, Any]) -> UUID:
    """Insert the item, leaving `status` at its `pending` default. The three
    identity columns are required, not optional: the unique index is on
    (request_id, lower(submitted_by)), so without them it cannot act as the
    dedup guard the duplicate path depends on."""
    return await conn.fetchval(_INSERT_PENDING, *_columns(item))


async def insert_human_review(conn: asyncpg.Connection, item: dict[str, Any], reason: str) -> UUID:
    """Inserts into `reimbursement` at status='human-review' — despite the
    name, this never touches the `human_review` table. To insert a
    `human_review` row, use record_human_review_decision()."""
    return await conn.fetchval(_INSERT_HUMAN_REVIEW, *_columns(item), reason)


async def get_by_uuid(conn: asyncpg.Connection, uuid: UUID) -> asyncpg.Record | None:
    """Full row, not a column subset: `updated_at` drives the Agent's
    staleness check and `original_payload` is what the future processing
    feature needs, so there is no projection narrower than * that serves
    every caller. None on no match (via fetchrow) — the ghost case (R-001)
    maps directly onto this, no exception handling needed to detect it."""
    return await conn.fetchrow(_SELECT_BY_UUID, uuid)


async def update_decision(
    conn: asyncpg.Connection,
    uuid: UUID,
    status: DecisionStatus,
    decision_reason: str,
    *,
    receipts_value: Decimal | None = None,
    receipts_date: date | None = None,
    currency: str | None = None,
) -> bool:
    """Write a decision onto an existing row, backfilling the resolved
    receipts_* columns the same way approve() does — COALESCE against the
    existing column value, so a caller with nothing to backfill (e.g.
    escalate_existing's human-review escalation) leaves them untouched
    rather than nulling out a value a later attempt already resolved.
    Returns whether exactly one row was affected, parsed from asyncpg's
    `UPDATE n` status string — the False branch is what a ghost uuid needs,
    to route to the failure log instead of treating the update as having
    succeeded."""
    result = await conn.execute(
        _UPDATE_DECISION, uuid, status, decision_reason, receipts_value, receipts_date, currency
    )
    return result == "UPDATE 1"


async def fetch_reimbursement_page(
    conn: asyncpg.Connection, *, statuses: list[str] | None, limit: int, offset: int
) -> list[asyncpg.Record]:
    """The one read query this feature needs: a `created_at DESC` page,
    optionally filtered to `statuses`, each row paired with its most recent
    `human_review` (if any) via a LATERAL join. Pure SQL, no validation —
    trusts its caller to have already gated `statuses`/`limit`/`offset`."""
    return await conn.fetch(_FETCH_REIMBURSEMENT_PAGE, statuses, limit, offset)


async def fetch_reimbursement_by_uuid(conn: asyncpg.Connection, uuid: UUID) -> asyncpg.Record | None:
    """Single-row equivalent of fetch_reimbursement_page(): the row paired
    with its most recent human_review (if any) via the same LATERAL join.
    None on no match."""
    return await conn.fetchrow(_FETCH_REIMBURSEMENT_BY_UUID, uuid)


async def approve(
    conn: asyncpg.Connection,
    uuid: UUID,
    *,
    eligible_statuses: list[str],
    receipts_value: Decimal,
    receipts_date: date,
    receipts_currency: str,
    reason: str,
) -> asyncpg.Record | None:
    """Atomic UPDATE ... WHERE ... RETURNING: None means the uuid doesn't
    exist or its current status isn't in `eligible_statuses` — this
    function does not distinguish the two, that's the use case's job."""
    return await conn.fetchrow(
        _APPROVE, uuid, eligible_statuses, receipts_value, receipts_date, receipts_currency, reason
    )


async def reject(
    conn: asyncpg.Connection,
    uuid: UUID,
    *,
    eligible_statuses: list[str],
    reason: str,
    receipts_value: Decimal | None = None,
    receipts_date: date | None = None,
    receipts_currency: str | None = None,
) -> asyncpg.Record | None:
    """Atomic UPDATE ... WHERE ... RETURNING: None means the uuid doesn't
    exist or its current status isn't in `eligible_statuses` — this
    function does not distinguish the two, that's the use case's job.
    Rejecting never requires receipts_value/receipts_date/currency to be
    populated (unlike approve); when supplied anyway they're persisted via
    COALESCE, otherwise the row's existing values are left untouched — a
    rejected claim may stay incomplete."""
    return await conn.fetchrow(
        _REJECT, uuid, eligible_statuses, reason, receipts_value, receipts_date, receipts_currency
    )


async def find_reimbursement_state(conn: asyncpg.Connection, uuid: UUID) -> asyncpg.Record | None:
    """Called only on the 0-rows-affected path of approve()/reject(), to
    disambiguate 404 (no row) from 400 (row exists, ineligible status)."""
    return await conn.fetchrow(_FIND_REIMBURSEMENT_STATE, uuid)


async def record_human_review_decision(
    conn: asyncpg.Connection, reimbursement_uuid: UUID, status: str, reviewed_by: str, reason: str
) -> UUID:
    """Called only from review_reimbursement's transaction, after a
    successful approve()/reject(), to append the audit-trail row those two
    functions don't write themselves. The returned uuid is currently unused
    by any caller."""
    return await conn.fetchval(
        _RECORD_HUMAN_REVIEW_DECISION, reimbursement_uuid, status, reviewed_by, reason
    )


async def delete_pending(conn: asyncpg.Connection, uuid: UUID) -> bool:
    """Compensating delete for a publish failure (AD-033): removes the row
    `publish_pending` just inserted, gated to `status = 'pending'` so this
    can never touch a row some other writer has already moved on. Returns
    whether exactly one row was affected, parsed from asyncpg's `DELETE n`
    status string — the same pattern update_decision() already uses for
    `UPDATE n`."""
    result = await conn.execute(_DELETE_PENDING, uuid)
    return result == "DELETE 1"


def is_duplicate(exc: BaseException) -> bool:
    """True only for a collision on (request_id, lower(submitted_by)).

    Matched on the constraint name rather than the bare 23505 sqlstate: a
    primary-key collision is not a business duplicate and must never take the
    silent-drop path. Text matching would break on locale and server version.
    """
    return isinstance(exc, asyncpg.UniqueViolationError) and exc.constraint_name == DUPLICATE_CONSTRAINT
