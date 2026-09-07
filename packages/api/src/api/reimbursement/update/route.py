import logging
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Request
from shared import failure_log
from shared.config import MAX_BODY_BYTES, load_config
from shared.errors import (
    ReimbursementNotEligible,
    ReimbursementNotFound,
    ReimbursementUuidMismatch,
    sanitize,
)
from shared.logging import log_event
from shared.metrics import reimbursement_status_transitions_total
from shared.reimbursement.use_cases.get_reimbursement import get_reimbursement
from shared.reimbursement.use_cases.review_reimbursement import approve_reimbursement, reject_reimbursement

from api.dependencies import get_pool
from api.errors import MessageResponse
from api.metrics import reimbursement_review_wait_seconds
from api.reimbursement.create.payload import read_capped
from api.reimbursement.response import ReimbursementDetailResponse, ReimbursementItem
from api.reimbursement.update.validation import ApproveReview, validate_review

logger = logging.getLogger(__name__)
router = APIRouter()

DECISION_RECORDED_EVENT = "reimbursement.decision_recorded"
DECISION_WRITE_FAILED_EVENT = "reimbursement.decision_write_failed"


@router.put(
    "/api/v1/reimbursement/{uuid}",
    response_model=ReimbursementDetailResponse,
    responses={
        400: {"model": MessageResponse, "description": "uuid mismatch, or the row's state disallows this decision"},
        404: {"model": MessageResponse, "description": "Unknown reimbursement"},
        413: {
            "model": MessageResponse,
            "description": f"Body exceeds the {MAX_BODY_BYTES // (1024 * 1024)} MiB ceiling",
        },
        422: {"model": MessageResponse, "description": "Review payload failed its own shape contract"},
        500: {"model": MessageResponse, "description": "Failed to apply the decision"},
    },
)
async def put_reimbursement(
    uuid: UUID, request: Request, pool: asyncpg.Pool = Depends(get_pool)
) -> ReimbursementDetailResponse:
    """Parse -> check body/path uuid consistency -> delegate to the use case
    -> respond. Every failure mode, including the uuid consistency check, is
    a raise of a typed exception from validation.py, this route, or the use
    case, caught by the app-wide handlers registered in errors.py."""
    raw = await read_capped(request)
    review = validate_review(raw)
    if review.uuid is not None and review.uuid != uuid:
        raise ReimbursementUuidMismatch("body uuid does not match the path uuid")

    decision_write_error: Exception | None = None
    decision_row: asyncpg.Record | None = None
    async with pool.acquire(timeout=load_config().database.acquire_timeout_seconds) as conn:
        try:
            if isinstance(review, ApproveReview):
                decision_row = await approve_reimbursement(
                    conn,
                    uuid,
                    receipts_value=review.receipts_value,
                    receipts_date=review.receipts_date,
                    receipts_currency=review.receipts_currency,
                    reason=review.reason,
                    approved_by=review.approved_by,
                )
            else:
                decision_row = await reject_reimbursement(
                    conn,
                    uuid,
                    reason=review.reason,
                    approved_by=review.approved_by,
                    receipts_value=review.receipts_value,
                    receipts_date=review.receipts_date,
                    receipts_currency=review.receipts_currency,
                )
        except (ReimbursementNotFound, ReimbursementNotEligible):
            # Routine, already-typed business outcomes (unknown uuid / row
            # ineligible for this decision) — each has its own registered
            # 404/400 handler in errors.py. Not a failure_log-worthy event:
            # that channel is the last-resort record for something nothing
            # else in the chain could handle, not for expected client
            # behavior like a double-submitted or stale decision.
            raise
        except Exception as exc:
            # Genuinely unexpected — held until the connection is released
            # below, matching publisher.processing's own convention of not
            # holding a pool connection across a (potentially blocking)
            # failure_log.write() call.
            decision_write_error = exc
        else:
            try:
                row = await get_reimbursement(conn, uuid)
            except ReimbursementNotFound as exc:
                raise RuntimeError(
                    f"reimbursement {uuid} decision committed but could not be re-fetched afterward"
                ) from exc

    if decision_write_error is not None:
        failure_log.write(
            load_config().failure_log,
            {
                "event": DECISION_WRITE_FAILED_EVENT,
                "uuid": str(uuid),
                "approved_by": review.approved_by,
                "error": sanitize(decision_write_error),
            },
        )
        raise decision_write_error

    from_status = decision_row["from_status"]
    reimbursement_status_transitions_total.labels(from_status, row["status"]).inc()
    if from_status == "human-review":
        wait_seconds = (datetime.now(UTC) - decision_row["from_updated_at"]).total_seconds()
        reimbursement_review_wait_seconds.observe(wait_seconds)

    log_event(
        logger,
        logging.INFO,
        DECISION_RECORDED_EVENT,
        uuid=str(uuid),
        status=row["status"],
        approved_by=review.approved_by,
    )

    return ReimbursementDetailResponse(
        msg="reimbursement decision recorded", data=ReimbursementItem.from_record(row)
    )
