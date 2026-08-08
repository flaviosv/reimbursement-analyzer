import json

import pytest
from reimbursement.update.validation import ApproveReview, RejectReview, validate_review
from shared.errors import ReviewInvalid

_APPROVE_PAYLOAD = {
    "status": "approved",
    "reason": "looks good",
    "receipts_date": "2026-01-05",
    "receipts_value": "50.00",
    "receipts_currency": "BRL",
    "approved_by": "reviewer@example.com",
}

_REJECT_PAYLOAD = {
    "status": "rejected",
    "reason": "missing evidence",
    "approved_by": "reviewer@example.com",
}


def _body(payload: dict) -> bytes:
    return json.dumps(payload).encode()


class DescribeValidateReview:
    def it_parses_a_valid_approve_payload_into_approve_review(self) -> None:
        review = validate_review(_body(_APPROVE_PAYLOAD))

        assert isinstance(review, ApproveReview)
        assert review.reason == "looks good"

    def it_parses_a_valid_reject_payload_into_reject_review(self) -> None:
        review = validate_review(_body(_REJECT_PAYLOAD))

        assert isinstance(review, RejectReview)
        assert review.approved_by == "reviewer@example.com"

    def it_raises_when_a_required_approve_field_is_missing(self) -> None:
        payload = {k: v for k, v in _APPROVE_PAYLOAD.items() if k != "receipts_value"}

        with pytest.raises(ReviewInvalid):
            validate_review(_body(payload))

    def it_raises_when_receipts_date_is_malformed(self) -> None:
        payload = {**_APPROVE_PAYLOAD, "receipts_date": "not-a-date"}

        with pytest.raises(ReviewInvalid):
            validate_review(_body(payload))

    def it_raises_when_receipts_currency_is_malformed(self) -> None:
        payload = {**_APPROVE_PAYLOAD, "receipts_currency": "brl"}

        with pytest.raises(ReviewInvalid):
            validate_review(_body(payload))

    def it_raises_when_receipts_value_is_negative(self) -> None:
        payload = {**_APPROVE_PAYLOAD, "receipts_value": "-1.00"}

        with pytest.raises(ReviewInvalid):
            validate_review(_body(payload))

    def it_raises_when_approved_by_is_missing_on_reject(self) -> None:
        payload = {k: v for k, v in _REJECT_PAYLOAD.items() if k != "approved_by"}

        with pytest.raises(ReviewInvalid):
            validate_review(_body(payload))

    def it_raises_when_status_is_not_approved_or_rejected(self) -> None:
        payload = {**_REJECT_PAYLOAD, "status": "cancelled"}

        with pytest.raises(ReviewInvalid):
            validate_review(_body(payload))
