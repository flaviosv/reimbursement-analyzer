from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Request
from shared.config import MAX_BODY_BYTES, load_config
from shared.errors import ReimbursementUuidMismatch
from shared.reimbursement.use_cases.get_reimbursement import get_reimbursement
from shared.reimbursement.use_cases.review_reimbursement import approve_reimbursement, reject_reimbursement

from api.dependencies import get_pool
from api.errors import MessageResponse
from api.reimbursement.create.payload import read_capped
from api.reimbursement.response import ReimbursementDetailResponse, ReimbursementItem
from api.reimbursement.update.validation import ApproveReview, validate_review

router = APIRouter()


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

    async with pool.acquire(timeout=load_config().database.acquire_timeout_seconds) as conn:
        if isinstance(review, ApproveReview):
            await approve_reimbursement(
                conn,
                uuid,
                receipts_value=review.receipts_value,
                receipts_date=review.receipts_date,
                receipts_currency=review.receipts_currency,
                reason=review.reason,
                approved_by=review.approved_by,
            )
        else:
            await reject_reimbursement(conn, uuid, reason=review.reason, approved_by=review.approved_by)

        row = await get_reimbursement(conn, uuid)

    return ReimbursementDetailResponse(
        msg="reimbursement decision recorded", data=ReimbursementItem.from_record(row)
    )
