import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from shared.errors import (
    BatchInvalid,
    PayloadTooLarge,
    PublishFailed,
    ReimbursementFilterInvalid,
    ReimbursementNotEligible,
    ReimbursementNotFound,
    ReviewInvalid,
)
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class MessageResponse(BaseModel):
    """The api's single response-body shape for every non-2xx status, and for
    201. api-only by definition — no other service builds HTTP responses, so
    this stays out of `shared`. Folded in here rather than its own file:
    errors.py was already this class's only real consumer."""

    msg: str


def _msg_response(status_code: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=MessageResponse(msg=msg).model_dump())


async def _payload_too_large_handler(request: Request, exc: PayloadTooLarge) -> JSONResponse:
    return _msg_response(413, str(exc))


async def _batch_invalid_handler(request: Request, exc: BatchInvalid) -> JSONResponse:
    return _msg_response(400, str(exc))


async def _publish_failed_handler(request: Request, exc: PublishFailed) -> JSONResponse:
    return _msg_response(500, "failed to publish request")


async def _reimbursement_filter_invalid_handler(
    request: Request, exc: ReimbursementFilterInvalid
) -> JSONResponse:
    return _msg_response(400, str(exc))


async def _review_invalid_handler(request: Request, exc: ReviewInvalid) -> JSONResponse:
    return _msg_response(422, str(exc))


async def _reimbursement_not_found_handler(request: Request, exc: ReimbursementNotFound) -> JSONResponse:
    return _msg_response(404, str(exc))


async def _reimbursement_not_eligible_handler(
    request: Request, exc: ReimbursementNotEligible
) -> JSONResponse:
    return _msg_response(400, str(exc))


async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default here is 422 + {"detail": [...]}; replaced app-wide so
    # every route shares one error contract. Built from `loc`/`msg` only —
    # never `input`, which pydantic's ValidationError carries verbatim.
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first["loc"])
    return _msg_response(400, f"{location}: {first['msg']}")


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _msg_response(exc.status_code, str(exc.detail))


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled exception on %s %s", request.method, request.url.path)
    return _msg_response(500, "internal error")


def register_handlers(app: FastAPI) -> None:
    """Registered app-wide, not per-route: the {"msg"} contract belongs to
    the API, so every future route inherits it instead of re-implementing
    it."""
    app.add_exception_handler(PayloadTooLarge, _payload_too_large_handler)
    app.add_exception_handler(BatchInvalid, _batch_invalid_handler)
    app.add_exception_handler(PublishFailed, _publish_failed_handler)
    app.add_exception_handler(ReimbursementFilterInvalid, _reimbursement_filter_invalid_handler)
    app.add_exception_handler(ReviewInvalid, _review_invalid_handler)
    app.add_exception_handler(ReimbursementNotFound, _reimbursement_not_found_handler)
    app.add_exception_handler(ReimbursementNotEligible, _reimbursement_not_eligible_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
