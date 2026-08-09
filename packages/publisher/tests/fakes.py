"""Test doubles shared by the publisher's test modules.

A module rather than conftest fixtures because these are classes tests
construct with arguments, not resources pytest manages. Reachable by bare
name through the root pyproject's `pythonpath`, the same mechanism
`src/api/tests/helpers.py` already uses.
"""

import asyncio
import json
from types import TracebackType
from typing import Any, Self
from uuid import UUID, uuid4

import asyncpg


class FakeProducer:
    """Records every produced message. `errors` maps a topic to the exception
    its delivery future carries, so a Reimbursement failure and a requeue
    failure can be injected independently."""

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
        self.pool.in_flight += 1
        self.pool.max_in_flight = max(self.pool.max_in_flight, self.pool.in_flight)
        return FakeConnection(self.pool)

    async def __aexit__(self, *exc_info: object) -> bool:
        self.pool.in_flight -= 1
        return False


class _NullTransaction:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> bool:
        return False


class FakeConnection:
    def __init__(self, pool: "FakePool") -> None:
        self.pool = pool

    def transaction(self) -> _NullTransaction:
        return _NullTransaction()

    async def fetchval(self, statement: str, *args: Any) -> UUID:
        return await self.pool.insert(args)


class FakePool:
    """Stands in for an asyncpg pool. `insert_errors` fails the insert of the
    named request_ids only, so per-item independence can be exercised; the
    in-flight counters record how many items hold a connection at once.

    `insert_turns` holds the named request_ids for that many extra event-loop
    turns, which reorders completion deterministically without a clock;
    `insert_delay_seconds` gives every item the same real delay, so the shape
    of the fan-out (concurrent vs. serialised) becomes observable."""

    def __init__(
        self,
        *,
        insert_errors: dict[str, Exception] | None = None,
        insert_turns: dict[str, int] | None = None,
        insert_delay_seconds: float = 0.0,
        acquire_error: Exception | None = None,
    ) -> None:
        self.insert_errors = insert_errors or {}
        self.insert_turns = insert_turns or {}
        self.insert_delay_seconds = insert_delay_seconds
        self.acquire_error = acquire_error
        self.inserted: list[tuple[Any, ...]] = []
        self.acquisitions = 0
        self.in_flight = 0
        self.max_in_flight = 0

    def acquire(self, *, timeout: float | None = None) -> _FakeAcquisition:
        return _FakeAcquisition(self)

    async def insert(self, args: tuple[Any, ...]) -> UUID:
        await asyncio.sleep(self.insert_delay_seconds)
        for _ in range(self.insert_turns.get(args[0], 0)):
            await asyncio.sleep(0)
        error = self.insert_errors.get(args[0])
        if error is not None:
            raise error
        self.inserted.append(args)
        return uuid4()


class _RealAcquisition:
    def __init__(self, pool: "RealPool") -> None:
        self.pool = pool

    async def __aenter__(self) -> asyncpg.Connection:
        await self.pool.lock.acquire()
        return self.pool.connection

    async def __aexit__(self, *exc_info: object) -> bool:
        self.pool.lock.release()
        return False


class RealPool:
    """Hands out the one real connection the `db` fixture owns, so the
    rollback, duplicate and escalation branches run against real asyncpg
    transaction semantics instead of a fake's own idea of them.

    The lock makes it a pool of exactly one: asyncpg rejects concurrent
    operations on a single connection, which a real pool never issues because
    every waiter gets its own. Callers still wait for a slot rather than
    failing, so the fan-out under test is unchanged."""

    def __init__(self, connection: asyncpg.Connection) -> None:
        self.connection = connection
        self.lock = asyncio.Lock()

    def acquire(self, *, timeout: float | None = None) -> _RealAcquisition:
        return _RealAcquisition(self)
