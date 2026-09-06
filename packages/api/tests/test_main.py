import importlib
import logging

import ecs_logging
import pytest
import shared.logging as shared_logging_module
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from shared.logging import CorrelationIdFilter

import api.main as main_module
from api.dependencies import get_producer
from api.main import lifespan

# SPEC_DEVIATION: this file constructs a real, unmocked AIOProducer (via
# lifespan) targeting the default localhost:9092 bootstrap server, with no
# Docker-gated container. Safe in a Docker-less CI run only because
# construction and .close() never actually publish anything — produce() is
# never called against this app, only /producer-identity-check (no Kafka
# I/O) — so a refused background connection (visible in stderr as librdkafka
# "Connect...failed") is expected noise, not a test failure.


def _build_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)

    @app.get("/producer-identity-check")
    def _check(producer=Depends(get_producer)) -> dict:
        return {"is_app_state_producer": producer is app.state.producer}

    return app


class DescribeLifespan:
    def it_constructs_the_producer_with_batch_size_one(self) -> None:
        app = _build_app()

        with TestClient(app):
            assert app.state.producer._batch_size == 1

    def it_closes_the_producer_on_shutdown(self) -> None:
        app = _build_app()

        with TestClient(app):
            assert app.state.producer._is_closed is False

        assert app.state.producer._is_closed is True

    def it_constructs_the_pool_with_min_size_zero(
        self, migrated_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # pool_min_size=0 is a deliberate override of DatabaseConfig's own
        # default (2) — see main.lifespan's `replace(config.database,
        # pool_min_size=0)`. Assert it against the app's real pool rather
        # than a mock, the same way DescribeTheRealApp tests exercise the
        # real lifespan wiring elsewhere in this suite.
        monkeypatch.setenv("DATABASE_URL", migrated_db)
        app = _build_app()

        with TestClient(app):
            assert app.state.pool.get_min_size() == 0

    def it_closes_the_pool_on_shutdown(self, migrated_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", migrated_db)
        app = _build_app()

        with TestClient(app):
            assert app.state.pool.is_closing() is False

        assert app.state.pool.is_closing() is True


class DescribeLoggingSetup:
    def it_calls_configure_logging_on_module_import(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # RA-1 (amends TRC-11): api/main.py now calls shared.logging's
        # configure_logging() instead of logging.basicConfig(...) directly.
        # Reloading the module re-executes its top-level configure_logging()
        # call under a patched configure_logging so it's observable without
        # depending on fragile stdout/handler-state side effects.
        calls: list[tuple] = []
        monkeypatch.setattr(shared_logging_module, "configure_logging", lambda: calls.append(()))

        importlib.reload(main_module)

        assert calls == [()]

    def it_attaches_an_ecs_json_handler_with_the_correlation_filter_to_the_root_logger(self) -> None:
        importlib.reload(main_module)

        handler = next(
            h for h in logging.getLogger().handlers if isinstance(h.formatter, ecs_logging.StdlibFormatter)
        )
        assert any(isinstance(f, CorrelationIdFilter) for f in handler.filters)


class DescribeCorrelationIdMiddlewareRegistration:
    def it_returns_an_x_request_id_response_header_when_none_was_sent(self) -> None:
        with TestClient(main_module.app) as client:
            response = client.get("/health")

        assert response.headers["x-request-id"]

    def it_echoes_a_supplied_x_request_id_response_header(self) -> None:
        with TestClient(main_module.app) as client:
            response = client.get("/health", headers={"X-Request-ID": "caller-id"})

        assert response.headers["x-request-id"] == "caller-id"

    def it_returns_the_x_request_id_header_on_an_error_response_too(self) -> None:
        # CORR-04 covers "success or error response" — a malformed uuid
        # never reaches the route body (FastAPI's own path coercion raises
        # RequestValidationError first, caught by errors.py's app-wide
        # handler), so this exercises the header on a real 400 error
        # response with no database access needed.
        with TestClient(main_module.app) as client:
            response = client.get(
                "/api/v1/reimbursement/not-a-uuid", headers={"X-Request-ID": "caller-error-id"}
            )

        assert response.status_code == 400
        assert response.headers["x-request-id"] == "caller-error-id"


class DescribeGetProducer:
    def it_returns_the_apps_producer_instance(self) -> None:
        app = _build_app()

        with TestClient(app) as client:
            response = client.get("/producer-identity-check")

        assert response.json() == {"is_app_state_producer": True}
