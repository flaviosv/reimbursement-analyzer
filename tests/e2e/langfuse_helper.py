"""Thin polling wrapper around LangFuse's trace-by-session-id lookup —
the concrete proxy for the traceability requirement (CLAUDE.md) holding
end to end for a real decision. Exercised by tests/e2e/test_happy_path.py
(Phase 3).

Accessor chain confirmed against the pinned langfuse==4.14.3 SDK (matches
packages/reimbursement/pyproject.toml's `langfuse>=4.14.3` /
uv.lock's resolved 4.14.3) — SPEC_DEVIATION from design.md's own
Context7-verified plan, `Langfuse(...).api.trace.list(session_id=...)`:
that call is SDK-valid but 404s against this project's actual running
LangFuse v4 stack (docker-compose.yml's `langfuse/langfuse:4` image runs
in "events_only" mode), confirmed by manually invoking it against the
real stack — both `api.trace.list(session_id=...)` and
`api.sessions.get(session_id=...)` return the same 404 body:
'This endpoint is not available on deployments running in Langfuse v4
events_only mode ... read span and trace data via
GET /api/public/v2/observations?filter=<urlencoded sessionId
filter>&fromStartTime=<from>&toStartTime=<to>' (langfuse's own
deprecation notice, reproduced verbatim from the live response).
Reason: the durable/functional proxy for "this decision is traceable"
must actually work against the deployment this project ships, not just
be valid per the SDK's Python type signatures.

The v2 replacement the deployment itself names —
`Langfuse(...).api.observations.get_many(filter=...)`, a JSON array of
filter conditions with `sessionId` as a documented filter column
(.venv/lib/python3.14/site-packages/langfuse/api/observations/client.py) —
was manually verified against the real running stack, returning 3
observations for an existing auto-approved reimbursement's uuid."""

import json
import time

from langfuse import Langfuse

_POLL_INTERVAL_SECONDS = 1.0


def trace_exists_for_session(client: Langfuse, session_id: str, timeout: float) -> bool:
    """Poll LangFuse's v2 observations endpoint, filtered to `session_id`,
    until at least one observation appears or `timeout` elapses."""
    session_filter = json.dumps(
        [{"column": "sessionId", "operator": "=", "value": session_id, "type": "string"}]
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        observations = client.api.observations.get_many(filter=session_filter)
        if observations.data:
            return True
        time.sleep(_POLL_INTERVAL_SECONDS)
    return False
