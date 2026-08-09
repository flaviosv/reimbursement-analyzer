from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends
from shared.config import load_config

from api.dependencies import get_pool
from api.errors import MessageResponse
from api.reimbursement.response import ReimbursementDetailResponse, ReimbursementItem
from shared.reimbursement.use_cases.get_reimbursement import get_reimbursement

router = APIRouter()


@router.get(
    "/api/v1/reimbursement/{uuid}",
    response_model=ReimbursementDetailResponse,
    responses={
        400: {"model": MessageResponse, "description": "Malformed uuid"},
        404: {"model": MessageResponse, "description": "Unknown reimbursement"},
        500: {"model": MessageResponse, "description": "Failed to query reimbursement"},
    },
)
async def get_reimbursement_by_uuid(
    uuid: UUID, pool: asyncpg.Pool = Depends(get_pool)
) -> ReimbursementDetailResponse:
    """Delegate -> shape -> respond. A malformed uuid never reaches here —
    FastAPI's own path coercion raises RequestValidationError first, caught
    by the app-wide handler in errors.py."""
    async with pool.acquire(timeout=load_config().database.acquire_timeout_seconds) as conn:
        row = await get_reimbursement(conn, uuid)
    return ReimbursementDetailResponse(msg="reimbursement found", data=ReimbursementItem.from_record(row))
