"""Test doubles for the agent's own test modules.

A module rather than conftest fixtures because these are classes tests
construct with arguments, not resources pytest manages — same reasoning as
`src/publisher/tests/fakes.py`. Reachable by bare name through the root
pyproject's `pythonpath`.

Named `agent_fakes`, not `fakes`: `src/publisher/tests` and `src/agent/tests`
are both bare-name pythonpath roots, and publisher already owns the name
`fakes` — first-match-wins on sys.path would otherwise silently resolve
this module's imports to publisher's own doubles instead (discovered via a
collection-time TypeError, not a subtler bug, but avoided here by naming).

No in-flight/max-in-flight counters, unlike publisher's FakePool: this
feature has no per-message fan-out to observe. No RealPool: agent's
validation.py wraps no `conn.transaction()`, so there is no transaction
semantics a fake could approximate imperfectly — the real-Postgres proof
lives entirely in the integration test.

`FakeProducer` itself is re-exported from `shared.testing`, not redefined
here: it doubles a contract (`AIOProducer.produce()`) `shared.producer`
already owns, reachable by a normal package import with no bare-name
collision risk — unlike `FakePool`/`FakeConnection`, which are genuinely
agent-specific.
"""

from typing import Any
from uuid import UUID

from shared.testing import FakeProducer

__all__ = [
    "FakeProducer",
    "FakePool",
    "FakeConnection",
    "FakeStructuredModel",
    "FakeApplyDecision",
]


class _FakeAcquisition:
    def __init__(self, pool: "FakePool") -> None:
        self.pool = pool

    async def __aenter__(self) -> "FakeConnection":
        self.pool.acquisitions += 1
        if self.pool.acquire_error is not None:
            raise self.pool.acquire_error
        return FakeConnection(self.pool)

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class FakeConnection:
    def __init__(self, pool: "FakePool") -> None:
        self.pool = pool

    async def fetchrow(self, statement: str, *args: Any) -> dict[str, Any] | None:
        return await self.pool.get(args[0])

    async def execute(self, statement: str, *args: Any) -> str:
        return await self.pool.update(args[0], args[1], args[2])


class FakePool:
    """Stands in for an asyncpg pool across the two statements agent code
    issues: a read (`get_by_uuid`) and a status update (`update_decision`,
    including via `escalate_existing`).

    `rows` maps `uuid -> a dict standing in for an asyncpg.Record` (absent or
    `None` means a ghost — no matching row). `get_errors`/`update_errors` map
    `uuid -> exception`, to inject a transient DB failure independently of
    the ghost case.
    """

    def __init__(
        self,
        *,
        rows: dict[UUID, dict[str, Any]] | None = None,
        get_errors: dict[UUID, Exception] | None = None,
        update_errors: dict[UUID, Exception] | None = None,
        acquire_error: Exception | None = None,
    ) -> None:
        self.rows = dict(rows or {})
        self.get_errors = get_errors or {}
        self.update_errors = update_errors or {}
        self.acquire_error = acquire_error
        self.updated: dict[UUID, str] = {}
        self.acquisitions = 0

    def acquire(self, *, timeout: float | None = None) -> _FakeAcquisition:
        return _FakeAcquisition(self)

    async def get(self, uuid: UUID) -> dict[str, Any] | None:
        error = self.get_errors.get(uuid)
        if error is not None:
            raise error
        return self.rows.get(uuid)

    async def update(self, uuid: UUID, status: str, reason: str) -> str:
        error = self.update_errors.get(uuid)
        if error is not None:
            raise error
        if uuid not in self.rows:
            return "UPDATE 0"
        self.updated[uuid] = reason
        self.rows[uuid] = {**self.rows[uuid], "status": status, "decision_reason": reason}
        return "UPDATE 1"


class FakeStructuredModel:
    """Stands in for an Ollama chat model bound via `.with_structured_output`
    (T8/T11's `ExtractFields`/`Analysis` constructor dependency) — returns a
    fixed structured result (or raises) from `ainvoke`, and records every
    call it received, so a test can assert exactly-one-invocation without a
    real Ollama call."""

    def __init__(self, result: Any = None, *, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[Any] = []

    async def ainvoke(self, input: Any, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(input)
        if self.error is not None:
            raise self.error
        return self.result


class FakeApplyDecision:
    """Stands in for `shared.reimbursement.use_cases.apply_decision.apply_decision`
    — injected directly into `ApplyPolicies`/`ApplyAgentDecision` (Design's
    constructor-injection shape), so a test proves the dependency was called
    with the right arguments without monkeypatching a module import."""

    def __init__(self, result: UUID | None) -> None:
        self.result = result
        self.calls: list[tuple[Any, UUID, str, str]] = []

    async def __call__(self, conn: Any, uuid: UUID, status: str, decision_reason: str) -> UUID | None:
        self.calls.append((conn, uuid, status, decision_reason))
        return self.result
