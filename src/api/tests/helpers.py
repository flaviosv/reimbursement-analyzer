import asyncio

import asyncpg
import httpx
from api.dependencies import get_pool
from api.errors import register_handlers
from fastapi import APIRouter, FastAPI


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
    directly. Reachable by bare name via the workspace `pythonpath`, not a
    conftest fixture, so both `reimbursement/list/` and
    `reimbursement/update/` test packages can reuse
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
