import asyncpg
from fastapi import APIRouter, Depends, Request
from shared.config import load_config

from dependencies import get_pool
from errors import MessageResponse
from reimbursement.list.params import LimitQuery, OffsetQuery, parse_status_filter
from reimbursement.list.response import ReimbursementListItem, ReimbursementListResponse
from shared.errors import ReimbursementFilterInvalid
from shared.reimbursement.use_cases.list_reimbursements import list_reimbursements

router = APIRouter()


def _single_status_param(request: Request) -> str | None:
    """`status` is read from the raw query params, not a FastAPI-bound
    scalar: FastAPI's own Query() binding silently collapses a repeated
    `?status=a&status=b` to one value instead of raising, which would
    silently violate AD-028's "only the single-param comma-separated form is
    accepted" contract (LIST-08)."""
    values = request.query_params.getlist("status")
    if len(values) > 1:
        raise ReimbursementFilterInvalid("status must be supplied once, comma-separated for multiple values")
    return values[0] if values else None


@router.get(
    "/api/v1/reimbursement",
    response_model=ReimbursementListResponse,
    responses={
        400: {"model": MessageResponse, "description": "Invalid status/limit/offset filter"},
        500: {"model": MessageResponse, "description": "Failed to query reimbursements"},
    },
)
async def get_reimbursements(
    request: Request,
    limit: LimitQuery = 100,
    offset: OffsetQuery = 0,
    pool: asyncpg.Pool = Depends(get_pool),
) -> ReimbursementListResponse:
    """Orchestrates parse -> query -> shape -> respond — every failure mode
    is still a raise from one of the collaborators, caught by the app-wide
    handlers registered in errors.py."""
    statuses = parse_status_filter(_single_status_param(request))
    async with pool.acquire(timeout=load_config().database.acquire_timeout_seconds) as conn:
        rows = await list_reimbursements(conn, statuses=statuses, limit=limit, offset=offset)
    return ReimbursementListResponse(
        msg=f"{len(rows)} reimbursement(s) found",
        data=[ReimbursementListItem.from_record(row) for row in rows],
    )
