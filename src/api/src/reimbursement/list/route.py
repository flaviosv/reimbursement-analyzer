import asyncpg
from fastapi import APIRouter, Depends, Query, Request
from shared.config import load_config

from dependencies import get_pool
from errors import MessageResponse
from reimbursement.list.params import LimitQuery, OffsetQuery, parse_status_filter
from reimbursement.list.response import ReimbursementListItem, ReimbursementListResponse
from shared.reimbursement.use_cases.list_reimbursements import list_reimbursements

router = APIRouter()


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
    status: str | None = Query(
        None,
        description="Comma-separated reimbursement statuses to filter by. "
        "Must be supplied as a single query parameter (AD-028); repeating "
        "`status` is rejected.",
    ),
    limit: LimitQuery = 100,
    offset: OffsetQuery = 0,
    pool: asyncpg.Pool = Depends(get_pool),
) -> ReimbursementListResponse:
    """Orchestrates parse -> query -> shape -> respond — every failure mode
    is still a raise from one of the collaborators, caught by the app-wide
    handlers registered in errors.py."""
    statuses = parse_status_filter(request.query_params.getlist("status"))
    async with pool.acquire(timeout=load_config().database.acquire_timeout_seconds) as conn:
        rows = await list_reimbursements(conn, statuses=statuses, limit=limit, offset=offset)
    return ReimbursementListResponse(
        msg=f"{len(rows)} reimbursement(s) found",
        data=[ReimbursementListItem.from_record(row) for row in rows],
    )
