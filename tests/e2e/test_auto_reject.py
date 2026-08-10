"""E2E auto-reject path — real Groq (E2E-03).

POST an item whose extracted receipt date is well over 90 days before
submission, and confirm the real chain lands it on `auto-rejected` with a
non-null `decision_reason`.
"""

from uuid import uuid4

import httpx
import pytest
from payload_builders import reject_bucket_payload
from polling import find_uuid_by_request_id, wait_for_status

pytestmark = pytest.mark.e2e

_FIND_UUID_TIMEOUT_SECONDS = 30.0
_DECISION_TIMEOUT_SECONDS = 90.0


class DescribeAutoReject:
    def it_reaches_auto_rejected_status_for_a_stale_receipt(self, api_client: httpx.Client) -> None:
        request_id = f"E2E-REJECT-{uuid4().hex[:12]}"

        response = api_client.post("/api/v1/reimbursement", json=[reject_bucket_payload(request_id)])
        assert response.status_code == 201

        uuid = find_uuid_by_request_id(api_client, request_id, timeout=_FIND_UUID_TIMEOUT_SECONDS)
        item = wait_for_status(api_client, uuid, {"auto-rejected"}, timeout=_DECISION_TIMEOUT_SECONDS)
        assert item["decision_reason"] is not None
