"""GET-polling helpers for tests/e2e's scenario tests (Phase 3) — waits for a
reimbursement row to reach a terminal status through the real api container,
adapted from test_integration.py's `_run_agent` bounded-poll-with-deadline
style (there it polls a Kafka committed offset; here it polls an HTTP GET)."""

import time
from uuid import UUID

import httpx

_POLL_INTERVAL_SECONDS = 1.0
# The route's own default (api/reimbursement/list/route.py) — plenty to
# contain a just-created row, since fetch_reimbursement_page orders
# `created_at DESC` and this suite only ever drives one item at a time.
_LIST_LIMIT = 100


def find_uuid_by_request_id(api_client: httpx.Client, request_id: str, timeout: float) -> UUID:
    """Poll GET /api/v1/reimbursement (no `status` filter, so `pending` rows
    are included too, per fetch_reimbursement_page's `$1::text[] IS NULL`
    branch) until an item with `request_id` appears, returning its uuid.

    SPEC_DEVIATION: design.md's sequence diagram assumed POST returns a
    directly usable uuid. The real route (api/reimbursement/create/route.py)
    returns only a MessageResponse (`{"msg": "N request(s) accepted"}`) —
    no uuid, per-item or otherwise, since the row is inserted later,
    asynchronously, by the publisher. This resolves the same uuid via the
    list endpoint's `request_id` field instead, which is unique per test
    (fresh per POST) and reliably the newest row for a sequential suite.
    Reason: this is the only HTTP-only way to learn the uuid without
    querying Postgres directly, which design.md's own Tech Decisions
    table rules out for this suite.
    """
    deadline = time.monotonic() + timeout
    last_seen_count = 0
    while time.monotonic() < deadline:
        response = api_client.get("/api/v1/reimbursement", params={"limit": _LIST_LIMIT})
        response.raise_for_status()
        items = response.json()["data"]
        last_seen_count = len(items)
        for item in items:
            if item["request_id"] == request_id:
                return UUID(item["uuid"])
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"no item with request_id={request_id!r} appeared within {timeout}s "
        f"({last_seen_count} items seen at last poll)"
    )


def wait_for_status(
    api_client: httpx.Client,
    uuid: UUID,
    expected_terminal_statuses: set[str],
    timeout: float,
) -> dict:
    """Poll GET /api/v1/reimbursement/:uuid until the row's `status` is in
    `expected_terminal_statuses` or `timeout` elapses. Returns the item dict
    (the response's `data` field) on success; raises AssertionError naming
    the last-seen status on timeout."""
    deadline = time.monotonic() + timeout
    last_status: str | None = None
    while time.monotonic() < deadline:
        response = api_client.get(f"/api/v1/reimbursement/{uuid}")
        response.raise_for_status()
        item = response.json()["data"]
        last_status = item["status"]
        if last_status in expected_terminal_statuses:
            return item
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"reimbursement {uuid} never reached one of {expected_terminal_statuses} "
        f"within {timeout}s (last-seen status: {last_status!r})"
    )
