# Tech Stack

## Core

- Language: Python 3.14.7 (pinned via `.python-version`; `requires-python >= 3.14.7` in every `pyproject.toml`)
- Package manager: [uv](https://docs.astral.sh/uv/) — manages both the Python toolchain and dependencies
- Workspace: uv workspace monorepo, `members = ["packages/*"]`, four packages: `api`, `publisher`, `reimbursement`, `shared`
- All four packages use the `uv_build` backend with a conventional nested src-layout (`packages/<pkg>/src/<pkg>/*.py`) — the import name matches a real physical directory, so every nested subpackage (e.g. `api.reimbursement.create`, `reimbursement.agent.nodes`) is discovered automatically and every editable install is a plain static `.pth`, resolvable by static analyzers (Pyright/Pylance/cursorpyright), not a dynamic finder (AD-031, amended).

## Key Libraries

| Library | Version | Purpose | Used in |
| ------- | ------- | ------- | ------- |
| `fastapi` | >=0.141.1 | HTTP API framework | `api` |
| `uvicorn[standard]` | >=0.52.1 | ASGI server | `api` |
| `pydantic[email]` | >=2.13.4 | Request/message validation, `EmailStr` | `shared`, `api` |
| `confluent-kafka` | >=2.15.0 | Kafka producer/consumer client (`confluent_kafka.aio.AIOProducer` for the async producer) | `api`, `shared`, `reimbursement`, `publisher` |
| `asyncpg` | >=0.31.0 | Postgres async driver — pool + statements in `shared.reimbursement.repository` | `publisher`, `reimbursement` (both real use, via `shared`) |
| `python-dotenv` | >=1.2.2 | Loads `.env` at process start (`load_dotenv()`) | `api`, `reimbursement`, `publisher` |
| `langchain` | >=1.3.14 | LLM orchestration — declared; the `agent/` decision-graph subpackage imports it but every node is still a stub | `reimbursement` |
| `langgraph` | >=1.2.10 | Agentic graph orchestration — `agent/agent.py` builds a `StateGraph` and registers nodes, but adds no edges and never compiles/invokes it | `reimbursement` |
| `watchfiles` | >=1.2.0 | Dev-mode hot reload | `reimbursement`, `publisher` |
| `yoyo-migrations` | >=9.0.0 | Plain-SQL schema migrations | `api` (`migrations` extra only) |
| `psycopg[binary]` | >=3.3.4 | Sync Postgres driver, used by the migration runner and the advisory lock | `api` (`migrations` extra only) |
| `pytest` | >=9.1.1 | Test runner (workspace-wide, root `dependency-groups.dev`) | all |
| `testcontainers[kafka,postgres]` | >=4.15.0 | Ephemeral Postgres/Kafka containers for tests | `api`, `shared`, `publisher`, `reimbursement` |
| `httpx` | >=0.28.1 | Used transitively by FastAPI's `TestClient` | `api` tests |
| `pylint` | >=4.0.6 | Declared dev dependency, no `.pylintrc`/`[tool.pylint]` config and not run in any gate — a second, equally unconfigured linter alongside `ruff` (see `CONCERNS.md`) | workspace-wide (`dependency-groups.dev`) |

## Backend

- API style: REST, single versioned prefix `/api/v1/...`, OpenAPI schema generated from the same `pydantic.TypeAdapter` that validates requests (no hand-duplicated schema).
- Database: PostgreSQL 18 (app's own), schema owned by `api`'s migrations. No ORM — raw SQL migrations via `yoyo`; runtime access is raw SQL statements via `asyncpg` (`shared.reimbursement.repository`). Used by all three services: `publisher` and `reimbursement` as before, plus `api` itself for `GET /api/v1/reimbursement` (list) and `PUT /api/v1/reimbursement/{uuid}` (approve/reject), reading/writing Postgres directly rather than only through Kafka. The pool constructor (`managed_pool`) lives in `shared.db` (relocated from `shared.reimbursement.repository`) — same construct/yield/close shape as `shared.producer.managed_producer`. `api` constructs its own pool in `main.py`'s `lifespan`, with `pool_min_size` overridden to 0 (a request-serving process has bursty, not baseline, DB usage, unlike `publisher`/`reimbursement`'s steady pool).
- Messaging: Apache Kafka 4.3.1 (KRaft mode, single node, no ZooKeeper).
- Authentication: none implemented on the public API.

## Testing

- Unit/integration: `pytest` with `Describe*`/`it_*` naming (`python_classes`/`python_functions` in root `pyproject.toml`).
- Async: `pytest.mark.anyio` (anyio's pytest plugin, not auto-mode — async tests must opt in explicitly).
- Container-backed: `testcontainers` — a real ephemeral `postgres:18` and (for one file) a Confluent Kafka image — for both unit-adjacent DB tests and marked `integration` tests.
- Detail: `docs/codebase/TESTING.md`.

## External Services

- Kafka — message backbone between `api`, `publisher`, and `reimbursement` (detail: `INTEGRATIONS.md`).
- LangFuse (self-hosted, v4) — intended tracing/eval backend for `reimbursement`'s LangGraph decision graph; provisioned in `docker-compose.yml` but not yet wired into any code.

## Commands

| Task | Command |
| ---- | ------- |
| Install everything | `uv sync --all-packages` |
| Copy local env defaults | `cp .env.sample .env` |
| Run the full stack | `docker compose up -d` |
| Stop the stack | `docker compose down -v` |
| Run all tests | `uv run pytest` |
| Run tests without Docker-heavy ones | `uv run pytest -m "not integration"` |
| Point tests at a supplied Postgres | `TEST_DATABASE_URL=postgresql://user:pw@host:5432/postgres uv run pytest` |
| Apply migrations by hand | `DATABASE_URL=postgresql://reimbursementanalyzer:$POSTGRES_PASSWORD@localhost:5433/reimbursementanalyzer uv run --extra migrations python -m api.migrate` |
| Lint (ad hoc, not gated) | `uv run ruff check <path>` |

## Local Development Setup

- `docker compose up -d` starts: the app's own Postgres (port 5433), Kafka KRaft (port 9092), the full LangFuse stack (its own Postgres/ClickHouse/Redis/MinIO), a one-shot `migrate` job, and the three workspace services (`api`, `reimbursement`, `publisher`).
- `api`, `reimbursement`, and `publisher` bind-mount their own `packages/<pkg>` directory plus `packages/shared` for hot reload in the `dev` Docker target.
- API: `http://localhost:8000` (`/health`). LangFuse: `http://localhost:3000`.
- Test suite needs no running stack — `testcontainers` starts and tears down its own throwaway Postgres/Kafka.

## Environment Configuration

| Variable | Description |
| -------- | ----------- |
| `POSTGRES_PASSWORD` | App Postgres password |
| `DATABASE_URL` | Full app Postgres DSN (compose sets this per-service; local runs must set it manually) |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka bootstrap address (`kafka:19092` inside compose, `localhost:9092` default outside it) |
| `KAFKA_SECURITY_PROTOCOL`, `KAFKA_SASL_MECHANISM`, `KAFKA_SASL_USERNAME`, `KAFKA_SASL_PASSWORD`, `KAFKA_SSL_CA_LOCATION` | Optional Kafka SASL/TLS — unset means PLAINTEXT |
| `AGENT_CONSUMER_GROUP_ID` | `reimbursement`'s Kafka consumer group id (var name and default `"agent"` unchanged by the package rename) |
| `DATABASE_POOL_MAX_SIZE` | Shared Postgres pool's max size (default `20`) — raise this in step with `PUBLISHER_ITEM_CONCURRENCY` to avoid connection starvation under load (R-005) |
| `LANGFUSE_POSTGRES_PASSWORD`, `SALT`, `ENCRYPTION_KEY`, `NEXTAUTH_SECRET`, `CLICKHOUSE_PASSWORD`, `REDIS_AUTH`, `MINIO_ROOT_PASSWORD`, `LANGFUSE_S3_*_SECRET_ACCESS_KEY` | LangFuse stack's own infra credentials |
| `LANGFUSE_INIT_PROJECT_SECRET_KEY`, `LANGFUSE_INIT_USER_PASSWORD` | LangFuse first-boot bootstrap credentials; `reimbursement`'s decision graph will authenticate with the same project key pair once wired |
| `TEST_DATABASE_URL` | Points the test suite at a supplied Postgres server instead of a throwaway container |
