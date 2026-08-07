from typing import Any

from pydantic import TypeAdapter, ValidationError
from shared.models import ReimbursementRequest
from starlette.requests import Request

from api.config import MAX_BODY_BYTES
from api.errors import BatchInvalid, PayloadTooLarge

# Module-level: building a TypeAdapter per request re-compiles the validator.
BATCH_ADAPTER = TypeAdapter(list[ReimbursementRequest])


async def read_capped(request: Request) -> bytes:
    """Stream the body while counting bytes, aborting the moment the running
    total exceeds MAX_BODY_BYTES — before an oversized body is ever fully
    buffered. Content-Length is never consulted: it is absent under chunked
    encoding and is client-controlled either way."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise PayloadTooLarge("payload exceeds 25 MiB limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _format_error(error: dict[str, Any]) -> str:
    # Built from `loc` and `msg` only — never `input`, which ValidationError
    # carries as the raw offending value (RCV-11).
    loc = error["loc"]
    if not loc:
        return str(error["msg"])
    if len(loc) == 1:
        return f"item {loc[0]}: {error['msg']}"
    index, *field_parts = loc
    field = ".".join(str(part) for part in field_parts)
    return f"item {index}: {field} — {error['msg']}"


def validate_batch(raw: bytes) -> list[ReimbursementRequest]:
    """Validate the streamed body as a JSON array of ReimbursementRequest.
    All-or-nothing: the first error found rejects the whole batch."""
    try:
        batch = BATCH_ADAPTER.validate_json(raw)
    except ValidationError as exc:
        raise BatchInvalid(_format_error(exc.errors()[0])) from exc
    if not batch:
        raise BatchInvalid("batch must contain at least one request")
    return batch
