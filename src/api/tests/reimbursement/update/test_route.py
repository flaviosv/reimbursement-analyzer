import asyncio
import logging
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import FakePool

from dependencies import get_pool
from errors import register_handlers
from main import app as real_app
from reimbursement.update.route import router

pytestmark = pytest.mark.anyio

_SEED_REIMBURSEMENT = """
    INSERT INTO reimbursement (uuid, request_id, original_payload, status)
    VALUES ($1, $2, '{}'::jsonb, $3)
"""

_SEED_REIMBURSEMENT_WITH_RECEIPTS = """
    INSERT INTO reimbursement (
        uuid, request_id, original_payload, status, receipts_value, receipts_date, currency
    )
    VALUES ($1, $2, '{}'::jsonb, $3, $4, $5, $6)
"""

_APPROVE_PAYLOAD = {
    "status": "approved",
    "reason": "looks good",
    "receipts_date": "2026-01-05",
    "receipts_value": "50.00",
    "receipts_currency": "BRL",
    "approved_by": "reviewer@example.com",
}

_REJECT_PAYLOAD = {
    "status": "rejected",
    "reason": "missing evidence",
    "approved_by": "reviewer@example.com",
}


async def _seed(db: asyncpg.Connection, request_id: str, *, status: str = "human-review") -> UUID:
    uuid = uuid4()
    await db.execute(_SEED_REIMBURSEMENT, uuid, request_id, status)
    return uuid


async def _seed_with_receipts(db: asyncpg.Connection, request_id: str, *, status: str = "human-review") -> UUID:
    uuid = uuid4()
    await db.execute(
        _SEED_REIMBURSEMENT_WITH_RECEIPTS, uuid, request_id, status, Decimal("100.00"), date(2026, 1, 1), "BRL"
    )
    return uuid


def _build_client(pool: FakePool) -> httpx.AsyncClient:
    # httpx.AsyncClient + ASGITransport, not TestClient: TestClient drives
    # the app from a separate thread with its own event loop, and the real
    # asyncpg connection FakePool wraps is bound to *this* test's own loop
    # (the `db` fixture's) — see reimbursement/list/test_route.py's own note.
    app = FastAPI()
    register_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_pool] = lambda: pool
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


class DescribePutReimbursement:
    async def it_approves_an_eligible_row_and_returns_200(self, db: asyncpg.Connection) -> None:
        uuid = await _seed(db, "REQ-PUT-APPROVE-OK")

        async with _build_client(FakePool(db)) as client:
            response = await client.put(f"/api/v1/reimbursement/{uuid}", json=_APPROVE_PAYLOAD)

        assert response.status_code == 200
        assert "msg" in response.json()
        status = await db.fetchval("SELECT status FROM reimbursement WHERE uuid = $1", uuid)
        assert status == "human-approved"

    async def it_returns_422_when_a_required_approve_field_is_missing(self, db: asyncpg.Connection) -> None:
        uuid = await _seed(db, "REQ-PUT-APPROVE-MISSING")
        payload = {k: v for k, v in _APPROVE_PAYLOAD.items() if k != "receipts_value"}

        async with _build_client(FakePool(db)) as client:
            response = await client.put(f"/api/v1/reimbursement/{uuid}", json=payload)

        assert response.status_code == 422
        assert response.json() == {"msg": "approved.receipts_value: Field required"}

    async def it_returns_422_for_a_malformed_receipts_currency(self, db: asyncpg.Connection) -> None:
        uuid = await _seed(db, "REQ-PUT-APPROVE-BAD-CURRENCY")
        payload = {**_APPROVE_PAYLOAD, "receipts_currency": "brl"}

        async with _build_client(FakePool(db)) as client:
            response = await client.put(f"/api/v1/reimbursement/{uuid}", json=payload)

        assert response.status_code == 422
        assert response.json() == {"msg": "approved.receipts_currency: String should match pattern '^[A-Z]{3}$'"}
        status = await db.fetchval("SELECT status FROM reimbursement WHERE uuid = $1", uuid)
        assert status == "human-review"

    async def it_approves_an_auto_rejected_row_overturning_the_rejection(
        self, db: asyncpg.Connection
    ) -> None:
        # AD-027 §1 (.specs/STATE.md) + spec.md's P1 "Approve a rejected or
        # human-review reimbursement" story: overturning a rejection into
        # an approval is intentional, not an ELIGIBLE_STATUSES bug.
        uuid = await _seed(db, "REQ-PUT-APPROVE-OVERTURN", status="auto-rejected")

        async with _build_client(FakePool(db)) as client:
            response = await client.put(f"/api/v1/reimbursement/{uuid}", json=_APPROVE_PAYLOAD)

        assert response.status_code == 200
        status = await db.fetchval("SELECT status FROM reimbursement WHERE uuid = $1", uuid)
        assert status == "human-approved"

    async def it_returns_400_when_approving_an_ineligible_status(self, db: asyncpg.Connection) -> None:
        uuid = await _seed(db, "REQ-PUT-APPROVE-INELIGIBLE", status="human-approved")

        async with _build_client(FakePool(db)) as client:
            response = await client.put(f"/api/v1/reimbursement/{uuid}", json=_APPROVE_PAYLOAD)

        assert response.status_code == 400
        assert response.json() == {"msg": f"reimbursement {uuid} is not eligible for this decision"}

    async def it_rejects_an_eligible_complete_row_and_returns_200(self, db: asyncpg.Connection) -> None:
        uuid = await _seed_with_receipts(db, "REQ-PUT-REJECT-OK")

        async with _build_client(FakePool(db)) as client:
            response = await client.put(f"/api/v1/reimbursement/{uuid}", json=_REJECT_PAYLOAD)

        assert response.status_code == 200
        status = await db.fetchval("SELECT status FROM reimbursement WHERE uuid = $1", uuid)
        assert status == "human-rejected"

    async def it_returns_422_when_approved_by_is_missing_on_reject(self, db: asyncpg.Connection) -> None:
        uuid = await _seed_with_receipts(db, "REQ-PUT-REJECT-MISSING")
        payload = {k: v for k, v in _REJECT_PAYLOAD.items() if k != "approved_by"}

        async with _build_client(FakePool(db)) as client:
            response = await client.put(f"/api/v1/reimbursement/{uuid}", json=payload)

        assert response.status_code == 422
        assert response.json() == {"msg": "rejected.approved_by: Field required"}

    async def it_returns_404_for_an_unknown_uuid(self, db: asyncpg.Connection) -> None:
        unknown_uuid = uuid4()

        async with _build_client(FakePool(db)) as client:
            response = await client.put(f"/api/v1/reimbursement/{unknown_uuid}", json=_APPROVE_PAYLOAD)

        assert response.status_code == 404
        assert response.json() == {"msg": f"no reimbursement with uuid {unknown_uuid}"}

    async def it_returns_400_when_rejecting_an_ineligible_status(self, db: asyncpg.Connection) -> None:
        uuid = await _seed_with_receipts(db, "REQ-PUT-REJECT-INELIGIBLE", status="human-approved")

        async with _build_client(FakePool(db)) as client:
            response = await client.put(f"/api/v1/reimbursement/{uuid}", json=_REJECT_PAYLOAD)

        assert response.status_code == 400
        assert response.json() == {"msg": f"reimbursement {uuid} is not eligible for this decision"}
        status = await db.fetchval("SELECT status FROM reimbursement WHERE uuid = $1", uuid)
        assert status == "human-approved"

    async def it_returns_400_when_rejecting_an_incomplete_entity(self, db: asyncpg.Connection) -> None:
        uuid = await _seed(db, "REQ-PUT-REJECT-INCOMPLETE")

        async with _build_client(FakePool(db)) as client:
            response = await client.put(f"/api/v1/reimbursement/{uuid}", json=_REJECT_PAYLOAD)

        assert response.status_code == 400
        assert response.json() == {"msg": f"reimbursement {uuid} is not eligible for this decision"}

    async def it_re_rejects_an_already_human_rejected_row(self, db: asyncpg.Connection) -> None:
        # AD-027 §1 (.specs/STATE.md) + spec.md's P1 "Reject a reimbursement
        # under review" story ("...or re-reject an auto-rejected/
        # human-rejected one"): intentional, not an ELIGIBLE_STATUSES bug.
        uuid = await _seed_with_receipts(db, "REQ-PUT-REJECT-REREJECT", status="human-rejected")

        async with _build_client(FakePool(db)) as client:
            response = await client.put(f"/api/v1/reimbursement/{uuid}", json=_REJECT_PAYLOAD)

        assert response.status_code == 200
        status = await db.fetchval("SELECT status FROM reimbursement WHERE uuid = $1", uuid)
        assert status == "human-rejected"

    async def it_returns_400_when_the_body_uuid_does_not_match_the_path_uuid(
        self, db: asyncpg.Connection
    ) -> None:
        uuid = await _seed(db, "REQ-PUT-UUID-MISMATCH")
        payload = {**_APPROVE_PAYLOAD, "uuid": str(uuid4())}

        async with _build_client(FakePool(db)) as client:
            response = await client.put(f"/api/v1/reimbursement/{uuid}", json=payload)

        assert response.status_code == 400
        assert response.json() == {"msg": "body uuid does not match the path uuid"}

    async def it_approves_when_the_body_uuid_matches_the_path_uuid(self, db: asyncpg.Connection) -> None:
        uuid = await _seed(db, "REQ-PUT-UUID-MATCH")
        payload = {**_APPROVE_PAYLOAD, "uuid": str(uuid)}

        async with _build_client(FakePool(db)) as client:
            response = await client.put(f"/api/v1/reimbursement/{uuid}", json=payload)

        assert response.status_code == 200
        status = await db.fetchval("SELECT status FROM reimbursement WHERE uuid = $1", uuid)
        assert status == "human-approved"

    async def it_returns_500_on_a_simulated_pool_failure(
        self, db: asyncpg.Connection, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.ERROR, logger="errors")
        uuid = await _seed(db, "REQ-PUT-POOL-FAILURE")

        async with _build_client(FakePool(db, acquire_error=RuntimeError("connection reset"))) as client:
            response = await client.put(f"/api/v1/reimbursement/{uuid}", json=_APPROVE_PAYLOAD)

        assert response.status_code == 500
        assert response.json() == {"msg": "internal error"}
        assert "connection reset" in caplog.text

    async def it_lets_exactly_one_of_two_concurrent_puts_win(self, migrated_db: str) -> None:
        # A real asyncpg pool (not FakePool's single locked connection): the
        # REVIEW-09 guarantee rests on Postgres's own row-level write
        # serialization between two genuinely in-flight requests, which a
        # single shared connection can't exercise concurrently.
        pool = await asyncpg.create_pool(dsn=migrated_db, min_size=2, max_size=2)
        try:
            uuid = uuid4()
            await pool.execute(_SEED_REIMBURSEMENT, uuid, "REQ-PUT-CONCURRENT", "human-review")
            app = FastAPI()
            register_handlers(app)
            app.include_router(router)
            app.dependency_overrides[get_pool] = lambda: pool
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                responses = await asyncio.gather(
                    client.put(f"/api/v1/reimbursement/{uuid}", json={**_APPROVE_PAYLOAD, "reason": "a"}),
                    client.put(f"/api/v1/reimbursement/{uuid}", json={**_APPROVE_PAYLOAD, "reason": "b"}),
                )

            statuses = sorted(response.status_code for response in responses)
            assert statuses == [200, 400]
            count = await pool.fetchval(
                "SELECT count(*) FROM human_review WHERE reimbursement_uuid = $1", uuid
            )
            assert count == 1
        finally:
            await pool.close()


class DescribeTheRealApp:
    def it_serves_the_route_through_the_apps_actual_lifespan_and_wiring(
        self, migrated_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Second proof point for the pool wiring, alongside GET's own
        # DescribeTheRealApp — plain TestClient is safe here: the pool is
        # constructed fresh inside TestClient's own loop via the real
        # lifespan, never handed in from this test's loop.
        monkeypatch.setenv("DATABASE_URL", migrated_db)
        unknown_uuid = uuid4()

        with TestClient(real_app) as client:
            response = client.put(f"/api/v1/reimbursement/{unknown_uuid}", json=_APPROVE_PAYLOAD)

        assert response.status_code == 404
        assert response.json() == {"msg": f"no reimbursement with uuid {unknown_uuid}"}
