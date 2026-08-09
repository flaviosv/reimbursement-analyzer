import logging
from datetime import UTC, datetime
from functools import partial
from uuid import uuid4

import asyncpg
import pytest
from fastapi.testclient import TestClient
from helpers import FakePool
from helpers import _build_client as _shared_build_client
from api.main import app as real_app
from api.reimbursement.get.route import router
from shared.testing import seed_human_review, seed_reimbursement

pytestmark = pytest.mark.anyio

_build_client = partial(_shared_build_client, router)


class DescribeGetReimbursementByUuid:
    async def it_returns_200_with_the_matching_reimbursement(self, db: asyncpg.Connection) -> None:
        uuid = await seed_reimbursement(db, "REQ-GET-FOUND", status="human-approved")

        async with _build_client(FakePool(db)) as client:
            response = await client.get(f"/api/v1/reimbursement/{uuid}")

        assert response.status_code == 200
        body = response.json()
        assert body["msg"] == "reimbursement found"
        assert body["data"]["uuid"] == str(uuid)
        assert body["data"]["status"] == "human-approved"

    async def it_includes_the_last_human_review_when_present_and_null_when_absent(
        self, db: asyncpg.Connection
    ) -> None:
        with_review = await seed_reimbursement(db, "REQ-GET-HR-PRESENT", status="human-approved")
        await seed_human_review(
            db, with_review, reason="first look", created_at=datetime(2026, 5, 1, tzinfo=UTC)
        )
        await seed_human_review(
            db, with_review, reason="second look", created_at=datetime(2026, 5, 2, tzinfo=UTC)
        )
        without_review = await seed_reimbursement(db, "REQ-GET-HR-ABSENT", status="human-review")

        async with _build_client(FakePool(db)) as client:
            with_review_response = await client.get(f"/api/v1/reimbursement/{with_review}")
            without_review_response = await client.get(f"/api/v1/reimbursement/{without_review}")

        assert with_review_response.json()["data"]["last_human_review"]["reason"] == "second look"
        assert without_review_response.json()["data"]["last_human_review"] is None

    async def it_returns_404_for_a_well_formed_uuid_that_matches_no_row(
        self, db: asyncpg.Connection
    ) -> None:
        unknown_uuid = uuid4()

        async with _build_client(FakePool(db)) as client:
            response = await client.get(f"/api/v1/reimbursement/{unknown_uuid}")

        assert response.status_code == 404
        assert response.json() == {"msg": f"no reimbursement with uuid {unknown_uuid}"}

    async def it_returns_400_for_a_malformed_uuid_and_runs_no_query(self) -> None:
        # FakePool(None): a malformed path segment never reaches the pool —
        # FastAPI's own path coercion rejects it before the route body runs.
        async with _build_client(FakePool(None)) as client:
            response = await client.get("/api/v1/reimbursement/not-a-uuid")

        assert response.status_code == 400
        assert "uuid" in response.json()["msg"]

    async def it_returns_500_on_a_simulated_pool_failure(
        self, db: asyncpg.Connection, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.ERROR, logger="errors")
        uuid = await seed_reimbursement(db, "REQ-GET-500")

        async with _build_client(FakePool(db, acquire_error=RuntimeError("connection reset"))) as client:
            response = await client.get(f"/api/v1/reimbursement/{uuid}")

        assert response.status_code == 500
        assert response.json() == {"msg": "internal error"}
        assert "connection reset" in caplog.text


class DescribeTheRealApp:
    def it_serves_the_route_through_the_apps_actual_lifespan_and_wiring(
        self, migrated_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", migrated_db)

        with TestClient(real_app) as client:
            unknown_uuid = uuid4()
            response = client.get(f"/api/v1/reimbursement/{unknown_uuid}")

        assert response.status_code == 404
        assert response.json() == {"msg": f"no reimbursement with uuid {unknown_uuid}"}
