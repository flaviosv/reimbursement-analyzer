"""GET-polling helper for tests/e2e's scenario tests (Phase 3) — waits for a
reimbursement row to reach a terminal status through the real api container,
adapted from test_integration.py's `_run_agent` bounded-poll-with-deadline
style (there it polls a Kafka committed offset; here it polls an HTTP GET)."""

import time
from uuid import UUID

import httpx

_POLL_INTERVAL_SECONDS = 1.0


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
