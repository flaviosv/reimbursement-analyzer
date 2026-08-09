from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, StringConstraints, TypeAdapter, ValidationError
from shared.errors import ReviewInvalid


class ApproveReview(BaseModel):
    status: Literal["approved"]
    reason: str
    receipts_date: date
    receipts_value: Annotated[Decimal, Field(ge=0)]  # mirrors the DB CHECK
    receipts_currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]  # mirrors the DB CHECK
    approved_by: EmailStr
    uuid: UUID | None = None


class RejectReview(BaseModel):
    status: Literal["rejected"]
    reason: str
    approved_by: EmailStr
    uuid: UUID | None = None


ReviewRequest = Annotated[ApproveReview | RejectReview, Field(discriminator="status")]
REVIEW_ADAPTER = TypeAdapter(ReviewRequest)


def _format_error(error: dict[str, Any]) -> str:
    # Built from `loc`/`msg` only — never `input`, which ValidationError
    # carries as the raw offending value.
    loc = error["loc"]
    field = ".".join(str(part) for part in loc) if loc else "body"
    return f"{field}: {error['msg']}"


def validate_review(raw: bytes) -> ApproveReview | RejectReview:
    """Parse `raw` against the approve/reject discriminated union; raise
    ReviewInvalid naming the offending field on any shape failure."""
    try:
        return REVIEW_ADAPTER.validate_json(raw)
    except ValidationError as exc:
        raise ReviewInvalid(_format_error(exc.errors()[0])) from exc
