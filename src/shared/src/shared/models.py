from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, EmailStr, StringConstraints


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

    request_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    submitted_by: EmailStr
    submitted_at: AwareDatetime


class RequestEnvelope(BaseModel):
    """The message shape published to the `Request` Kafka topic. The write
    path never constructs this via the model — the envelope is byte-spliced
    around the raw request body — but the publisher parses what the api
    writes with this same definition."""

    retry: int
    published_at: AwareDatetime
    payload: list[dict[str, Any]]
