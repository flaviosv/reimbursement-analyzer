import importlib
import logging

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

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
    def it_calls_basic_config_at_info_level_on_module_import(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # TRC-11: without this, uvicorn's default logging setup never
        # configures the root logger, so every INFO-level log line this
        # feature adds would silently never emit. Reloading the module
        # re-executes its top-level logging.basicConfig(...) call under a
        # patched basicConfig so it's observable without depending on
        # fragile stdout/handler-state side effects.
        calls: list[dict] = []
        monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: calls.append(kwargs))

        importlib.reload(main_module)

        assert calls == [{"level": logging.INFO}]


class DescribeGetProducer:
    def it_returns_the_apps_producer_instance(self) -> None:
        app = _build_app()

        with TestClient(app) as client:
            response = client.get("/producer-identity-check")

        assert response.json() == {"is_app_state_producer": True}


class DescribeTracingIntegration:
    def it_initializes_a_tracer_provider_with_the_api_service_name(self) -> None:
        # `main_module._tracer_provider` is what module scope constructed
        # and registered; asserting on its own resource content (rather than
        # `trace.get_tracer_provider() is ...`) keeps this robust against
        # other tests in this file reloading the module — OTel's own
        # set_tracer_provider() refuses to override an already-registered
        # global provider, by design, so re-import never changes the
        # process-wide one.
        provider = main_module._tracer_provider

        assert provider.resource.attributes["service.name"] == "reimbursement-analyzer-api"

    def it_shuts_down_the_tracer_on_lifespan_shutdown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[object] = []
        monkeypatch.setattr(main_module, "shutdown_tracer", lambda provider: calls.append(provider))
        app = _build_app()

        with TestClient(app):
            assert calls == []

        assert calls == [main_module._tracer_provider]

    def it_instruments_the_app_with_fastapi_instrumentor(self) -> None:
        # instrument_app's own documented, stable signal — set exactly once,
        # at import time, with no per-route code (AC3's scope-out).
        assert main_module.app._is_instrumented_by_opentelemetry is True

    def it_does_not_fail_to_start_when_the_apm_server_is_unreachable(self) -> None:
        # The default/otel-configured endpoint is unreachable in this test
        # environment already (no live APM Server), and app construction/
        # startup above never raised — this test names that guarantee
        # explicitly rather than leaving it merely implicit in "the other
        # tests didn't crash".
        with TestClient(main_module.app) as client:
            response = client.get("/health")

        assert response.status_code == 200
