import httpx
import pytest
from api.dependencies import get_pool
from api.main import app as real_app
from fastapi.testclient import TestClient
from helpers import FakePool
from prometheus_client import CONTENT_TYPE_LATEST
from shared.testing import seed_reimbursement

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _database_url(migrated_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Every test in this file exercises the real api.main.app through its
    # real lifespan (DescribeTheRealApp's own pattern) — a valid DATABASE_URL
    # is needed for the lifespan's own pool construction to succeed even when
    # a given test overrides get_pool with a FakePool for the request itself.
    monkeypatch.setenv("DATABASE_URL", migrated_db)


class DescribeMetricsRoute:
    def it_returns_200(self) -> None:
        with TestClient(real_app) as client:
            response = client.get("/metrics")

        assert response.status_code == 200

    def it_returns_the_prometheus_exposition_content_type(self) -> None:
        with TestClient(real_app) as client:
            response = client.get("/metrics")

        assert response.headers["content-type"] == CONTENT_TYPE_LATEST

    def it_returns_valid_prometheus_format_text(self) -> None:
        with TestClient(real_app) as client:
            response = client.get("/metrics")

        body = response.text
        assert "# TYPE api_http_requests_total counter" in body
        assert "# TYPE reimbursement_status_count gauge" in body

    async def it_updates_the_status_gauge_from_the_current_db_state(self, db) -> None:
        # httpx.AsyncClient + ASGITransport, not TestClient: TestClient drives
        # the ASGI app from a separate thread with its own event loop, and
        # the real asyncpg connection FakePool wraps is bound to *this*
        # test's own loop (the `db` fixture's) — a cross-loop connection use
        # asyncpg rejects outright (see helpers.py::_build_client). No
        # lifespan needed here either: get_pool is overridden, so the route
        # never touches the lifespan-created pool.
        await seed_reimbursement(db, "REQ-METRICS-GAUGE-1", status="human-review")
        real_app.dependency_overrides[get_pool] = lambda: FakePool(db)
        try:
            transport = httpx.ASGITransport(app=real_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/metrics")
        finally:
            real_app.dependency_overrides.clear()

        assert 'reimbursement_status_count{status="human-review"} 1.0' in response.text
        assert 'reimbursement_status_count{status="pending"} 0.0' in response.text

    def it_records_a_404_with_the_unmatched_path_label(self) -> None:
        with TestClient(real_app) as client:
            client.get("/this-route-does-not-exist")
            response = client.get("/metrics")

        body = response.text
        assert 'api_http_requests_total{method="GET",path="unmatched",status_code="404"}' in body
