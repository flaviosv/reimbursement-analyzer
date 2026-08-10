from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
)

from shared.config import MAX_BATCH_ITEMS

# Where an AttemptError originated: publisher's own insert/publish, the
# agent's resolve-by-uuid step, or its decision graph (AD-039).
Stage = Literal["db-insert", "publish", "resolve", "decide"]

# The three outcomes the decision graph (and its human-review escalation
# fallback) ever write via repository.update_decision — distinct from
# human-approved/human-rejected, which approve()/reject() write via their
# own hardcoded SQL literals, never through this path.
DecisionStatus = Literal["auto-approved", "auto-rejected", "human-review"]


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


class AttemptError(BaseModel):
    """One failed attempt at turning a request item into a reimbursement.

    The history travels on the envelope because the consumer that eventually
    sees `retry = 4` is a different poll — possibly a different process — and
    has no other way to learn what failed on attempts 1-3 (AD-014)."""

    attempt: int
    occurred_at: AwareDatetime
    stage: Stage
    error_type: str
    message: str

    @classmethod
    def from_exception(
        cls, attempt: int, stage: Stage, exc: Exception, occurred_at: datetime | None = None
    ) -> Self:
        return cls(
            attempt=attempt,
            occurred_at=occurred_at or datetime.now(UTC),
            stage=stage,
            error_type=type(exc).__name__,
            message=str(exc),
        )


class RequestEnvelope(BaseModel):
    """The message shape published to the `Request` Kafka topic.

    The *original* write path never constructs this via the model — the
    envelope is byte-spliced around the raw request body in
    api/producer.py — but the publisher parses what the api writes with
    this same definition, and its own requeue path (a *second* write path)
    does construct one via the model directly."""

    retry: Annotated[int, Field(ge=0)]
    published_at: AwareDatetime
    errors: list[AttemptError] = []
    # Bounded to the same ceiling the API enforces at ingress: the
    # publisher must not trust that every producer onto this topic is the
    # API — its own requeue path is one, and a directly-produced message
    # is another — so it re-asserts the cap at its own trust boundary
    # rather than relying solely on fetch.max.bytes to keep item count down.
    payload: Annotated[list[dict[str, Any]], Field(max_length=MAX_BATCH_ITEMS)]


class ReimbursementEnvelope(BaseModel):
    """The message shape published to the `Reimbursement` Kafka topic. It
    carries the row's `uuid` and no payload — the Agent reads the payload
    back from `reimbursement.original_payload` (AD-015)."""

    uuid: UUID
    retry: int
    published_at: AwareDatetime
    errors: list[AttemptError] = []
