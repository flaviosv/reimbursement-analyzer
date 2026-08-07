from collections.abc import Iterator

import pytest
from api.config import KAFKA_MAX_MESSAGE_BYTES
from testcontainers.community.kafka import KafkaContainer

# SPEC_DEVIATION: pinned to testcontainers' own default image
# (confluentinc/cp-kafka), not the compose broker (apache/kafka:4.3.1).
# Reason: KafkaContainer.with_kraft() rejects any image tag below "7.0.0"
# under Confluent Platform's own versioning scheme -- verified empirically,
# it raises ValueError for "4.3.1" even though that is a valid, recent
# Apache Kafka release under a different numbering scheme entirely. The
# container's boot script also targets Confluent-image-only script paths
# that do not exist in the apache/kafka image. test_compose_parity.py still
# carries the "we ship 26 MiB" claim against the real compose file; this
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
