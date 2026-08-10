from collections.abc import Iterator

import pytest
from agent_fakes import DEFAULT_TEST_MODEL_NAME
from shared.config import KAFKA_MAX_MESSAGE_BYTES, load_config
from testcontainers.community.kafka import KafkaContainer

from reimbursement.agent.agent import get_graph
from reimbursement.config import load_agent_config


@pytest.fixture(autouse=True)
def _default_agent_ai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # AgentConfig now requires GROQ_API_KEY/EXTRACT_FIELDS_MODEL_NAME/
    # ANALYSIS_MODEL_NAME (AD-032) — every test that transitively calls
    # load_agent_config() (most of this package, via agent.py/validation.py)
    # would otherwise fail regardless of what it's actually testing. Sets
    # all six vars to deterministic placeholders; a fail-fast test overrides
    # via monkeypatch.delenv on the one var it cares about.
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_placeholder")
    monkeypatch.setenv("EXTRACT_FIELDS_MODEL_NAME", DEFAULT_TEST_MODEL_NAME)
    monkeypatch.setenv("ANALYSIS_MODEL_NAME", DEFAULT_TEST_MODEL_NAME)
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "30.0")
    monkeypatch.setenv("EXTRACT_FIELDS_TEMPERATURE", "0.0")
    monkeypatch.setenv("ANALYSIS_TEMPERATURE", "0.0")


@pytest.fixture(autouse=True)
def _clear_config_cache() -> None:
    # Both load_config() and load_agent_config() are @lru_cache'd for
    # production (read env once, reuse forever) — without this, whichever
    # test calls either first would poison every later test's view of the
    # environment for the rest of the run. pytest's per-directory conftest
    # scoping does not fan this out from the sibling api/shared/publisher
    # test trees, so it is declared again here.
    load_config.cache_clear()
    load_agent_config.cache_clear()
    # get_graph() is the same kind of process-lifetime singleton (T13,
    # AD-023 shape) — test_agent.py's singleton tests monkeypatch
    # build_graph() and populate this cache with a fake compiled graph;
    # without clearing it here, that fake would otherwise leak into
    # test_validation.py's/test_integration.py's real agent.decide() calls
    # for the rest of the process.
    get_graph.cache_clear()


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
