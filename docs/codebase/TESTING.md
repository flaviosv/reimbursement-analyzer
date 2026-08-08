# Testing Infrastructure

## Test Frameworks

- **Runner:** `pytest` >=9.1.1 (workspace root `dependency-groups.dev`, shared by every package — no per-package test runner).
- **Async:** `anyio`'s pytest plugin (transitive via `starlette`/`httpx`), not auto-mode — async test classes/methods set `pytestmark = pytest.mark.anyio` explicitly.
- **HTTP:** FastAPI's `TestClient` (`httpx` under the hood) for route-level tests.
- **Containers:** `testcontainers[kafka,postgres]` >=4.15.0 — ephemeral `postgres:18` (always) and a Confluent Kafka image (one integration file only).
- **Coverage tooling:** none configured.

## Test Organization

- **Location:** `src/<package>/tests/`, mirroring `src/<package>/src/`'s structure — e.g. `src/api/src/reimbursement/create/route.py` ↔ `src/api/tests/reimbursement/create/test_route.py`.
- **Naming:** `test_<module>.py`; classes `Describe<Subject>`, methods `it_<behavior>` — pytest is configured (`python_classes`/`python_functions` in root `pyproject.toml`) to collect this convention instead of the default `Test*`/`test_*`.
- **Collection root:** `testpaths = ["src"]` at the workspace level (not per-package) — a tests directory added to `agent` or `publisher` is picked up automatically.
- **Import path:** `pythonpath = ["src/api/tests", "src/api/src"]` — required because `api` is a virtual (uninstalled) workspace member, and because sibling feature slices can reuse filenames like `test_route.py` (import-mode `importlib` needs each nested test dir NOT to be the thing that lands on `sys.path`).

## Testing Patterns

- **Unit (fakes, no I/O):** validation logic (`test_validation.py`), body-cap logic (`test_payload.py`), config loading (`test_config.py`), envelope building and the publish wrapper against a fake producer (`test_producer.py` in both `shared` and `reimbursement/create`).
- **Route-level (TestClient + dependency_overrides):** `test_route.py` builds a throwaway `FastAPI()` app per test, overriding `get_producer` with a fake — the real app object (`main.app`) is exercised once, in `DescribeTheRealApp`, to prove production wiring (lifespan + handlers + router) also honors the app-wide error contract.
- **DB schema (constraint-focused, not ORM-focused):** `test_reimbursement_schema.py`, `test_human_review_schema.py` — assert every `CHECK`/`UNIQUE`/FK/trigger rejects its violating row; an unenforced constraint reads as a false guarantee.
- **Migration machinery:** `test_migrations.py`, `test_migration_lifecycle.py` — idempotency, rollback, advisory-lock behavior. Deliberately NOT the target of exhaustive testing (see `CONVENTIONS.md`/project memory: "test the guarantees infrastructure produces, not the machinery itself").
- **Compose/config parity:** `test_compose_parity.py` asserts `docker-compose.yml`'s Kafka byte-ceiling env vars actually match `shared.config.KAFKA_MAX_MESSAGE_BYTES` — catches config drift between the two.
- **Integration (`@pytest.mark.integration`):** `test_integration.py` (in both `api` and `publisher`) round-trips a real batch through real ephemeral Kafka/Postgres containers.
- **Decision-tree unit tests with test doubles as a module, not fixtures (`publisher`):** `src/publisher/tests/fakes.py` defines `FakeProducer`, `FakePool`/`FakeConnection`, and `RealPool` as plain classes tests construct directly with per-test arguments (injectable errors, delays, turn-ordering) — reachable by bare name via the workspace `pythonpath`, not via conftest fixtures, because each test needs a differently-configured double rather than one shared resource. `test_processing.py` (813 lines) covers every `ItemOutcome` branch this way, including concurrency (`FakePool`'s in-flight/max-in-flight counters prove the semaphore bound without timing).
- **Real-connection-behind-a-lock (`publisher`):** `RealPool` in `fakes.py` wraps the one real asyncpg connection a `db` fixture owns, serialized by an `asyncio.Lock`, so rollback/duplicate/escalation tests exercise genuine transaction and constraint semantics instead of a fake's approximation of them — while every in-flight caller still waits for a slot rather than failing, preserving the fan-out shape under test.

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
| Request validation | Unit | `src/api/tests/reimbursement/create/test_validation.py` | `uv run pytest -m "not integration"` |
| Body-size cap | Unit | `src/api/tests/reimbursement/create/test_payload.py` | same |
| Kafka publish (generic) | Unit (fake producer) | `src/shared/tests/test_producer.py` | same |
| Kafka publish (domain wrapper) | Unit (fake producer) | `src/api/tests/reimbursement/create/test_producer.py` | same |
| Config loading | Unit | `src/shared/tests/test_config.py` | same |
| Route / error contract | Route-level (TestClient) | `src/api/tests/reimbursement/create/test_route.py`, `src/api/tests/test_errors.py` | same |
| App lifespan / DI | Route-level (TestClient) | `src/api/tests/test_main.py` | same |
| DB schema constraints | Integration (real Postgres via testcontainers) | `src/api/tests/test_*_schema.py` | `uv run pytest` |
| Migration runner | Integration | `src/api/tests/test_migrat*.py` | `uv run pytest` |
| End-to-end Kafka round-trip (api → Request) | Integration (`@pytest.mark.integration`) | `src/api/tests/reimbursement/create/test_integration.py` | `uv run pytest` |
| Publisher decision tree (insert+publish, retry, duplicate, escalation) | Unit (fakes) | `src/publisher/tests/test_processing.py` | `uv run pytest -m "not integration"` |
| Publisher consumer lifecycle (offset commit, shutdown, startup checks) | Unit | `src/publisher/tests/test_consumer.py` | same |
| Publisher end-to-end (Request → row + Reimbursement) | Integration | `src/publisher/tests/test_integration.py` | `uv run pytest` |
| `reimbursement` repository (insert, duplicate detection) | Integration (real Postgres) | `src/shared/tests/reimbursement/test_repository.py` | `uv run pytest` |
| Human-review escalation + history rendering | Unit + integration | `src/shared/tests/reimbursement/use_cases/test_send_human_review.py` | mixed |
| Failure log (never raises, truncation) | Unit | `src/shared/tests/test_failure_log.py` | `uv run pytest -m "not integration"` |
| Decision logic — auto-approve/reject/human-review rules (`agent`) | **None — not implemented yet** | — | — |

## Parallelism Assessment

| Test Type | Parallel-Safe? | Isolation Model | Evidence |
| --------- | -------------- | ----------------- | -------- |
| Postgres-backed (schema, migrations, repository) | Yes, across processes | Each run creates its own uniquely-named database (`reimbursementanalyzer_<pid>_<random>_test`) on a session-scoped server, dropped `WITH (FORCE)` afterward. The fixture was promoted to a workspace-level conftest so `api` and `shared`/`publisher` tests share it | `src/api/tests/helpers.py::disposable_database_name`, workspace-level `conftest.py::disposable_database` |
| Kafka integration (`api`) | Not verified for concurrent runs | One session-scoped `KafkaContainer` fixture shared by every test in `reimbursement/create/`; each test drains from a fresh consumer group and matches on its own marker string, but the container itself is not per-test | `src/api/tests/reimbursement/create/conftest.py::kafka_bootstrap_server` |
| Kafka integration (`publisher`) | Not verified for concurrent runs | Same pattern as `api`'s, publisher-local: one session-scoped `KafkaContainer` sized from `KAFKA_MAX_MESSAGE_BYTES`. Not hoisted to the root conftest like Postgres was — the real reason is a topic-name collision between `api`'s and `publisher`'s fixtures, not conftest resolution scoping (I8, 2026-08-08 — the previous rationale here was checked and found factually wrong: pytest conftest fixtures *do* fan out workspace-wide, which is exactly how the root-level Postgres fixture below reaches both) | `src/publisher/tests/conftest.py::kafka_bootstrap_server` |
| Unit tests (fakes only) | Yes | No shared external state; `shared.config.load_config()`'s process-wide `lru_cache` is explicitly cleared by an autouse fixture before every test | `src/shared/tests/conftest.py`, `src/api/tests/conftest.py` |

## Gate Check Commands

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | After changes with no *Kafka* dependency | `uv run pytest -m "not integration"` — still needs Docker: the `integration` marker means "needs a container *beyond* the suite's own Postgres default" (`pyproject.toml`'s marker text), not "needs no container" (corrected 2026-08-08 — V8/M2/P4/I2, 4-way corroborated: this row previously said "no DB/Kafka dependency," contradicted by line 37 above and by dozens of Postgres-backed tests in this same gate) |
| Full | Before considering a task/PR done | `uv run pytest` |
| Lint (ad hoc) | Optional sanity check, not gated | `uv run ruff check <path>` |
