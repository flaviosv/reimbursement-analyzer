"""Shared fixtures for the tests/e2e suite: env loading, stack-readiness
gate, HTTP client, Kafka producer config. Never invoked directly by a
test — exercised indirectly by every e2e scenario test (Phase 3,
tests/e2e/test_*.py), matching this repo's own convention for test
infrastructure modules (fakes.py/agent_fakes.py/shared.testing).

Approach 1 (design.md): this fixture set never calls `docker compose
up`/`down` itself — it polls a real, already-running stack and fails fast
if it isn't up, rather than managing that stack's lifecycle."""

import os
import time
from collections.abc import Iterator
from dataclasses import dataclass, replace

import httpx
import pytest
from confluent_kafka import Producer
from dotenv import load_dotenv
from shared.config import KafkaConfig, load_config

_STACK_READY_TIMEOUT_SECONDS = 60.0
_STACK_READY_POLL_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class E2EEnv:
    api_base_url: str
    kafka_bootstrap: str
    langfuse_public_key: str | None
    langfuse_secret_key: str | None
    langfuse_host: str


@pytest.fixture(scope="session")
def e2e_env() -> E2EEnv:
    """Loads the repo-root .env — a real, working `load_dotenv()` call
    here, unlike the no-op it is inside a container, since this suite runs
    on the host. Fails immediately (not the live Groq call each scenario
    test would otherwise hang or error on) if GROQ_API_KEY is unset."""
    load_dotenv()
    if not os.getenv("GROQ_API_KEY"):
        pytest.fail(
            "GROQ_API_KEY is unset — the e2e suite drives real Groq calls "
            "through the reimbursement container; set a real key in .env "
            "before running `uv run pytest -m e2e`",
            pytrace=False,
        )
    return E2EEnv(
        api_base_url="http://localhost:8000",
        kafka_bootstrap="localhost:9092",
        langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-local-dev"),
        langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
        langfuse_host="http://localhost:3000",
    )


def _poll_http_health(url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: str = "never attempted"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=5.0)
            if response.status_code < 500:
                return
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(_STACK_READY_POLL_INTERVAL_SECONDS)
    raise AssertionError(f"{url} never became reachable within {timeout}s (last error: {last_error})")


def _poll_kafka_metadata(bootstrap_servers: str, timeout: float) -> None:
    producer = Producer({"bootstrap.servers": bootstrap_servers})
    deadline = time.monotonic() + timeout
    last_error: str = "never attempted"
    while time.monotonic() < deadline:
        try:
            producer.list_topics(timeout=5.0)
            return
        except Exception as exc:  # noqa: BLE001 - broker-reachability probe, any error means "not ready yet"
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(_STACK_READY_POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"kafka broker at {bootstrap_servers} never returned metadata within {timeout}s (last error: {last_error})"
    )


@pytest.fixture(scope="session", autouse=True)
def stack_ready(e2e_env: E2EEnv) -> None:
    """Polls every real dependency the e2e suite drives — api's /health,
    a Kafka broker-metadata request, and LangFuse's own health endpoint —
    each within a bounded timeout, and pytest.fails naming whichever
    dependency never became ready. Never starts/stops the compose stack
    itself (Approach 1, design.md) — `docker compose up -d` is a
    documented manual prerequisite (README, E2E-11)."""
    checks: dict[str, tuple[str, float]] = {
        "api": (f"{e2e_env.api_base_url}/health", _STACK_READY_TIMEOUT_SECONDS),
        "langfuse": (f"{e2e_env.langfuse_host}/api/public/health", _STACK_READY_TIMEOUT_SECONDS),
    }
    for name, (url, timeout) in checks.items():
        try:
            _poll_http_health(url, timeout)
        except AssertionError as exc:
            pytest.fail(f"stack_ready: {name} never became healthy — {exc}", pytrace=False)
    try:
        _poll_kafka_metadata(e2e_env.kafka_bootstrap, _STACK_READY_TIMEOUT_SECONDS)
    except AssertionError as exc:
        pytest.fail(f"stack_ready: kafka never became healthy — {exc}", pytrace=False)


@pytest.fixture
def api_client(e2e_env: E2EEnv) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=e2e_env.api_base_url, timeout=30.0) as client:
        yield client


@pytest.fixture
def kafka_producer_config(e2e_env: E2EEnv) -> KafkaConfig:
    return replace(load_config().kafka, bootstrap_servers=e2e_env.kafka_bootstrap)
