import logging
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends
from opentelemetry import trace
from shared.config import load_config
from shared.errors import ReimbursementNotFound
from shared.logging import get_correlation_id, log_event
from shared.reimbursement.use_cases.get_reimbursement import get_reimbursement
from shared.tracing import stamp_span

from api.dependencies import get_pool
from api.errors import MessageResponse
from api.reimbursement.response import ReimbursementDetailResponse, ReimbursementItem

logger = logging.getLogger(__name__)
router = APIRouter()

DETAIL_QUERIED_EVENT = "reimbursement.detail_queried"


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
    stamp_span(trace.get_current_span(), uuid=uuid, correlation_id=get_correlation_id())
    async with pool.acquire(timeout=load_config().database.acquire_timeout_seconds) as conn:
        try:
            row = await get_reimbursement(conn, uuid)
        except ReimbursementNotFound:
            log_event(logger, logging.INFO, DETAIL_QUERIED_EVENT, uuid=str(uuid), found=False)
            raise
    log_event(logger, logging.INFO, DETAIL_QUERIED_EVENT, uuid=str(uuid), found=True)
    return ReimbursementDetailResponse(msg="reimbursement found", data=ReimbursementItem.from_record(row))
