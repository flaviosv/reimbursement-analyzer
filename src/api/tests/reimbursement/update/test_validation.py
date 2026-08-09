import json

import pytest
from helpers import valid_approve_payload, valid_reject_payload
from api.reimbursement.update.validation import ApproveReview, RejectReview, validate_review
from shared.errors import ReviewInvalid

_APPROVE_PAYLOAD = valid_approve_payload()
_REJECT_PAYLOAD = valid_reject_payload()


def _body(payload: dict) -> bytes:
    return json.dumps(payload).encode()


def _missing(payload: dict, key: str) -> dict:
    return {k: v for k, v in payload.items() if k != key}


_INVALID_REVIEW_CASES = [
    pytest.param(
        _missing(_APPROVE_PAYLOAD, "receipts_value"),
        "receipts_value: Field required",
        id="approve-missing-receipts_value",
    ),
    pytest.param(
        {**_APPROVE_PAYLOAD, "receipts_date": "not-a-date"},
        "receipts_date: Input should be a valid date",
        id="approve-malformed-receipts_date",
    ),
    pytest.param(
        {**_APPROVE_PAYLOAD, "receipts_currency": "brl"},
        "receipts_currency: String should match pattern",
        id="approve-malformed-receipts_currency",
    ),
    pytest.param(
        {**_APPROVE_PAYLOAD, "receipts_value": "-1.00"},
        "receipts_value: Input should be greater than or equal to 0",
        id="approve-negative-receipts_value",
    ),
    pytest.param(
        _missing(_REJECT_PAYLOAD, "approved_by"),
        "approved_by: Field required",
        id="reject-missing-approved_by",
    ),
    pytest.param(
        _missing(_REJECT_PAYLOAD, "reason"),
        "reason: Field required",
        id="reject-missing-reason",
    ),
    pytest.param(
        {**_REJECT_PAYLOAD, "status": "cancelled"},
        "body: Input tag 'cancelled' found using 'status' does not match any of the expected tags",
        id="invalid-status-falls-back-to-body-on-empty-loc",
    ),
]


class DescribeValidateReview:
    def it_parses_a_valid_approve_payload_into_approve_review(self) -> None:
        review = validate_review(_body(_APPROVE_PAYLOAD))

        assert isinstance(review, ApproveReview)
        assert review.reason == "looks good"

    def it_parses_a_valid_reject_payload_into_reject_review(self) -> None:
        review = validate_review(_body(_REJECT_PAYLOAD))

        assert isinstance(review, RejectReview)
        assert review.approved_by == "reviewer@example.com"

    @pytest.mark.parametrize("payload, expected_message_fragment", _INVALID_REVIEW_CASES)
    def it_raises_review_invalid_with_a_message_naming_the_offending_field(
        self, payload: dict, expected_message_fragment: str
    ) -> None:
        with pytest.raises(ReviewInvalid) as exc_info:
            validate_review(_body(payload))

        assert expected_message_fragment in str(exc_info.value)
