"""E2E human-review path + PUT approve/reject resolution — real Groq
(E2E-04, E2E-05, E2E-06).

Drives the one branch touching both LLM nodes (extract_fields + analysis's
guardrail) and a second, separate API call (PUT) — landing, then approving,
then rejecting, each on its own fresh item (distinct request_ids).
"""

import time
from uuid import UUID, uuid4

import httpx
import pytest
from payload_builders import human_review_bucket_payload
from polling import find_uuid_by_request_id, wait_for_status

pytestmark = pytest.mark.e2e

_FIND_UUID_TIMEOUT_SECONDS = 30.0
_DECISION_TIMEOUT_SECONDS = 90.0
# Spaces out this file's 3 distinct item-creation POSTs so their real Groq
# calls (extract_fields + analysis, per item) don't all fire back-to-back
# against the 30 req/min ceiling.
_INTER_ITEM_DELAY_SECONDS = 2.0

_APPROVE_PAYLOAD = {
    "status": "approved",
    "reason": "receipt matches the claimed amount on manual review",
    "receipts_date": "2026-01-05",
    "receipts_value": "1000.00",
    "receipts_currency": "BRL",
    "approved_by": "reviewer@example.com",
}
_REJECT_PAYLOAD = {
    "status": "rejected",
    "reason": "receipt does not support the claimed amount",
    "approved_by": "reviewer@example.com",
}


def _post_and_reach_human_review(api_client: httpx.Client, request_id: str) -> UUID:
    response = api_client.post("/api/v1/reimbursement", json=[human_review_bucket_payload(request_id)])
    assert response.status_code == 201
    time.sleep(_INTER_ITEM_DELAY_SECONDS)

    uuid = find_uuid_by_request_id(api_client, request_id, timeout=_FIND_UUID_TIMEOUT_SECONDS)
    item = wait_for_status(api_client, uuid, {"human-review"}, timeout=_DECISION_TIMEOUT_SECONDS)
    assert item["decision_reason"] is not None
    return uuid


class DescribeHumanReview:
    def it_reaches_human_review_status_for_an_inconsistent_ambiguous_item(
        self, api_client: httpx.Client
    ) -> None:
        _post_and_reach_human_review(api_client, f"E2E-HUMANREVIEW-{uuid4().hex[:12]}")

    def it_becomes_human_approved_after_a_put_approval(self, api_client: httpx.Client) -> None:
        uuid = _post_and_reach_human_review(api_client, f"E2E-HUMANREVIEW-APPROVE-{uuid4().hex[:12]}")

        response = api_client.put(f"/api/v1/reimbursement/{uuid}", json=_APPROVE_PAYLOAD)
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "human-approved"

    def it_becomes_human_rejected_after_a_put_rejection(self, api_client: httpx.Client) -> None:
        uuid = _post_and_reach_human_review(api_client, f"E2E-HUMANREVIEW-REJECT-{uuid4().hex[:12]}")

        response = api_client.put(f"/api/v1/reimbursement/{uuid}", json=_REJECT_PAYLOAD)
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "human-rejected"
