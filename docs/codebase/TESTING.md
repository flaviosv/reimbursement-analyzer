# Testing Infrastructure

## Test Frameworks

- **Runner:** `pytest` >=9.1.1 (workspace root `dependency-groups.dev`, shared by every package — no per-package test runner).
- **Async:** `anyio`'s pytest plugin (transitive via `starlette`/`httpx`), not auto-mode — async test classes/methods set `pytestmark = pytest.mark.anyio` explicitly.
- **HTTP:** FastAPI's `TestClient` (`httpx` under the hood) for route-level tests.
- **Containers:** `testcontainers[kafka,postgres]` >=4.15.0 — ephemeral `postgres:18` (always) and a Confluent Kafka image (one integration file only).
- **Coverage tooling:** none configured.

## Test Organization

- **Location:** `src/<package>/tests/`, mirroring `src/<package>/src/`'s structure — e.g. `packages/api/src/reimbursement/create/route.py` ↔ `packages/api/tests/reimbursement/create/test_route.py`.
- **Naming:** `test_<module>.py`; classes `Describe<Subject>`, methods `it_<behavior>` — pytest is configured (`python_classes`/`python_functions` in root `pyproject.toml`) to collect this convention instead of the default `Test*`/`test_*`.
- **Collection root:** `testpaths = ["packages/api", "packages/publisher", "packages/reimbursement", "packages/shared"]` at the workspace level, named explicitly (not a bare `["src"]`) so a tests directory added to any package is never silently uncollected.
- **Import path:** `pythonpath = ["packages/api/tests", "packages/publisher/tests", "packages/reimbursement/tests"]` — `api`, `publisher`, and `reimbursement` are all real installed packages now (setuptools + package-dir, AD-031), so none needs its own module root on this list; only each package's `tests/` dir does, so bare-name imports like `from helpers import ...` (`api`), `from fakes import ...` (`publisher`), `from agent_fakes import ...` (`reimbursement`) resolve — plus sibling feature slices reusing filenames like `test_route.py` need import-mode `importlib` to keep each nested test dir off `sys.path` on its own.

## Testing Patterns

- **Unit (fakes, no I/O):** validation logic (`test_validation.py`), body-cap logic (`test_payload.py`), config loading (`test_config.py`), envelope building and the publish wrapper against a fake producer (`test_producer.py` in both `shared` and `reimbursement/create`).
- **Route-level (TestClient + dependency_overrides):** `test_route.py` builds a throwaway `FastAPI()` app per test, overriding `get_producer` with a fake — the real app object (`main.app`) is exercised once, in `DescribeTheRealApp`, to prove production wiring (lifespan + handlers + router) also honors the app-wide error contract.
- **DB schema (constraint-focused, not ORM-focused):** `test_reimbursement_schema.py`, `test_human_review_schema.py` — assert every `CHECK`/`UNIQUE`/FK/trigger rejects its violating row; an unenforced constraint reads as a false guarantee.
- **Migration machinery:** `test_migrations.py`, `test_migration_lifecycle.py` — idempotency, rollback, advisory-lock behavior. Deliberately NOT the target of exhaustive testing (see `CONVENTIONS.md`/project memory: "test the guarantees infrastructure produces, not the machinery itself").
- **Compose/config parity:** `test_compose_parity.py` asserts `docker-compose.yml`'s Kafka byte-ceiling env vars actually match `shared.config.KAFKA_MAX_MESSAGE_BYTES` — catches config drift between the two.
- **Centralized test helpers (`api`):** `packages/api/tests/helpers.py` holds `_build_client` (test-client factory), `FakePool` (fake `asyncpg.Pool`), and DB seed builders (`seed_reimbursement`, `seed_human_review`) shared across the `reimbursement/list` and `reimbursement/update` test modules — consolidated from what was previously duplicated per module.
- **Integration (`@pytest.mark.integration`):** `test_integration.py` (in both `api` and `publisher`) round-trips a real batch through real ephemeral Kafka/Postgres containers.
- **Decision-tree unit tests with test doubles as a module, not fixtures (`publisher`):** `packages/publisher/tests/fakes.py` defines `FakeProducer`, `FakePool`/`FakeConnection`, and `RealPool` as plain classes tests construct directly with per-test arguments (injectable errors, delays, turn-ordering) — reachable by bare name via the workspace `pythonpath`, not via conftest fixtures, because each test needs a differently-configured double rather than one shared resource. `test_processing.py` (813 lines) covers every `ItemOutcome` branch this way, including concurrency (`FakePool`'s in-flight/max-in-flight counters prove the semaphore bound without timing).
- **Real-connection-behind-a-lock (`publisher`):** `RealPool` in `fakes.py` wraps the one real asyncpg connection a `db` fixture owns, serialized by an `asyncio.Lock`, so rollback/duplicate/escalation tests exercise genuine transaction and constraint semantics instead of a fake's approximation of them — while every in-flight caller still waits for a slot rather than failing, preserving the fan-out shape under test.
- **Decision-tree unit tests with test doubles as a module (`reimbursement`):** `packages/reimbursement/tests/agent_fakes.py` defines `FakePool`/`FakeConnection` (no in-flight/max-in-flight counters — `reimbursement` has no per-message fan-out to observe; no `RealPool` — `reimbursement.validation` wraps no `conn.transaction()`, so there's no transaction semantics a fake would approximate imperfectly, unlike `publisher`). `FakeProducer` itself is not redefined here — it's re-exported from `shared.testing`, the one double `shared` owns for a contract (`AIOProducer.produce()`) it also owns. `test_validation.py` (293 lines) covers every `MessageOutcome` branch this way. The `agent/` decision-graph scaffold (`agent.py`, `nodes/*`, `prompts/*`) has no tests at all yet — every node is a one-line stub.

## Test Execution

| Command | Effect |
| ------- | ------ |
| `uv run pytest` | Full suite, including container-backed integration tests |
| `uv run pytest -m "not integration"` | Skip the Kafka-container-heavy tests |
| `TEST_DATABASE_URL=postgresql://user:pw@host:5432/postgres uv run pytest` | Point the Postgres-backed tests at a supplied server instead of a throwaway container |

Docker must be running for the default `postgres`/`kafka` container fixtures; if it is not, the suite fails with one explanatory message rather than dozens of connection errors.

## Coverage Targets

No coverage tool or enforced target exists. Coverage is a byproduct of the `Describe*`/`it_*` convention (each behavior gets its own named test), not a measured metric.

## Test Coverage Matrix

| Code Layer | Required Test Type | Location Pattern | Run Command |
| ---------- | ------------------- | ----------------- | ----------- |
| Request validation | Unit | `packages/api/tests/reimbursement/create/test_validation.py` | `uv run pytest -m "not integration"` |
| Body-size cap | Unit | `packages/api/tests/reimbursement/create/test_payload.py` | same |
| Kafka publish (generic) | Unit (fake producer) | `packages/shared/tests/test_producer.py` | same |
| Kafka publish (domain wrapper) | Unit (fake producer) | `packages/api/tests/reimbursement/create/test_producer.py` | same |
| Config loading | Unit | `packages/shared/tests/test_config.py` | same |
| Route / error contract | Route-level (TestClient) | `packages/api/tests/reimbursement/create/test_route.py`, `packages/api/tests/test_errors.py` | same |
| App lifespan / DI | Route-level (TestClient) | `packages/api/tests/test_main.py` | same |
| DB schema constraints | Integration (real Postgres via testcontainers) | `packages/api/tests/test_*_schema.py` | `uv run pytest` |
| Migration runner | Integration | `packages/api/tests/test_migrat*.py` | `uv run pytest` |
| End-to-end Kafka round-trip (api → Request) | Integration (`@pytest.mark.integration`) | `packages/api/tests/reimbursement/create/test_integration.py` | `uv run pytest` |
| Publisher decision tree (insert+publish, retry, duplicate, escalation) | Unit (fakes) | `packages/publisher/tests/test_processing.py` | `uv run pytest -m "not integration"` |
| Publisher consumer lifecycle (offset commit, shutdown, startup checks) | Unit | `packages/publisher/tests/test_consumer.py` | same |
| Publisher end-to-end (Request → row + Reimbursement) | Integration | `packages/publisher/tests/test_integration.py` | `uv run pytest` |
| `reimbursement` repository (insert, duplicate detection) | Integration (real Postgres) | `packages/shared/tests/reimbursement/test_repository.py` | `uv run pytest` |
| Human-review escalation + history rendering | Unit + integration | `packages/shared/tests/reimbursement/use_cases/test_send_human_review.py` | mixed |
| `publish_pending` use case (insert+publish as one unit) | Unit + integration (real Postgres) | `packages/shared/tests/reimbursement/use_cases/test_publish_pending.py` | mixed |
| `shared.models` (pydantic model validation) | Unit | `packages/shared/tests/test_models.py` | `uv run pytest -m "not integration"` |
| Workspace-level Postgres fixture reachability | Integration (real Postgres) | `packages/shared/tests/test_database_fixture.py` | `uv run pytest` |
| Failure log (never raises, truncation) | Unit | `packages/shared/tests/test_failure_log.py` | `uv run pytest -m "not integration"` |
| `reimbursement` resolve/requeue/escalate (staleness guard, ghost tolerance, retry ceiling) | Unit (fakes) | `packages/reimbursement/tests/test_validation.py` | `uv run pytest -m "not integration"` |
| `reimbursement` consumer lifecycle (offset commit, graceful shutdown, startup checks) | Unit | `packages/reimbursement/tests/test_consumer.py` | same |
| `reimbursement` config loading | Unit | `packages/reimbursement/tests/test_config.py` | same |
| `reimbursement` end-to-end (Reimbursement → resolved / ghost / stale / escalated) | Integration | `packages/reimbursement/tests/test_integration.py` | `uv run pytest` |
| List endpoint (query parsing, response shaping, route) | Unit + route-level | `packages/api/tests/reimbursement/list/{test_params,test_response,test_route}.py` | `uv run pytest -m "not integration"` |
| Update/PUT endpoint (payload validation, route) | Unit + route-level | `packages/api/tests/reimbursement/update/{test_route,test_validation}.py` | same |
| `list_reimbursements` use case (filter/pagination gates) | Unit | `packages/shared/tests/reimbursement/use_cases/test_list_reimbursements.py` | same |
| `review_reimbursement` use case (approve/reject transaction, disambiguation, concurrency) | Unit + integration (real Postgres) | `packages/shared/tests/reimbursement/use_cases/test_review_reimbursement.py` | mixed |
| `reimbursement` repository — list/approve/reject/find-state/record-decision | Integration (real Postgres) | `packages/shared/tests/reimbursement/test_repository.py` | `uv run pytest` |
| `shared.db.managed_pool` lifecycle | Unit | `packages/shared/tests/test_db.py` | `uv run pytest -m "not integration"` |
| Decision logic — auto-approve/reject/human-review policy (`reimbursement/agent/`) | **None** — scaffolded (LangGraph graph + 5 stub nodes), no logic to test yet | — | — |

## Parallelism Assessment

| Test Type | Parallel-Safe? | Isolation Model | Evidence |
| --------- | -------------- | ----------------- | -------- |
| Postgres-backed (schema, migrations, repository) | Yes, across processes | Each run creates its own uniquely-named database (`reimbursementanalyzer_<pid>_<random>_test`) on a session-scoped server, dropped `WITH (FORCE)` afterward. The fixture was promoted to a workspace-level conftest so `api` and `shared`/`publisher` tests share it | `packages/api/tests/helpers.py::disposable_database_name`, workspace-level `conftest.py::disposable_database` |
| Kafka integration (`api`) | Not verified for concurrent runs | One session-scoped `KafkaContainer` fixture shared by every test in `reimbursement/create/`; each test drains from a fresh consumer group and matches on its own marker string, but the container itself is not per-test | `packages/api/tests/reimbursement/create/conftest.py::kafka_bootstrap_server` |
| Kafka integration (`publisher`) | Not verified for concurrent runs | Same pattern as `api`'s, publisher-local: one session-scoped `KafkaContainer` sized from `KAFKA_MAX_MESSAGE_BYTES`. Not hoisted to the root conftest like Postgres was — the real reason is a topic-name collision between `api`'s and `publisher`'s fixtures, not conftest resolution scoping (pytest conftest fixtures *do* fan out workspace-wide, which is exactly how the root-level Postgres fixture below reaches both) | `packages/publisher/tests/conftest.py::kafka_bootstrap_server` |
| Kafka integration (`reimbursement`) | Not verified for concurrent runs | Same pattern again, package-local: one session-scoped `KafkaContainer`, same reasoning for staying local (topic-name collision risk against `api`'s/`publisher`'s own fixtures, not conftest scoping) | `packages/reimbursement/tests/conftest.py::kafka_bootstrap_server` |
| Unit tests (fakes only) | Yes | No shared external state; `shared.config.load_config()`'s process-wide `lru_cache` is explicitly cleared by an autouse fixture before every test | `packages/shared/tests/conftest.py`, `packages/api/tests/conftest.py` |

## Gate Check Commands

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | After changes with no *Kafka* dependency | `uv run pytest -m "not integration"` — still needs Docker: the `integration` marker means "needs a container *beyond* the suite's own Postgres default" (`pyproject.toml`'s marker text), not "needs no container" — see line 37 above and the dozens of Postgres-backed tests that also run in this gate |
| Full | Before considering a task/PR done | `uv run pytest` |
| Lint (ad hoc) | Optional sanity check, not gated | `uv run ruff check <path>` |
