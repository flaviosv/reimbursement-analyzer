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
    # sibling api/shared test trees, so it is declared again here.
    load_config.cache_clear()


@pytest.fixture(scope="session")
def kafka_bootstrap_server() -> Iterator[str]:
    """A broker sized from the same constant the compose broker and the API's
    producer already use, so a ceiling-sized message is genuinely deliverable
    rather than rejected by a limit this fixture picked for itself.

    Publisher-local: pytest resolves conftest fixtures per directory, so this
    cannot be the one in src/api/tests/reimbursement/create/. The Postgres
    container is shared workspace-wide (root conftest.py) and stays single.
    """
    container = KafkaContainer().with_kraft()
    container.with_env("KAFKA_MESSAGE_MAX_BYTES", str(KAFKA_MAX_MESSAGE_BYTES))
    container.with_env("KAFKA_REPLICA_FETCH_MAX_BYTES", str(KAFKA_MAX_MESSAGE_BYTES))
    container.start()
    try:
        yield container.get_bootstrap_server()
    finally:
        container.stop()
