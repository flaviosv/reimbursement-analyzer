import json
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Self
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime, BaseModel


class HumanReviewSummary(BaseModel):
    status: Literal["approved", "rejected"]
    reviewed_by: str
    reason: str
    created_at: AwareDatetime


class ReimbursementListItem(BaseModel):
    uuid: UUID
    request_id: str
    submitted_by: str | None
    submitted_at: AwareDatetime | None
    original_payload: dict[str, Any]
    status: str
    receipts_value: Decimal | None
    receipts_date: date | None
    currency: str | None
    decision_reason: str | None
    human_review_notes: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    last_human_review: HumanReviewSummary | None

    @classmethod
    def from_record(cls, record: asyncpg.Record) -> Self:
        """Build one response item from a fetch_reimbursement_page() row —
        the hr_*-prefixed LATERAL columns are NULL together when no
        human_review row exists, non-NULL together when one does."""
        last_human_review = None
        if record["hr_status"] is not None:
            last_human_review = HumanReviewSummary(
                status=record["hr_status"],
                reviewed_by=record["hr_reviewed_by"],
                reason=record["hr_reason"],
                created_at=record["hr_created_at"],
            )
        return cls(
            uuid=record["uuid"],
            request_id=record["request_id"],
            submitted_by=record["submitted_by"],
            submitted_at=record["submitted_at"],
            original_payload=json.loads(record["original_payload"]),
            status=record["status"],
            receipts_value=record["receipts_value"],
            receipts_date=record["receipts_date"],
            currency=record["currency"],
            decision_reason=record["decision_reason"],
            human_review_notes=record["human_review_notes"],
            created_at=record["created_at"],
            updated_at=record["updated_at"],
            last_human_review=last_human_review,
        )


class ReimbursementListResponse(BaseModel):
    msg: str
    data: list[ReimbursementListItem]
