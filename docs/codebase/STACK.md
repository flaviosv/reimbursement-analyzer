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
| `ecs-logging` | >=2.3.0 | ECS-JSON `logging.Formatter` — every service's structured log output, wired via `shared.logging.configure_logging()` | `shared` (all services transitively) |
| `asyncpg` | >=0.31.0 | Postgres async driver — pool + statements in `shared.reimbursement.repository` | `publisher`, `reimbursement` (both real use, via `shared`) |
| `python-dotenv` | >=1.2.2 | Loads `.env` at process start (`load_dotenv()`) | `api`, `reimbursement`, `publisher` |
| `langchain` | >=1.3.14 | LLM orchestration — `init_chat_model` constructs the two Groq-backed chat models (`extract_fields`/`analysis`, one per node) inside `build_graph()` | `reimbursement` |
| `langchain-groq` | >=1.1.3 | Groq chat-model client, resolved via `init_chat_model("groq:<model>", ...)` — replaces `langchain-ollama` (AD-032) | `reimbursement` |
| `langgraph` | >=1.2.10 | Agentic graph orchestration — `agent/agent.py` builds, wires, and compiles a real `StateGraph` (5 nodes, conditional edges), invoked from `validation.py`'s resolve path | `reimbursement` |
| `watchfiles` | >=1.2.0 | Dev-mode hot reload | `reimbursement`, `publisher` |
| `yoyo-migrations` | >=9.0.0 | Plain-SQL schema migrations | `api` (`migrations` extra only) |
| `psycopg[binary]` | >=3.3.4 | Sync Postgres driver, used by the migration runner and the advisory lock | `api` (`migrations` extra only) |
| `pytest` | >=9.1.1 | Test runner (workspace-wide, root `dependency-groups.dev`) | all |
| `testcontainers[kafka,postgres]` | >=4.15.0 | Ephemeral Postgres/Kafka containers for tests | `api`, `shared`, `publisher`, `reimbursement` |
| `httpx` | >=0.28.1 | Used transitively by FastAPI's `TestClient` | `api` tests |
| `pylint` | >=4.0.6 | Declared dev dependency, no `.pylintrc`/`[tool.pylint]` config and not run in any gate — a second, equally unconfigured linter alongside `ruff` (see `CONCERNS.md`) | workspace-wide (`dependency-groups.dev`) |
| `prometheus-client` | >=0.24.1 | Prometheus metrics — `Counter`/`Gauge`/`Histogram` objects plus `start_http_server`/`generate_latest`, each service's own default registry, no shared/federated registry across processes | `shared`, `api`, `publisher`, `reimbursement` |

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
- LangFuse (self-hosted, v4) — tracing backend for `reimbursement`'s LangGraph decision graph, wired via `langchain.CallbackHandler`; falls back to durable `failure_log` records when unreachable.
- Groq — hosted LLM inference for `reimbursement`'s two decision-graph LLM nodes (`extract_fields`, `analysis`), each its own independently-configured chat model; configured via `GROQ_API_KEY`/`AI_TIMEOUT_SECONDS` (shared) and `EXTRACT_FIELDS_MODEL_NAME`/`EXTRACT_FIELDS_TEMPERATURE`/`ANALYSIS_MODEL_NAME`/`ANALYSIS_TEMPERATURE` (per node) — replaces the self-hosted Ollama host-machine setup (AD-032).
- Prometheus — scrapes `/metrics` on all three services independently (`api`'s main HTTP port; `publisher`/`reimbursement`'s own `METRICS_PORT`); inbound-only, no outbound client call from this codebase (detail: `INTEGRATIONS.md`, metric catalog: `docs/METRICS.md`).

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
- `.env` is bind-mounted read-only into `api`/`publisher`/`reimbursement` at container runtime (`./.env:/app/.env:ro`) — deliberately not into `migrate`, which only reads `DATABASE_URL`, already supplied via its own compose `environment:` block. Each service's own `docker-compose.yml` `environment:` block is otherwise pruned to a topology-only allowlist (values like `kafka:19092` that are only ever correct inside the Docker network) — see `INTEGRATIONS.md` for the full per-integration breakdown.

## Environment Configuration

| Variable | Description |
| -------- | ----------- |
| `POSTGRES_PASSWORD` | App Postgres password |
| `DATABASE_URL` | Full app Postgres DSN (compose sets this per-service; local runs must set it manually) |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka bootstrap address (`kafka:19092` inside compose, `localhost:9092` default outside it) |
| `KAFKA_SECURITY_PROTOCOL`, `KAFKA_SASL_MECHANISM`, `KAFKA_SASL_USERNAME`, `KAFKA_SASL_PASSWORD`, `KAFKA_SSL_CA_LOCATION` | Optional Kafka SASL/TLS — unset means PLAINTEXT |
| `AGENT_CONSUMER_GROUP_ID` | `reimbursement`'s Kafka consumer group id (var name and default `"agent"` unchanged by the package rename) |
| `GROQ_API_KEY` | Groq credential shared by both LLM nodes' models — required, fails fast if unset (AD-032) |
| `AI_TIMEOUT_SECONDS` | Shared HTTP call timeout for both nodes' Groq clients — optional, defaults to `30.0` |
| `EXTRACT_FIELDS_MODEL_NAME` | Groq model name for the `extract_fields` node — required, fails fast if unset |
| `EXTRACT_FIELDS_TEMPERATURE` | `extract_fields`'s model temperature — optional, defaults to `0.0` |
| `ANALYSIS_MODEL_NAME` | Groq model name for the `analysis` node — required, fails fast if unset |
| `ANALYSIS_TEMPERATURE` | `analysis`'s model temperature — optional, defaults to `0.0` |
| `DATABASE_POOL_MAX_SIZE` | Shared Postgres pool's max size (default `20`) — raise this in step with `PUBLISHER_ITEM_CONCURRENCY` to avoid connection starvation under load (R-005) |
| `LOG_LEVEL` | Root logger level for all three services — one of `debug`/`info`/`warning`/`error`/`critical`, case-insensitive; optional, defaults to `debug`; an invalid value falls back to `debug` with a warning |
| `METRICS_PORT` | Standalone `/metrics` HTTP port `publisher` (default `9101`) and `reimbursement` (default `9102`) each bind at startup via `shared.metrics.start_metrics_server`; `api` has no such var — its `/metrics` rides the existing FastAPI app port instead. No auth on either (see `CONCERNS.md`) |
| `LANGFUSE_POSTGRES_PASSWORD`, `SALT`, `ENCRYPTION_KEY`, `NEXTAUTH_SECRET`, `CLICKHOUSE_PASSWORD`, `REDIS_AUTH`, `MINIO_ROOT_PASSWORD`, `LANGFUSE_S3_*_SECRET_ACCESS_KEY` | LangFuse stack's own infra credentials |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | LangFuse project key pair `reimbursement`'s decision graph authenticates with — read by `langfuse.langchain.CallbackHandler()` straight from the process environment. `LANGFUSE_PUBLIC_KEY` must stay in sync with `docker-compose.yml`'s `x-langfuse-public-key` anchor, enforced by `packages/api/tests/test_dotenv_config_parity.py` |
| `LANGFUSE_INIT_PROJECT_SECRET_KEY`, `LANGFUSE_INIT_USER_PASSWORD` | LangFuse first-boot bootstrap credentials for the `langfuse-web` service |
| `TEST_DATABASE_URL` | Points the test suite at a supplied Postgres server instead of a throwaway container |
