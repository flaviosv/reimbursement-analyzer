# ReimbursementAnalyzer

Mission-critical, event-driven service that decides the outcome of expense reimbursement requests in a financial domain. For each submitted request it produces one of three decisions — auto-approve, auto-reject (with justification), or route to human review — using a combination of deterministic business rules and a probabilistic (LLM/SLM-based) evaluation layer. Because decisions affecting money are partly driven by probabilistic LLM output, full traceability and auditability of every decision (automated or human) is a hard requirement.

> This project is a technical test for ReimbursementAnalyzer.

See [`docs/SCOPE.md`](docs/SCOPE.md) for the full requirements, approval policy, entities, API contracts, and architectural decisions.

## Project layout

A [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) monorepo with a shared kernel and three independently deployable services:

- `src/api` — public HTTP API (FastAPI)
- `src/agent` — LLM-based evaluation layer, consumes from Kafka (LangChain / LangGraph)
- `src/publisher` — consumes requests, persists them, republishes for downstream processing
- `src/shared` — shared kernel: models and code common to the services above

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (manages Python itself — no separate Python install needed)
- [Docker](https://docs.docker.com/get-docker/) and Docker Compose

## Install

```bash
# Install the pinned Python version and sync every workspace member
uv sync --all-packages

# Copy the local-dev environment defaults
cp .env.sample .env
```

## Run

```bash
docker compose up -d
```

This starts the app's Postgres, Kafka (KRaft, single node), the LangFuse observability stack, and the three services (`api`, `agent`, `publisher`).

- API: http://localhost:8000 (health check at `/health`)
- LangFuse: http://localhost:3000

```bash
docker compose down -v
```

## Database migrations

The schema is owned by the `api` package and applied by a one-shot `migrate` service that runs before `api`, `agent`, or `publisher` start — they each wait on `service_completed_successfully`, so no service ever sees an unmigrated database.

Migrations are plain SQL managed by [yoyo](https://ollycope.com/software/yoyo/latest), living inside the installed package at `src/api/src/api/migrations/` so they ship in the wheel and are available in the production image too.

| Migration | Creates |
| --------- | ------- |
| `0001.create-reimbursement` | `reimbursement` table, `set_updated_at()` trigger, status index |
| `0002.create-human-review` | `human_review` table (append-only), lookup index |

Applying them happens automatically on `docker compose up`. To run them by hand:

```bash
DATABASE_URL=postgresql://reimbursementanalyzer:$POSTGRES_PASSWORD@localhost:5433/reimbursementanalyzer \
  uv run python -m api.migrate
```

Re-running is safe — already-applied migrations are skipped, and a PostgreSQL advisory lock serialises concurrent runners.

To add a migration, create `NNNN.name.sql` plus a matching `NNNN.name.rollback.sql` in the migrations directory, and declare its predecessor with a `-- depends:` header.

## Tests

The suite exercises the migrations against a real PostgreSQL 18 instance — every `CHECK`, `UNIQUE`, and foreign key is asserted to reject its violating row, since an unenforced constraint reads as a guarantee.

```bash
# The tests need the database running, but not the rest of the stack
docker compose up -d postgres

uv run pytest
```

Tests use their own `reimbursementanalyzer_test` database, created and dropped by the suite itself. A guard refuses to run against any database whose name does not end in `_test`, so a mis-set `TEST_DATABASE_URL` cannot touch development data. Override the target with:

```bash
TEST_DATABASE_URL=postgresql://user:pw@host:5433/something_test uv run pytest
```
