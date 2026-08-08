from typing import Annotated, Any

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    StringConstraints,
)


def _require_str(value: object) -> object:
    # AwareDatetime alone accepts int/float as Unix timestamps, so a bare
    # number would otherwise silently pass as "parseable ISO-8601" — it
    # never was. Runs before AwareDatetime's own parsing (BeforeValidator).
    if not isinstance(value, str):
        raise ValueError("input should be an ISO-8601 string, not a bare number")
    return value


class HealthStatus(BaseModel):
    status: str = "ok"


class SampleMessage(BaseModel):
    id: str
    payload: str


class ReimbursementRequest(BaseModel):
    """One item of the POST /api/v1/reimbursement batch — the api↔publisher
    cross-service contract. `extra="allow"` lets every other field pass
    through untouched; the publish path never re-serialises this model, so
    the stripped/coerced values here never reach Kafka."""

    model_config = ConfigDict(extra="allow")

    request_id: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"
        ),
    ]
    submitted_by: EmailStr
    submitted_at: Annotated[AwareDatetime, BeforeValidator(_require_str)]


class RequestEnvelope(BaseModel):
    """The message shape published to the `Request` Kafka topic. The write
    path never constructs this via the model — the envelope is byte-spliced
    around the raw request body — but the publisher parses what the api
    writes with this same definition."""

    retry: int
    published_at: AwareDatetime
    payload: list[dict[str, Any]]
