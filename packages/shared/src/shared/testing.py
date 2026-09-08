"""Test doubles, DB seed helpers, and generic cross-service test-provisioning
utilities, importable by any service's own test suite via a normal package
import — not bare-name pythonpath resolution, so there is nothing to
collide with.

Production code never imports this module.
"""

import asyncio
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from secrets import token_hex
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import asyncpg
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer
from prometheus_client import Counter, Gauge, Histogram


class ThreadSafeAsyncEvent:
    """A `threading.Event` exposed with an async-compatible `.wait()` — for
    fake sync producers that set it from a `ThreadPoolExecutor` worker thread
    (where `shared.producer.publish` drives `produce()`), while test code
    awaits it from the event loop thread. `wait_sync` is for the reverse
    direction: a worker thread blocking on it directly, without reaching
    into the private `threading.Event` itself."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def set(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await asyncio.to_thread(self._event.wait)

    def wait_sync(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout=timeout)

# Matches the postgres service in docker-compose.yml, so tests exercise the
# same major version the stack runs.
POSTGRES_IMAGE = "postgres:18"
# Dropping and creating a database needs a session that is not attached to it;
# 'postgres' always exists on the server.
MAINTENANCE_DATABASE = "postgres"


def database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def with_database(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path=f"/{name}"))


def maintenance_url(url: str) -> str:
    return with_database(url, MAINTENANCE_DATABASE)


def disposable_database_name() -> str:
    """A database name no concurrent run can collide with.

    The suite drops its database WITH (FORCE), which terminates whatever
    backends are attached. Under a shared constant name that is not a race but
    mutual destruction -- two runs against one server tear each other down
    mid-assertion -- and pytest-xdist cannot work at all.
    """
    return f"reimbursementanalyzer_{os.getpid()}_{token_hex(4)}_test"


def guard_is_test_database(url: str) -> None:
    name = database_name(url)
    if not name.endswith("_test"):
        raise RuntimeError(
            f"refusing to run against database {name!r}: the test suite drops "
            "and recreates its database, so the name must end in '_test'"
        )


def in_memory_tracer() -> tuple[Tracer, InMemorySpanExporter]:
    """A throwaway `TracerProvider` wired to an `InMemorySpanExporter` via
    `SimpleSpanProcessor` (synchronous — spans are visible immediately, no
    batching delay), for tests that assert on finished span attributes.
    Previously redefined independently in `shared`, `publisher`, and
    `reimbursement`'s own test suites."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer(__name__), exporter


def metric_value(metric: Counter | Gauge, *labels: str) -> float:
    """Current value of a Counter or Gauge — or, if `labels` is given, of one
    of its label children — read from the same public `.collect()` sample
    `/metrics` itself serializes, instead of the private `._value` internal.
    Previously hand-copied as `<metric>.labels(...)._value.get()` across
    every service's metrics tests."""
    child = metric.labels(*labels) if labels else metric
    (family,) = child.collect()
    (sample,) = (s for s in family.samples if not s.name.endswith("_created"))
    return sample.value


def histogram_sample_count(histogram: Histogram, *labels: str) -> float:
    """Total number of `.observe()` calls recorded so far on a Histogram —
    or, if `labels` is given, on one of its label children — read from the
    same public `_count` sample `/metrics` itself serializes, instead of
    summing the private per-bucket `_buckets` internals. Previously
    duplicated (as `_histogram_count`/`_observation_count`) across four
    services' own metrics tests."""
    child = histogram.labels(*labels) if labels else histogram
    (family,) = child.collect()
    (count_sample,) = (s for s in family.samples if s.name.endswith("_count"))
    return count_sample.value


def histogram_sample_sum(histogram: Histogram, *labels: str) -> float:
    """Sum of every value `.observe()`d so far on a Histogram — or, if
    `labels` is given, on one of its label children — read from the public
    `_sum` sample `/metrics` itself serializes."""
    child = histogram.labels(*labels) if labels else histogram
    (family,) = child.collect()
    (sum_sample,) = (s for s in family.samples if s.name.endswith("_sum"))
    return sum_sample.value


def valid_reimbursement_item(request_id: str = "REQ-0001", **extra: object) -> dict:
    """The canonical minimal-valid POST /api/v1/reimbursement item shape —
    a single source of truth across api's, publisher's, and reimbursement's
    own test suites, which each need a slightly different usage pattern
    (a fixed dict vs. a request_id-keyed factory) but previously maintained
    independently hand-copied versions of this shape."""
    return {
        "request_id": request_id,
        "submitted_by": "person@example.com",
        "submitted_at": "2026-01-01T12:00:00Z",
        **extra,
    }


class _FakeSyncProducer:
    """The synchronous half of `FakeProducer` — models
    `confluent_kafka.Producer`'s `produce()`/`flush()` pair, the shape
    `shared.producer.publish` drives directly via `._producer`/`.executor`.
    `threading.local()` mirrors `publish()`'s own per-call isolation:
    produce() and flush() for one logical call always run on the same
    thread."""

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
        if self._outer.pending:
            # Models "still queued" after the timeout elapses: the delivery
            # callback never fires, so `shared.producer.publish`'s delivery
            # future never resolves and its own `asyncio.wait_for` times out
            # — the one documented failure path `errors=` alone can't reach.
            return 1
        on_delivery = getattr(self._local, "on_delivery", None)
        error = getattr(self._local, "error", None)
        if on_delivery is not None:
            on_delivery(error, object())
        return 0


class FakeProducer:
    """Stands in for `confluent_kafka.aio.AIOProducer`, the one
    `shared.producer.publish` and every caller depend on. Records every
    produced message. `errors` maps a topic to the exception its delivery
    callback carries, so a delivery failure can be injected independently
    per topic. `pending=True` instead models a delivery that never
    completes at all, for the "still queued" timeout branch."""

    def __init__(self, *, errors: dict[str, Exception] | None = None, pending: bool = False) -> None:
        self.errors = errors or {}
        self.pending = pending
        self.produced: list[tuple[str, bytes]] = []
        self._producer = _FakeSyncProducer(self)
        self.executor = ThreadPoolExecutor(max_workers=4)

    def messages(self, topic: str) -> list[dict[str, Any]]:
        return [json.loads(value) for produced, value in self.produced if produced == topic]


_SEED_REIMBURSEMENT = """
    INSERT INTO reimbursement (uuid, request_id, original_payload, status, created_at)
    VALUES ($1, $2, $3::text::jsonb, $4, $5)
"""

_SEED_REIMBURSEMENT_WITH_RECEIPTS = """
    INSERT INTO reimbursement (
        uuid, request_id, original_payload, status, receipts_value, receipts_date, currency
    )
    VALUES ($1, $2, '{}'::jsonb, $3, $4, $5, $6)
"""

_SEED_HUMAN_REVIEW = """
    INSERT INTO human_review (uuid, reimbursement_uuid, status, reviewed_by, reason, created_at)
    VALUES ($1, $2, $3, $4, $5, $6)
"""


async def seed_reimbursement(
    db: asyncpg.Connection,
    request_id: str,
    *,
    status: str = "human-review",
    created_at: datetime | None = None,
    original_payload: dict[str, Any] | None = None,
) -> UUID:
    """Row-level seed shared across every service's own test suite — each
    needs a reimbursement row present at a given status without going
    through the real insert/approve flow."""
    uuid = uuid4()
    await db.execute(
        _SEED_REIMBURSEMENT,
        uuid,
        request_id,
        json.dumps(original_payload or {}),
        status,
        created_at or datetime.now(UTC),
    )
    return uuid


async def seed_reimbursement_with_receipts(
    db: asyncpg.Connection,
    request_id: str,
    *,
    status: str = "human-review",
    receipts_value: Decimal | None = Decimal("100.00"),
    receipts_date: date | None = date(2026, 1, 1),
    currency: str | None = "BRL",
) -> UUID:
    uuid = uuid4()
    await db.execute(
        _SEED_REIMBURSEMENT_WITH_RECEIPTS, uuid, request_id, status, receipts_value, receipts_date, currency
    )
    return uuid


async def seed_human_review(
    db: asyncpg.Connection,
    reimbursement_uuid: UUID,
    *,
    status: str = "approved",
    reviewed_by: str = "reviewer@example.com",
    reason: str = "looks good",
    created_at: datetime | None = None,
) -> None:
    await db.execute(
        _SEED_HUMAN_REVIEW,
        uuid4(),
        reimbursement_uuid,
        status,
        reviewed_by,
        reason,
        created_at or datetime.now(UTC),
    )
