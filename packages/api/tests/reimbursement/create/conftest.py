from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from shared.config import KAFKA_MAX_MESSAGE_BYTES, KafkaConfig, load_config
from testcontainers.community.kafka import KafkaContainer


class _ImmediateFakeSyncProducer:
    """produce() then flush() resolves the message immediately — models
    confluent_kafka.Producer's synchronous produce()/flush() pair, the shape
    shared.producer.publish now drives directly via ._producer/.executor."""

    def __init__(self, outer: "ImmediateFakeProducer", *, error: Exception | None = None) -> None:
        self._outer = outer
        self.error = error
        self._on_delivery: Any = None

    def produce(
        self, *, topic: str, value: bytes | None = None, on_delivery: Any = None, **kwargs: object
    ) -> None:
        self._outer.produced.append(value)
        self._on_delivery = on_delivery

    def flush(self, timeout: float) -> int:
        if self._on_delivery is not None:
            self._on_delivery(self.error, object())
        return 0


class ImmediateFakeProducer:
    """Every produce() call resolves immediately — either successfully, or
    with the given delivery error. Shared by test_producer.py and
    test_route.py — the only fake either file needs when a call's outcome
    doesn't have to depend on argument order."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.produced: list[bytes] = []
        self._producer = _ImmediateFakeSyncProducer(self, error=error)
        self.executor = ThreadPoolExecutor(max_workers=4)


@pytest.fixture
def immediate_fake_producer_class() -> type[ImmediateFakeProducer]:
    # Exposed as a fixture, not a plain import: two conftest.py files exist
    # in this test tree (AD-009), so a bare `from conftest import ...` is
    # ambiguous under --import-mode=importlib. Fixtures resolve by pytest's
    # own directory scoping instead, sidestepping that entirely.
    return ImmediateFakeProducer


@pytest.fixture
def kafka_config() -> KafkaConfig:
    return load_config().kafka

# SPEC_DEVIATION: pinned to testcontainers' own default image
# (confluentinc/cp-kafka), not the compose broker (apache/kafka:4.3.1).
# Reason: KafkaContainer.with_kraft() rejects any image tag below "7.0.0"
# under Confluent Platform's own versioning scheme -- verified empirically,
# it raises ValueError for "4.3.1" even though that is a valid, recent
# Apache Kafka release under a different numbering scheme entirely. The
# container's boot script also targets Confluent-image-only script paths
# that do not exist in the apache/kafka image. test_compose_parity.py still
# carries the "we ship 2 MiB" claim against the real compose file; this
# fixture only proves that a broker sized from the same constant accepts
# and returns a ceiling-sized message.


@pytest.fixture(scope="session")
def kafka_bootstrap_server() -> Iterator[str]:
    container = KafkaContainer().with_kraft()
    container.with_env("KAFKA_MESSAGE_MAX_BYTES", str(KAFKA_MAX_MESSAGE_BYTES))
    container.with_env("KAFKA_REPLICA_FETCH_MAX_BYTES", str(KAFKA_MAX_MESSAGE_BYTES))
    container.start()
    try:
        yield container.get_bootstrap_server()
    finally:
        container.stop()
