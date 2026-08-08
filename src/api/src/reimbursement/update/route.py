from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from shared.config import load_config

from dependencies import get_pool
from errors import MessageResponse
from reimbursement.update.validation import ApproveReview, validate_review
from shared.reimbursement.use_cases.review_reimbursement import approve_reimbursement, reject_reimbursement

router = APIRouter()


@router.put(
    "/api/v1/reimbursement/{uuid}",
    response_model=MessageResponse,
    responses={
        400: {"model": MessageResponse, "description": "uuid mismatch, or the row's state disallows this decision"},
        404: {"model": MessageResponse, "description": "Unknown reimbursement"},
        422: {"model": MessageResponse, "description": "Review payload failed its own shape contract"},
        500: {"model": MessageResponse, "description": "Failed to apply the decision"},
    },
)
async def put_reimbursement(
    uuid: UUID, request: Request, pool: asyncpg.Pool = Depends(get_pool)
) -> MessageResponse:
    """Parse -> check body/path uuid consistency -> delegate to the use case
    -> respond. No branching logic of its own beyond the uuid consistency
    check — every other failure mode is a raise from validation.py or the
    use case, caught by the app-wide handlers registered in errors.py."""
    raw = await request.body()
    review = validate_review(raw)
    if review.uuid is not None and review.uuid != uuid:
        raise HTTPException(status_code=400, detail="body uuid does not match the path uuid")

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

    return MessageResponse(msg="reimbursement decision recorded")
