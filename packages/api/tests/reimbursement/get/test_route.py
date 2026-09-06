import logging
from datetime import UTC, datetime
from functools import partial
from unittest.mock import Mock
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

    async def it_returns_identical_data_across_two_back_to_back_gets(self, db: asyncpg.Connection) -> None:
        uuid = await seed_reimbursement(db, "REQ-GET-IDEMPOTENT", status="human-approved")

        async with _build_client(FakePool(db)) as client:
            first_response = await client.get(f"/api/v1/reimbursement/{uuid}")
            second_response = await client.get(f"/api/v1/reimbursement/{uuid}")

        assert first_response.json()["data"] == second_response.json()["data"]

    async def it_includes_last_human_review_when_present(self, db: asyncpg.Connection) -> None:
        uuid = await seed_reimbursement(db, "REQ-GET-HR-PRESENT", status="human-approved")
        await seed_human_review(
            db, uuid, reason="first look", created_at=datetime(2026, 5, 1, tzinfo=UTC)
        )
        await seed_human_review(
            db, uuid, reason="second look", created_at=datetime(2026, 5, 2, tzinfo=UTC)
        )

        async with _build_client(FakePool(db)) as client:
            response = await client.get(f"/api/v1/reimbursement/{uuid}")

        assert response.json()["data"]["last_human_review"]["reason"] == "second look"

    async def it_returns_last_human_review_null_when_absent(self, db: asyncpg.Connection) -> None:
        uuid = await seed_reimbursement(db, "REQ-GET-HR-ABSENT", status="human-review")

        async with _build_client(FakePool(db)) as client:
            response = await client.get(f"/api/v1/reimbursement/{uuid}")

        assert response.json()["data"]["last_human_review"] is None

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

    async def it_stamps_reimbursement_uuid_on_the_current_span(
        self, db: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        uuid = await seed_reimbursement(db, "REQ-GET-SPAN-ATTR", status="human-approved")
        fake_span = Mock()
        monkeypatch.setattr(
            "api.reimbursement.get.route.trace.get_current_span", lambda: fake_span
        )
        monkeypatch.setattr("api.reimbursement.get.route.get_correlation_id", lambda: None)

        async with _build_client(FakePool(db)) as client:
            response = await client.get(f"/api/v1/reimbursement/{uuid}")

        assert response.status_code == 200
        fake_span.set_attribute.assert_called_once_with("reimbursement.uuid", str(uuid))

    async def it_stamps_correlation_id_on_the_current_span_when_available(
        self, db: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        uuid = await seed_reimbursement(db, "REQ-GET-SPAN-CORR", status="human-approved")
        fake_span = Mock()
        monkeypatch.setattr(
            "api.reimbursement.get.route.trace.get_current_span", lambda: fake_span
        )
        monkeypatch.setattr(
            "api.reimbursement.get.route.get_correlation_id", lambda: "req-get-corr-id"
        )

        async with _build_client(FakePool(db)) as client:
            response = await client.get(f"/api/v1/reimbursement/{uuid}")

        assert response.status_code == 200
        fake_span.set_attribute.assert_any_call("reimbursement.uuid", str(uuid))
        fake_span.set_attribute.assert_any_call("correlation_id", "req-get-corr-id")


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
