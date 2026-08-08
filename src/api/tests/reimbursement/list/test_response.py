from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from reimbursement.list.response import ReimbursementListItem

_BASE_RECORD = {
    "uuid": uuid4(),
    "request_id": "REQ-1",
    "submitted_by": "person@example.com",
    "submitted_at": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    "status": "human-approved",
    "receipts_value": Decimal("93.50"),
    "receipts_date": date(2026, 1, 2),
    "currency": "BRL",
    "decision_reason": "looks good",
    "human_review_notes": None,
    "created_at": datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
    "updated_at": datetime(2026, 1, 2, 8, 0, tzinfo=UTC),
}


class DescribeReimbursementListItemFromRecord:
    def it_builds_last_human_review_and_round_trips_every_column_when_hr_columns_are_populated(
        self,
    ) -> None:
        record = {
            **_BASE_RECORD,
            "hr_status": "approved",
            "hr_reviewed_by": "reviewer@example.com",
            "hr_reason": "all good",
            "hr_created_at": datetime(2026, 1, 3, tzinfo=UTC),
        }

        item = ReimbursementListItem.from_record(record)

        assert item.uuid == _BASE_RECORD["uuid"]
        assert item.request_id == "REQ-1"
        assert item.submitted_by == "person@example.com"
        assert item.submitted_at == _BASE_RECORD["submitted_at"]
        assert item.status == "human-approved"
        assert item.receipts_value == Decimal("93.50")
        assert item.receipts_date == date(2026, 1, 2)
        assert item.currency == "BRL"
        assert item.decision_reason == "looks good"
        assert item.human_review_notes is None
        assert item.created_at == _BASE_RECORD["created_at"]
        assert item.updated_at == _BASE_RECORD["updated_at"]
        assert item.last_human_review is not None
        assert item.last_human_review.status == "approved"
        assert item.last_human_review.reviewed_by == "reviewer@example.com"
        assert item.last_human_review.reason == "all good"
        assert item.last_human_review.created_at == datetime(2026, 1, 3, tzinfo=UTC)

    def it_sets_last_human_review_to_none_when_the_hr_columns_are_null(self) -> None:
        record = {
            **_BASE_RECORD,
            "hr_status": None,
            "hr_reviewed_by": None,
            "hr_reason": None,
            "hr_created_at": None,
        }

        item = ReimbursementListItem.from_record(record)

        assert item.last_human_review is None
