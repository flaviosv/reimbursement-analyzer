import asyncio
import json
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from secrets import token_hex
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import asyncpg
import httpx
from dependencies import get_pool
from errors import register_handlers
from fastapi import APIRouter, FastAPI

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


def valid_reimbursement_item(request_id: str = "REQ-0001", **extra: object) -> dict:
    """The canonical minimal-valid POST /api/v1/reimbursement item shape —
    a single source of truth for test_validation.py, test_route.py, and
    test_integration.py, which each need a slightly different usage pattern
    (a fixed dict vs. a request_id-keyed factory) but were previously
    maintaining three independently hand-copied versions of this shape."""
    return {
        "request_id": request_id,
        "submitted_by": "person@example.com",
        "submitted_at": "2026-01-01T12:00:00Z",
        **extra,
    }


def valid_approve_payload(**overrides: object) -> dict:
    """The canonical minimal-valid PUT /api/v1/reimbursement/{uuid} approve
    body -- a single source of truth for test_route.py and
    test_validation.py, which each need a slightly different usage pattern
    (a fixed dict used as-is vs. one mutated per parametrize case) but were
    previously maintaining two independently hand-copied versions of this
    shape."""
    return {
        "status": "approved",
        "reason": "looks good",
        "receipts_date": "2026-01-05",
        "receipts_value": "50.00",
        "receipts_currency": "BRL",
        "approved_by": "reviewer@example.com",
        **overrides,
    }


def valid_reject_payload(**overrides: object) -> dict:
    """The canonical minimal-valid PUT /api/v1/reimbursement/{uuid} reject
    body -- same rationale as valid_approve_payload above."""
    return {
        "status": "rejected",
        "reason": "missing evidence",
        "approved_by": "reviewer@example.com",
        **overrides,
    }


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
    """Row-level seed shared by reimbursement/list and reimbursement/update's
    own route tests (AD-009 rules out a cross-conftest import, see FakePool
    below) — both need a reimbursement row present without going through the
    real POST/PUT flow, just at different states of it."""
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
    receipts_value: Decimal = Decimal("100.00"),
    receipts_date: date = date(2026, 1, 1),
    currency: str = "BRL",
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


class _FakePoolAcquisition:
    def __init__(self, pool: FakePool) -> None:
        self.pool = pool

    async def __aenter__(self) -> asyncpg.Connection:
        if self.pool.acquire_error is not None:
            raise self.pool.acquire_error
        await self.pool.lock.acquire()
        return self.pool.connection

    async def __aexit__(self, *exc_info: object) -> bool:
        self.pool.lock.release()
        return False


class FakePool:
    """Hands out the one real connection a `db` fixture owns, so route-level
    tests exercise genuine Postgres query/transaction semantics instead of a
    fake's own idea of them — adapted from
    `publisher/tests/fakes.py::RealPool`'s "real connection behind a lock"
    pattern, pool-shaped so `Depends(get_pool)` can be overridden with it
    directly. Reachable by bare name via the workspace `pythonpath` (like
    `valid_reimbursement_item` above), not a conftest fixture, so both
    `reimbursement/list/` and `reimbursement/update/` test packages can reuse
    the same class without duplicating it (AD-009 rules out a cross-conftest
    import).

    `acquire_error`, when set, is raised on every `acquire()` instead of
    yielding the connection — how a route test forces a genuine 500 without
    an actually broken database."""

    def __init__(self, connection: asyncpg.Connection, *, acquire_error: Exception | None = None) -> None:
        self.connection = connection
        self.acquire_error = acquire_error
        self.lock = asyncio.Lock()

    def acquire(self, *, timeout: float | None = None) -> _FakePoolAcquisition:
        return _FakePoolAcquisition(self)


def _build_client(router: APIRouter, pool: FakePool) -> httpx.AsyncClient:
    """A throwaway FastAPI() + register_handlers() + `router`, backed by
    `pool` — shared by `reimbursement/list/test_route.py` and
    `reimbursement/update/test_route.py`'s own route tests (AD-009 rules out
    a cross-conftest import, see FakePool above), which each wire a
    different router into an otherwise identical app.

    httpx.AsyncClient + ASGITransport, not TestClient: TestClient drives the
    ASGI app from a separate thread with its own event loop, and the real
    asyncpg connection FakePool wraps is bound to *this* test's own loop
    (the `db` fixture's) -- a cross-loop connection use asyncpg rejects
    outright. AsyncClient runs the app in-process on the current loop.
    """
    app = FastAPI()
    register_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_pool] = lambda: pool
    # raise_app_exceptions=False: otherwise ASGITransport re-raises an
    # unhandled exception into the test instead of returning the registered
    # Exception handler's 500 response -- the async-client mirror of
    # TestClient's raise_server_exceptions=False (see test_errors.py).
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")
