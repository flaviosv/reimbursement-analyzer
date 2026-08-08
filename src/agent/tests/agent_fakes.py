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
"""

import asyncio
import json
from typing import Any
from uuid import UUID


class FakeProducer:
    """Records every produced message. `errors` maps a topic to the
    exception its delivery future carries, so a requeue failure can be
    injected independently of everything else."""

    def __init__(self, *, errors: dict[str, Exception] | None = None) -> None:
        self.errors = errors or {}
        self.produced: list[tuple[str, bytes]] = []

    async def produce(self, topic: str, value: bytes, **kwargs: object) -> asyncio.Future:
        await asyncio.sleep(0)
        self.produced.append((topic, value))
        future = asyncio.get_running_loop().create_future()
        error = self.errors.get(topic)
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(object())
        return future

    def messages(self, topic: str) -> list[dict[str, Any]]:
        return [json.loads(value) for produced, value in self.produced if produced == topic]


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
        return await self.pool.update(args[0], args[1])


class FakePool:
    """Stands in for an asyncpg pool across the two statements agent code
    issues: a read (`get_by_uuid`) and a status update (`update_human_review`,
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

    def acquire(self) -> _FakeAcquisition:
        return _FakeAcquisition(self)

    async def get(self, uuid: UUID) -> dict[str, Any] | None:
        error = self.get_errors.get(uuid)
        if error is not None:
            raise error
        return self.rows.get(uuid)

    async def update(self, uuid: UUID, reason: str) -> str:
        error = self.update_errors.get(uuid)
        if error is not None:
            raise error
        if uuid not in self.rows:
            return "UPDATE 0"
        self.updated[uuid] = reason
        self.rows[uuid] = {**self.rows[uuid], "status": "human-review", "decision_reason": reason}
        return "UPDATE 1"
