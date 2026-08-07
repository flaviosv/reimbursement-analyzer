from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api.config import KAFKA_MAX_MESSAGE_BYTES, MESSAGE_TIMEOUT_MS, bootstrap_servers
from api.kafka import get_producer, lifespan_producer, producer_config


class DescribeProducerConfig:
    def it_sets_kafka_config_from_the_pinned_constants(self) -> None:
        config = producer_config()

        assert config == {
            "bootstrap.servers": bootstrap_servers(),
            "acks": "all",
            "enable.idempotence": True,
            "message.max.bytes": KAFKA_MAX_MESSAGE_BYTES,
            "message.timeout.ms": MESSAGE_TIMEOUT_MS,
        }

    def it_leaves_retries_at_the_librdkafka_default(self) -> None:
        # enable.idempotence=true rejects retries=0, and the envelope's own
        # `retry` counter is a separate, message-carried concept (not
        # librdkafka's producer-level retries property).
        assert "retries" not in producer_config()


def _build_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan_producer)

    @app.get("/producer-identity-check")
    def _check(producer=Depends(get_producer)) -> dict:
        return {"is_app_state_producer": producer is app.state.producer}

    return app


class DescribeLifespanProducer:
    def it_constructs_the_producer_with_batch_size_one(self) -> None:
        app = _build_app()

        with TestClient(app):
            assert app.state.producer._batch_size == 1

    def it_closes_the_producer_on_shutdown(self) -> None:
        app = _build_app()

        with TestClient(app):
            assert app.state.producer._is_closed is False

        assert app.state.producer._is_closed is True


class DescribeGetProducer:
    def it_returns_the_apps_producer_instance(self) -> None:
        app = _build_app()

        with TestClient(app) as client:
            response = client.get("/producer-identity-check")

        assert response.json() == {"is_app_state_producer": True}
