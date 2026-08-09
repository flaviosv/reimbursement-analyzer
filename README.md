# ReimbursementAnalyzer

Mission-critical, event-driven service that decides the outcome of expense reimbursement requests in a financial domain. For each submitted request it produces one of three decisions — auto-approve, auto-reject (with justification), or route to human review — using a combination of deterministic business rules and a probabilistic (LLM/SLM-based) evaluation layer. Because decisions affecting money are partly driven by probabilistic LLM output, full traceability and auditability of every decision (automated or human) is a hard requirement.

> This project is a technical test for ReimbursementAnalyzer.

See [`docs/SCOPE.md`](docs/SCOPE.md) for the full requirements, approval policy, entities, API contracts, and architectural decisions.

## Project layout

A [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) monorepo with a shared kernel and three independently deployable services:

- `packages/api` — public HTTP API (FastAPI)
- `packages/publisher` — consumes requests, persists them, republishes for downstream processing
- `packages/reimbursement` — LLM-based evaluation layer, consumes from Kafka (LangChain / LangGraph); the decision graph is implemented and wired into the consume path
- `packages/shared` — shared kernel: models and code common to the services above

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (manages Python itself — installs the pinned `3.14.7` from `.python-version` automatically, no separate Python install needed)
- [Docker](https://docs.docker.com/get-docker/) with the Compose plugin (the `docker compose` subcommand used below — not the standalone `docker-compose` v1 binary)
- A few GB of free RAM for Docker: the local stack is 12 containers — the app's Postgres, Kafka, the three services, and LangFuse's own Postgres/ClickHouse/Redis/MinIO

## Install

```bash
# Install the pinned Python version and sync every workspace member
uv sync --all-packages

# Copy the local-dev environment defaults
cp .env.sample .env
```

The defaults in `.env.sample` are self-contained placeholders and work as-is for local dev — no edits needed to bring the stack up. They are deliberately weak/guessable and must never be reused outside a local machine (see the warning at the top of the file).

Every service loads `.env` through [python-dotenv](https://pypi.org/project/python-dotenv/) at start-up, so running one directly (`uv run python -m api.migrate`) picks up the same configuration Compose injects. Real environment variables always win over the file, so a container's settings are never overridden by a stray local `.env`.

## Run

```bash
docker compose up -d
```

`api`, `publisher`, and `reimbursement` bind-mount their own `packages/<pkg>` (plus `packages/shared`) and hot-reload on change — `uvicorn --reload` for `api`, [watchfiles](https://watchfiles.helpmanual.io/) for `publisher`/`reimbursement` — so a plain `up -d` already picks up source edits, no rebuild needed.

What isn't bind-mounted is the venv baked into the image at build time, so it only goes stale after something that changes *that*: a `pyproject.toml`, `uv.lock`, or a `Dockerfile`. Rebuild after pulling or making one of those changes:

```bash
docker compose up -d --build
```

Compose only builds an image the first time it's missing, never on its own after a later change — skip `--build` following a dependency/packaging change and the stack runs against a stale image (e.g. `migrate` failing with `ModuleNotFoundError: No module named 'api'` because the image predates a package-layout change). Docker's layer cache keeps the flag cheap even when nothing changed, so pass it whenever unsure.

This starts the app's Postgres, Kafka (KRaft, single node), the LangFuse observability stack, and the three services (`api`, `publisher`, `reimbursement`).

| Service | Address | Notes |
| ------- | ------- | ----- |
| API | http://localhost:8000 | Health check at `/health` |
| App Postgres | `localhost:5433` | Remapped off the default `5432` so it doesn't clash with a locally-running Postgres |
| Kafka | `localhost:9092` | Bootstrap address for a client running outside Compose |
| LangFuse | http://localhost:3000 | Web UI; sign in with `LANGFUSE_INIT_USER_PASSWORD` |

LangFuse's own Postgres/ClickHouse/Redis/MinIO are also exposed on their default ports (`5432`, `8123`/`9000`, `6379`, `9090`/`9091`) but are internal to its stack — not meant to be used directly.

Verify the stack came up healthy:

```bash
docker compose ps        # every service should be "healthy" or "Exited (0)" for the one-shot migrate job
curl http://localhost:8000/health
```

On a freshly created stack, `publisher` and `reimbursement` may log a few `UNKNOWN_TOPIC_OR_PART` errors before their topics exist. Kafka auto-creates a topic on first *produce*, not on a consumer's `subscribe()`, so the topics don't exist until `api` handles its first request. This clears itself at that point — nothing to fix.

```bash
docker compose down -v
```

## Database migrations

The schema is owned by the `api` package and applied by a one-shot `migrate` service that runs before `api`, `publisher`, or `reimbursement` start — they each wait on `service_completed_successfully`, so no service ever sees an unmigrated database.

Migrations are plain SQL managed by [yoyo](https://ollycope.com/software/yoyo/latest), living inside the installed package at `packages/api/src/api/migrations/` so they ship in the wheel.

The DDL tooling is deliberately *not* in the request-serving image. `psycopg` and `yoyo` sit in an optional `migrations` extra that only the Dockerfile's `migrate` stage installs, so nothing on the API's hot path can execute schema changes, and the API image is free of `psycopg[binary]`'s vendored `libssl`/`libpq` — which a base-image rebuild would never patch.

| Migration | Creates |
| --------- | ------- |
| `0001.create-reimbursement` | `reimbursement` table, uniqueness index, listing index |
| `0002.create-human-review` | `human_review` table, lookup index, append-only trigger |

`updated_at` is owned by the application, not by a trigger: it defaults to `now()` on insert, and whoever updates a row sets it. The `reimbursement` service's staleness rule reads that column, so the writer decides its value.

Applying them happens automatically on `docker compose up`. To run them by hand:

```bash
DATABASE_URL=postgresql://reimbursementanalyzer:$POSTGRES_PASSWORD@localhost:5433/reimbursementanalyzer \
  uv run --extra migrations python -m api.migrate
```

Re-running is safe: already-applied migrations are skipped, and concurrent runners are serialised by a session-scoped PostgreSQL **advisory** lock held on a connection of its own.

The choice of lock is load-bearing rather than incidental. yoyo's built-in lock is a row in a table, removed in a `finally` clause that a `SIGKILL`, a lost node, or an evicted pod never reaches — so a runner that dies mid-migration leaves the row behind permanently, and since every service waits on the migrate job completing, the whole stack stays down until someone runs `yoyo break-lock` by hand. An advisory lock is released by PostgreSQL the moment the runner's connection drops, so that failure mode does not exist. Any lock row orphaned by an older run is cleared automatically, which is safe precisely because the advisory lock proves no other runner is alive.

To add a migration, create `NNNN.name.sql` plus a matching `NNNN.name.rollback.sql` in the migrations directory, and declare its predecessor with a `-- depends:` header.

## Tests

The suite covers the *schema*, not the migration machinery: every `CHECK`, `UNIQUE`, foreign key, and trigger is asserted to reject its violating row, since an unenforced constraint reads as a guarantee. Applying migrations is bootstrap — if it breaks, nothing starts at all.

```bash
uv run pytest
```

Tests are grouped `describe`/`it` style — `Describe*` classes holding `it_*` methods — so a failure reads as a sentence: `DescribeUniqueness::it_rejects_a_case_varied_submitter`.

That is the whole setup. [Testcontainers](https://testcontainers.com/) starts a throwaway `postgres:18` container for the session and tears it down afterwards, so the suite needs no running stack, shares nothing between runs, and cannot reach a real database. Docker must be running; if it is not, the suite says so in one line rather than reporting dozens of failures.

To point the suite at a server you supply instead — a CI service container, for example:

```bash
TEST_DATABASE_URL=postgresql://user:pw@host:5432/postgres uv run pytest
```

The URL names a *server*, not a database. Every run creates its own `reimbursementanalyzer_<pid>_<random>_test` database on it and drops it afterwards, so two runs — or two `pytest-xdist` workers — against one server cannot collide. The suite drops databases `WITH (FORCE)`, which terminates whatever is attached to them, so it only ever touches a name it generated and refuses any target not ending in `_test`.
