from collections.abc import Iterator

import pytest
from shared.config import KAFKA_MAX_MESSAGE_BYTES, load_config
from testcontainers.community.kafka import KafkaContainer


@pytest.fixture(autouse=True)
def _clear_config_cache() -> None:
    # load_config() is @lru_cache'd for production (read env once, reuse
    # forever) — without this, whichever test calls it first would poison
    # every later test's view of the environment for the rest of the run.
    # pytest's per-directory conftest scoping does not fan this out from the
    # sibling api/shared/publisher test trees, so it is declared again here.
    load_config.cache_clear()


@pytest.fixture(scope="session")
def kafka_bootstrap_server() -> Iterator[str]:
    """Agent-local, mirroring publisher/tests/conftest.py's own fixture —
    pytest resolves conftest fixtures per directory, so this cannot be the
    one there. Sized from the same constant the compose broker and every
    producer/consumer already use."""
    container = KafkaContainer().with_kraft()
    container.with_env("KAFKA_MESSAGE_MAX_BYTES", str(KAFKA_MAX_MESSAGE_BYTES))
    container.with_env("KAFKA_REPLICA_FETCH_MAX_BYTES", str(KAFKA_MAX_MESSAGE_BYTES))
    container.start()
    try:
        yield container.get_bootstrap_server()
    finally:
        container.stop()
