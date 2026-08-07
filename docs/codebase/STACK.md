# Tech stack

## Core

- Language: Python 3.14.7 (pinned in `.python-version`; every package
  requires `>=3.14.7`)
- Package manager: uv (workspace monorepo, `uv_build` backend
  `>=0.12.2,<0.13.0`)
- Runtime containers: `python:3.14.7-slim`, multi-stage Dockerfiles with
  `dev` (hot reload) and `prod` targets

## Key libraries

Versions are lower bounds from the package manifests; exact resolutions live
in `uv.lock`. LangChain and LangGraph idioms verified against
docs.langchain.com via Context7 on 2026-08-07; the rest are evidenced by
in-repo usage.

| Library | Version | Used by | Purpose / modern usage |
| ------- | ------- | ------- | ---------------------- |
| asyncpg | >=0.31.0 | agent, publisher | PostgreSQL async driver (no ORM; not yet used in code) |
| confluent-kafka | >=2.15.0 | agent, api, publisher | Kafka clients; config-dict `Consumer` + `poll()` loop |
| fastapi | >=0.141.1 | api | HTTP API; decorator routes with `response_model` |
| langchain | >=1.3.14 | agent | v1 API: `create_agent(model, tools, response_format=PydanticModel)` returns `structured_response` |
| langgraph | >=1.2.10 | agent | v1 API: `StateGraph` + `add_node`/`add_edge` + `compile()` |
| pydantic | >=2.13.4 | all | v2 API: `BaseModel`, `model_validate_json()` |
| uvicorn[standard] | >=0.52.1 | api | ASGI server (`--reload` in dev target) |
| watchfiles | >=1.2.0 | agent, publisher | Dev-only process restart for the plain-script consumers |

## Backend

- API style: REST (FastAPI), planned under `/api/v1/`
- Authentication: none (deliberate scope decision, `docs/SCOPE.md`)
- Database: PostgreSQL 18 via asyncpg; no ORM, no migrations yet
- Messaging: Apache Kafka 4.3.1, KRaft single node

## External services

- LLM tracing/observability: LangFuse v4 (detail in `INTEGRATIONS.md`)

## Commands

| Task | Command |
| ---- | ------- |
| Copy local env defaults | `cp .env.sample .env` |
| Install (Python + all packages) | `uv sync --all-packages` |
| Start the full stack | `docker compose up -d` |
| Stop and wipe volumes | `docker compose down -v` |

No test, lint, or format commands exist yet (see `TESTING.md` and
`CONCERNS.md`).

## Local development setup

`docker compose up -d` starts: app Postgres, Kafka, the LangFuse stack (its
own Postgres 17, ClickHouse, Redis, MinIO), and the three services. Service
containers use the `dev` Dockerfile target: `src/<service>` and `src/shared`
are bind-mounted, with reload via `uvicorn --reload` (api) or `watchfiles`
(agent, publisher). LangFuse auto-provisions an org, project, admin user, and
API key pair on first boot (`LANGFUSE_INIT_*` in `docker-compose.yml`).

| Endpoint | URL |
| -------- | --- |
| API (health at `/health`) | http://localhost:8000 |
| App Postgres | localhost:5433 |
| ClickHouse | localhost:8123 / localhost:9000 |
| Kafka | localhost:9092 |
| LangFuse Postgres | localhost:5432 |
| LangFuse web | http://localhost:3000 |
| MinIO (S3 / console) | localhost:9090 / localhost:9091 |
| Redis | localhost:6379 |

## Environment configuration

Names only — values live in `.env` (gitignored); `.env.sample` documents
dev-only placeholders. Compose additionally injects `DATABASE_URL`,
`KAFKA_BOOTSTRAP_SERVERS`, `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, and
`LANGFUSE_SECRET_KEY` into the service containers.

| Variable | Description |
| -------- | ----------- |
| CLICKHOUSE_PASSWORD | LangFuse ClickHouse password |
| ENCRYPTION_KEY | LangFuse encryption key (generate with `openssl rand -hex 32`) |
| LANGFUSE_INIT_PROJECT_SECRET_KEY | Auto-provisioned LangFuse API secret; the agent reuses it |
| LANGFUSE_INIT_USER_PASSWORD | LangFuse admin user password |
| LANGFUSE_POSTGRES_PASSWORD | LangFuse Postgres password |
| LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY | MinIO secret for event uploads |
| LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY | MinIO secret for media uploads |
| MINIO_ROOT_PASSWORD | MinIO root password |
| NEXTAUTH_SECRET | LangFuse web auth secret |
| POSTGRES_PASSWORD | App Postgres password |
| REDIS_AUTH | LangFuse Redis password |
| SALT | LangFuse hashing salt |
