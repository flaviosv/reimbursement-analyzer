"""Test doubles shared by the publisher's test modules.

A module rather than conftest fixtures because these are classes tests
construct with arguments, not resources pytest manages. Reachable by bare
name through the root pyproject's `pythonpath`, the same mechanism
`src/api/tests/helpers.py` already uses.
"""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import TracebackType
from typing import Any, Self
from uuid import UUID, uuid4

import asyncpg


class _FakeSyncProducer:
    """The synchronous half of `FakeProducer` — models
    `confluent_kafka.Producer`'s `produce()`/`flush()` pair, the shape
    `shared.producer.publish` now drives directly via `._producer`/
    `.executor`. `threading.local()` mirrors `publish()`'s own per-call
    isolation: produce() and flush() for one logical call always run on the
    same thread."""

    def __init__(self, outer: "FakeProducer") -> None:
        self._outer = outer
        self._local = threading.local()

    def produce(
        self,
        *,
        topic: str,
        value: bytes | None = None,
        headers: list[tuple[str, bytes]] | None = None,
        on_delivery: Any = None,
        **kwargs: object,
    ) -> None:
        self._outer.produced.append((topic, value))
        self._local.on_delivery = on_delivery
        self._local.error = self._outer.errors.get(topic)

    def flush(self, timeout: float) -> int:
        on_delivery = getattr(self._local, "on_delivery", None)
        error = getattr(self._local, "error", None)
        if on_delivery is not None:
            on_delivery(error, object())
        return 0


class FakeProducer:
    """Records every produced message. `errors` maps a topic to the exception
    its delivery callback carries, so a Reimbursement failure and a requeue
    failure can be injected independently."""

    def __init__(self, *, errors: dict[str, Exception] | None = None) -> None:
        self.errors = errors or {}
        self.produced: list[tuple[str, bytes]] = []
        self._producer = _FakeSyncProducer(self)
        self.executor = ThreadPoolExecutor(max_workers=4)

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

    async def execute(self, statement: str, *args: Any) -> str:
        # Only caller today is the compensating delete_pending().
        return self.pool.delete(args[0])


class FakePool:
    """Stands in for an asyncpg pool. `insert_errors` fails the insert of the
    named request_ids only, so per-item independence can be exercised; the
    in-flight counters record how many items hold a connection at once.

    `insert_turns` holds the named request_ids for that many extra event-loop
    turns, which reorders completion deterministically without a clock;
    `insert_delay_seconds` gives every item the same real delay, so the shape
    of the fan-out (concurrent vs. serialised) becomes observable.

    `delete_errors` fails the compensating delete of the named request_ids
    only (looked up by the uuid `delete_pending` is called with), mirroring
    `insert_errors`' shape for the delete side — lets a test force
    `ItemOutcome.REQUEUED` to still hold even when the compensating delete
    itself raises. A successful delete removes the matching entry from
    `inserted`, keeping that list an accurate "rows currently persisted in
    the fake store" — a failed delete leaves it untouched, matching a real
    DELETE that never actually removed the row."""

    def __init__(
        self,
        *,
        insert_errors: dict[str, Exception] | None = None,
        insert_turns: dict[str, int] | None = None,
        insert_delay_seconds: float = 0.0,
        acquire_error: Exception | None = None,
        delete_errors: dict[str, Exception] | None = None,
    ) -> None:
        self.insert_errors = insert_errors or {}
        self.insert_turns = insert_turns or {}
        self.insert_delay_seconds = insert_delay_seconds
        self.acquire_error = acquire_error
        self.delete_errors = delete_errors or {}
        self.inserted: list[tuple[Any, ...]] = []
        self._by_uuid: dict[UUID, tuple[Any, ...]] = {}
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
        uuid = uuid4()
        self._by_uuid[uuid] = args
        return uuid

    def delete(self, uuid: UUID) -> str:
        args = self._by_uuid.get(uuid)
        request_id = args[0] if args is not None else None
        error = self.delete_errors.get(request_id)
        if error is not None:
            raise error
        if args is not None:
            self.inserted.remove(args)
            del self._by_uuid[uuid]
        return "DELETE 1"


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
