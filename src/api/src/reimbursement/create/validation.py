from typing import Annotated, Any

from pydantic import Field, TypeAdapter, ValidationError
from shared.config import MAX_BATCH_ITEMS
from shared.errors import BatchInvalid
from shared.models import ReimbursementRequest

# Module-level: building a TypeAdapter per request re-compiles the validator.
BATCH_ADAPTER = TypeAdapter(Annotated[list[ReimbursementRequest], Field(max_length=MAX_BATCH_ITEMS)])


def _format_error(error: dict[str, Any]) -> str:
    # Built from `loc` and `msg` only — never `input`, which ValidationError
    # carries as the raw offending value.
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
