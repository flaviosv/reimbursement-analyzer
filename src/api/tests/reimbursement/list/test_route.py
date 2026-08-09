import logging
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest
from dependencies import get_pool
from errors import register_handlers
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import FakePool, seed_human_review, seed_reimbursement
from main import app as real_app
from reimbursement.list.route import router

pytestmark = pytest.mark.anyio


def _build_client(pool: FakePool) -> httpx.AsyncClient:
    # httpx.AsyncClient + ASGITransport, not TestClient: TestClient drives the
    # ASGI app from a separate thread with its own event loop, and the real
    # asyncpg connection FakePool wraps is bound to *this* test's own loop
    # (the `db` fixture's) — a cross-loop connection use asyncpg rejects
    # outright. AsyncClient runs the app in-process on the current loop.
    app = FastAPI()
    register_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_pool] = lambda: pool
    # raise_app_exceptions=False: otherwise ASGITransport re-raises an
    # unhandled exception into the test instead of returning the registered
    # Exception handler's 500 response — the async-client mirror of
    # TestClient's raise_server_exceptions=False (see test_errors.py).
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


class DescribeGetReimbursement:
    async def it_returns_200_with_rows_ordered_created_at_desc_by_default(
        self, db: asyncpg.Connection
    ) -> None:
        base = datetime(2026, 6, 1, tzinfo=UTC)
        older = await seed_reimbursement(db, "REQ-DEFAULT-OLD", status="auto-approved", created_at=base)
        newer = await seed_reimbursement(
            db, "REQ-DEFAULT-NEW", status="auto-approved", created_at=base + timedelta(hours=1)
        )

        async with _build_client(FakePool(db)) as client:
            response = await client.get("/api/v1/reimbursement", params={"status": "auto-approved"})

        assert response.status_code == 200
        assert [item["uuid"] for item in response.json()["data"]] == [str(newer), str(older)]

    async def it_applies_a_custom_limit_and_offset(self, db: asyncpg.Connection) -> None:
        base = datetime(2026, 6, 1, tzinfo=UTC)
        uuids = [
            await seed_reimbursement(
                db, f"REQ-PAGE-{n}", status="auto-approved", created_at=base + timedelta(minutes=n)
            )
            for n in range(5)
        ]

        async with _build_client(FakePool(db)) as client:
            response = await client.get(
                "/api/v1/reimbursement", params={"limit": 2, "offset": 1, "status": "auto-approved"}
            )

        assert [item["uuid"] for item in response.json()["data"]] == [str(uuids[3]), str(uuids[2])]

    async def it_returns_400_when_limit_exceeds_the_ceiling(self, db: asyncpg.Connection) -> None:
        async with _build_client(FakePool(db)) as client:
            response = await client.get("/api/v1/reimbursement", params={"limit": 501})

        assert response.status_code == 400
        assert "msg" in response.json()

    async def it_returns_400_when_offset_is_negative(self, db: asyncpg.Connection) -> None:
        async with _build_client(FakePool(db)) as client:
            response = await client.get("/api/v1/reimbursement", params={"offset": -1})

        assert response.status_code == 400
        assert "msg" in response.json()

    async def it_returns_400_when_limit_is_negative(self, db: asyncpg.Connection) -> None:
        async with _build_client(FakePool(db)) as client:
            response = await client.get("/api/v1/reimbursement", params={"limit": -1})

        assert response.status_code == 400
        assert "msg" in response.json()

    async def it_returns_400_when_limit_is_not_an_integer(self, db: asyncpg.Connection) -> None:
        async with _build_client(FakePool(db)) as client:
            response = await client.get("/api/v1/reimbursement", params={"limit": "abc"})

        assert response.status_code == 400
        assert "msg" in response.json()

    async def it_returns_400_when_offset_is_not_an_integer(self, db: asyncpg.Connection) -> None:
        async with _build_client(FakePool(db)) as client:
            response = await client.get("/api/v1/reimbursement", params={"offset": "abc"})

        assert response.status_code == 400
        assert "msg" in response.json()

    async def it_returns_200_with_an_empty_list_when_nothing_matches(self, db: asyncpg.Connection) -> None:
        async with _build_client(FakePool(db)) as client:
            response = await client.get("/api/v1/reimbursement", params={"status": "auto-rejected"})

        assert response.status_code == 200
        assert response.json()["data"] == []

    async def it_filters_to_a_single_status(self, db: asyncpg.Connection) -> None:
        await seed_reimbursement(db, "REQ-SINGLE-A", status="human-review")
        await seed_reimbursement(db, "REQ-SINGLE-B", status="auto-rejected")

        async with _build_client(FakePool(db)) as client:
            response = await client.get("/api/v1/reimbursement", params={"status": "human-review"})

        # Membership, not exact-set: other pre-existing integration tests
        # (e.g. agent's, REVIEW-09) commit real, undeletable human-review
        # rows to this same session-scoped migrated_db.
        request_ids = {item["request_id"] for item in response.json()["data"]}
        assert "REQ-SINGLE-A" in request_ids
        assert "REQ-SINGLE-B" not in request_ids

    async def it_filters_to_multiple_comma_separated_statuses(self, db: asyncpg.Connection) -> None:
        await seed_reimbursement(db, "REQ-MULTI-A", status="human-review")
        await seed_reimbursement(db, "REQ-MULTI-B", status="auto-rejected")
        await seed_reimbursement(db, "REQ-MULTI-C", status="auto-approved")

        async with _build_client(FakePool(db)) as client:
            response = await client.get(
                "/api/v1/reimbursement", params={"status": "human-review,auto-rejected"}
            )

        # Subset, not exact-set: see it_filters_to_a_single_status above.
        request_ids = {item["request_id"] for item in response.json()["data"]}
        assert {"REQ-MULTI-A", "REQ-MULTI-B"} <= request_ids
        assert "REQ-MULTI-C" not in request_ids

    async def it_includes_pending_rows_when_status_is_omitted(self, db: asyncpg.Connection) -> None:
        await seed_reimbursement(db, "REQ-ALL-PENDING", status="pending")
        await seed_reimbursement(db, "REQ-ALL-APPROVED", status="human-approved")

        async with _build_client(FakePool(db)) as client:
            response = await client.get("/api/v1/reimbursement")

        # Subset, not exact-set: the update-route and use-case concurrency
        # tests (REVIEW-09) commit real, undeletable rows to this same
        # session-scoped migrated_db — human_review is append-only (DB
        # trigger) and FK-RESTRICTs deleting its parent reimbursement row,
        # so a no-filter query can legitimately see extra rows depending on
        # test order. Scoped to what this test itself seeded, like the
        # concurrency tests' own count(*) assertions are scoped to their uuid.
        request_ids = {item["request_id"] for item in response.json()["data"]}
        assert {"REQ-ALL-PENDING", "REQ-ALL-APPROVED"} <= request_ids

    async def it_returns_400_for_pending_status_excluded_from_the_client_facing_whitelist(
        self, db: asyncpg.Connection
    ) -> None:
        # "pending" is a real status column value (AD-003, see
        # shared/src/shared/reimbursement/use_cases/list_reimbursements.py),
        # deliberately excluded from the client-facing status whitelist.
        async with _build_client(FakePool(db)) as client:
            response = await client.get("/api/v1/reimbursement", params={"status": "pending"})

        assert response.status_code == 400
        assert "msg" in response.json()

    async def it_returns_400_when_one_segment_of_a_comma_list_is_invalid(
        self, db: asyncpg.Connection
    ) -> None:
        # spec.md's own Independent Test for LIST-05/07: one invalid segment
        # among otherwise-valid ones must still invalidate the whole filter,
        # not just be silently dropped.
        async with _build_client(FakePool(db)) as client:
            response = await client.get(
                "/api/v1/reimbursement", params={"status": "human-review,pending"}
            )

        assert response.status_code == 400
        assert "msg" in response.json()

    async def it_returns_400_for_a_repeated_status_query_param(self, db: asyncpg.Connection) -> None:
        async with _build_client(FakePool(db)) as client:
            response = await client.get(
                "/api/v1/reimbursement?status=human-review&status=auto-rejected"
            )

        assert response.status_code == 400
        assert "msg" in response.json()

    async def it_includes_the_last_human_review_when_present_and_null_when_absent(
        self, db: asyncpg.Connection
    ) -> None:
        with_review = await seed_reimbursement(db, "REQ-HR-PRESENT", status="human-approved")
        await seed_human_review(
            db, with_review, reason="first look", created_at=datetime(2026, 5, 1, tzinfo=UTC)
        )
        await seed_human_review(
            db, with_review, reason="second look", created_at=datetime(2026, 5, 2, tzinfo=UTC)
        )
        without_review = await seed_reimbursement(db, "REQ-HR-ABSENT", status="human-review")

        async with _build_client(FakePool(db)) as client:
            response = await client.get("/api/v1/reimbursement")
        by_uuid = {item["uuid"]: item for item in response.json()["data"]}

        assert by_uuid[str(with_review)]["last_human_review"]["reason"] == "second look"
        assert by_uuid[str(without_review)]["last_human_review"] is None

    async def it_includes_the_original_payload_as_a_parsed_json_object(
        self, db: asyncpg.Connection
    ) -> None:
        payload = {"amount": 93.5, "currency": "BRL"}
        uuid = await seed_reimbursement(db, "REQ-PAYLOAD", original_payload=payload)

        async with _build_client(FakePool(db)) as client:
            response = await client.get("/api/v1/reimbursement")
        by_uuid = {item["uuid"]: item for item in response.json()["data"]}

        assert by_uuid[str(uuid)]["original_payload"] == payload

    async def it_returns_500_on_a_simulated_pool_failure(
        self, db: asyncpg.Connection, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.ERROR, logger="errors")

        async with _build_client(FakePool(db, acquire_error=RuntimeError("connection reset"))) as client:
            response = await client.get("/api/v1/reimbursement")

        assert response.status_code == 500
        assert response.json() == {"msg": "internal error"}
        assert "connection reset" in caplog.text


class DescribeTheRealApp:
    def it_serves_the_route_through_the_apps_actual_lifespan_and_wiring(
        self, migrated_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Every other test in this file builds its own throwaway FastAPI() +
        # register_handlers() + router, backed by a FakePool. This one proves
        # the real api.main.app — lifespan (incl. T4's get_pool DB wiring),
        # register_handlers, and router wired together exactly as production
        # runs it — also serves this route correctly. Plain TestClient is
        # safe here (unlike DescribeGetReimbursement above): the pool is
        # constructed fresh inside TestClient's own loop via the real
        # lifespan, never handed in from this test's loop.
        monkeypatch.setenv("DATABASE_URL", migrated_db)

        with TestClient(real_app) as client:
            response = client.get("/api/v1/reimbursement")

        assert response.status_code == 200
        assert isinstance(response.json()["data"], list)
