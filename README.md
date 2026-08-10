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

Fill up all variables in .env as per necessity

### Groq API key

Every other value in `.env.sample` is a working local-dev placeholder, but `GROQ_API_KEY` is not

To get one:

1. Go to [console.groq.com](https://console.groq.com) and sign up (email or Google SSO; free, no credit card required) or log in.
2. Open [API Keys](https://console.groq.com/keys) in the console sidebar.
3. Click **Create API Key**, give it a name (e.g. `reimbursementanalyzer-local`), and submit.
4. Copy the key immediately — it's shown once. Paste it into `.env` as `GROQ_API_KEY=<your key>`.

## Run

```bash
docker compose up -d
```

`api`, `publisher`, and `reimbursement` bind-mount their own `packages/<pkg>` (plus `packages/shared`) and hot-reload on change — `uvicorn --reload` for `api`, [watchfiles](https://watchfiles.helpmanual.io/) for `publisher`/`reimbursement` — so a plain `up -d` already picks up source edits, no rebuild needed.

What isn't bind-mounted is the venv baked into the image at build time, so it only goes stale after something that changes *that*: a `pyproject.toml`, `uv.lock`, or a `Dockerfile`. Rebuild after pulling or making one of those changes:

```bash
docker compose up -d --build
```

This starts the app's Postgres, Kafka (KRaft, single node), the LangFuse observability stack, and the three services (`api`, `publisher`, `reimbursement`).

| Service | Address | Notes |
| ------- | ------- | ----- |
| API | http://localhost:8000 | Health check at `/health` |
| App Postgres | `localhost:5433` | Remapped off the default `5432` so it doesn't clash with a locally-running Postgres |
| Kafka | `localhost:9092` | Bootstrap address for a client running outside Compose |
| LangFuse | http://localhost:3000 | Web UI; sign in with `admin@reimbursementanalyzer.local` / `LANGFUSE_INIT_USER_PASSWORD` |

LangFuse's own Postgres/ClickHouse/Redis/MinIO are also exposed on their default ports (`5432`, `8123`/`9000`, `6379`, `9090`/`9091`) but are internal to its stack — not meant to be used directly.

A Postman collection for manually exercising the API is available at [`docs/misc/ReimbursementAnalyzer.postman_collection.json`](docs/misc/ReimbursementAnalyzer.postman_collection.json).

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

Migrations are plain SQL managed by [yoyo](https://ollycope.com/software/yoyo/latest), living inside the installed package at `packages/api/src/api/migrations/` so they ship in the wheel.

The DDL tooling is deliberately *not* in the request-serving image. `psycopg` and `yoyo` sit in an optional `migrations` extra that only the Dockerfile's `migrate` stage installs, so nothing on the API's hot path can execute schema changes, and the API image is free of `psycopg[binary]`'s vendored `libssl`/`libpq` — which a base-image rebuild would never patch.

| Migration | Creates |
| --------- | ------- |
| `0001.create-reimbursement` | `reimbursement` table, uniqueness index, listing index |
| `0002.create-human-review` | `human_review` table, lookup index, append-only trigger |

Applying them happens automatically on `docker compose up`. To run them by hand:

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

### End-to-end suite

`tests/e2e/` drives a real, already-running `docker compose` stack through its external surfaces only (HTTP for `api`, a direct Kafka producer for the 3 branches that need a hand-crafted message) — no `TestClient`, no fakes, real Groq calls. It's excluded from every other gate above (`@pytest.mark.e2e`, registered with a default `-m "not e2e"` in `pyproject.toml`'s `addopts`), since it's slow, costs real API calls, and needs external state up first:

```bash
cp .env.sample .env   # if not already done, with a real GROQ_API_KEY filled in
docker compose up -d
uv run pytest -m e2e
```

The suite never starts or stops the stack itself — it polls `api`'s `/health`, Kafka, and LangFuse for a bounded timeout and fails with a named diagnostic if one isn't reachable, rather than shelling out `docker compose up` on a developer's behalf. A real `GROQ_API_KEY` in `.env` is required; the suite fails fast with a clear message if it's unset, rather than hanging on a live call. Each run creates its own uuid-suffixed `request_id`s but leaves those rows in the persistent dev database — unlike every other integration test above, this one has no ephemeral container to tear down, so repeated local runs accumulate data (accepted, dev-only).

Passing an explicit `-m` on the command line replaces `addopts`' own default rather than adding to it, so `uv run pytest -m "not integration"` also collects `e2e`-marked tests once any exist — use `uv run pytest -m "not integration and not e2e"` for the Kafka-free-but-no-real-stack gate instead.
