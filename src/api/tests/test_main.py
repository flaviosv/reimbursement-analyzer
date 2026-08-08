from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from main import lifespan
from producer import get_producer

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

    def it_sets_the_kafka_config_on_app_state(self) -> None:
        app = _build_app()

        with TestClient(app):
            assert app.state.kafka_config.bootstrap_servers


class DescribeGetProducer:
    def it_returns_the_apps_producer_instance(self) -> None:
        app = _build_app()

        with TestClient(app) as client:
            response = client.get("/producer-identity-check")

        assert response.json() == {"is_app_state_producer": True}
