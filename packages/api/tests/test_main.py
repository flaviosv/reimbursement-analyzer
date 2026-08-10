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
