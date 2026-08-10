"""E2E happy path — auto-approve, real Groq, traceable (E2E-01, E2E-02).

POST a fresh, low-value, unambiguous item through the real `api` container,
let the real `publisher`/`reimbursement` chain decide it, then confirm both
the persisted decision and its LangFuse trace — the concrete proxy for
CLAUDE.md's full-traceability requirement holding end to end.
"""

from uuid import uuid4

import httpx
import pytest
from langfuse import Langfuse
from langfuse_helper import trace_exists_for_session
from payload_builders import approve_bucket_payload
from polling import find_uuid_by_request_id, wait_for_status

pytestmark = pytest.mark.e2e

_FIND_UUID_TIMEOUT_SECONDS = 30.0
_DECISION_TIMEOUT_SECONDS = 90.0
_TRACE_TIMEOUT_SECONDS = 30.0


class DescribeTheHappyPath:
    # e2e_env not type-hinted as conftest.E2EEnv: every service's own
    # tests/ dir is on the shared pythonpath list (pyproject.toml) under
    # the same bare module name "conftest" — `from conftest import ...`
    # resolves to whichever one sys.modules cached first, not necessarily
    # this suite's own tests/e2e/conftest.py.
    def it_reaches_auto_approved_status_with_a_traceable_langfuse_trace(
        self, api_client: httpx.Client, e2e_env: object
    ) -> None:
        request_id = f"E2E-APPROVE-{uuid4().hex[:12]}"

        response = api_client.post("/api/v1/reimbursement", json=[approve_bucket_payload(request_id)])
        assert response.status_code == 201

        uuid = find_uuid_by_request_id(api_client, request_id, timeout=_FIND_UUID_TIMEOUT_SECONDS)
        item = wait_for_status(api_client, uuid, {"auto-approved"}, timeout=_DECISION_TIMEOUT_SECONDS)
        assert item["decision_reason"] is not None

        langfuse_client = Langfuse(
            public_key=e2e_env.langfuse_public_key,
            secret_key=e2e_env.langfuse_secret_key,
            host=e2e_env.langfuse_host,
        )
        assert trace_exists_for_session(langfuse_client, str(uuid), timeout=_TRACE_TIMEOUT_SECONDS)
