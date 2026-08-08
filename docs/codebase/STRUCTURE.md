# Project Structure

**Root:** repo root — a uv workspace monorepo (`members = ["src/*"]`)

## Directory Tree

```
.
├── docs/
│   ├── SCOPE.md              # Full requirements, approval policy, contracts
│   ├── SCOPE_GAP_ANALYSIS.md
│   ├── original/sample.json  # Sample reimbursement request data
│   ├── assets/                # Diagrams referenced from docs
│   └── codebase/              # This context set
├── .specs/                    # tlc-spec-driven working memory (decisions, feature specs)
├── src/
│   ├── api/                   # Public HTTP API (FastAPI) — virtual workspace member
│   │   ├── src/                 # Loose modules, no package dir (flat layout)
│   │   │   ├── main.py            # App entrypoint, lifespan (producer construction)
│   │   │   ├── dependencies.py    # FastAPI route dependency accessors
│   │   │   ├── errors.py          # App-wide exception handlers, MessageResponse
│   │   │   ├── migrate.py         # Migration runner (advisory-lock serialised)
│   │   │   ├── migrations/        # Plain SQL migrations (yoyo)
│   │   │   └── reimbursement/
│   │   │       └── create/          # Vertical slice: POST /api/v1/reimbursement
│   │   │           ├── route.py       # Endpoint
│   │   │           ├── payload.py     # Streaming body-size cap
│   │   │           ├── validation.py  # Batch validation (TypeAdapter)
│   │   │           └── producer.py    # Envelope building + publish wrapper
│   │   ├── tests/                # Mirrors src/ layout
│   │   └── Dockerfile            # Multi-stage: builder / dev / migrate / prod
│   ├── agent/                 # LLM evaluation layer — stub only
│   │   └── src/agent/consumer.py  # Placeholder Kafka consumer
│   ├── publisher/              # Consume Request, persist, publish Reimbursement — implemented
│   │   ├── src/                  # Loose modules, no package dir (flat layout, like api)
│   │   │   ├── consumer.py         # Composition root: pool/producer/consumer lifecycle, offset commit
│   │   │   └── processing.py       # Decision tree: insert+publish, retry/requeue, escalation, duplicates
│   │   └── tests/
│   │       ├── conftest.py         # Publisher-local Kafka container fixture
│   │       ├── fakes.py            # Test doubles as classes (FakeProducer, FakePool, RealPool)
│   │       ├── test_consumer.py
│   │       ├── test_processing.py
│   │       └── test_integration.py   # Real Kafka + Postgres round trip
│   └── shared/                 # Shared kernel, installable package
│       └── src/shared/
│           ├── config.py          # KafkaConfig, DatabaseConfig, FailureLogConfig, PublisherConfig, load_config()
│           ├── errors.py          # Cross-service exception classes, sanitize()
│           ├── failure_log.py     # Last-resort structured JSON log (critical level)
│           ├── models.py          # Cross-service pydantic models, AttemptError, ReimbursementEnvelope
│           ├── producer.py        # Generic Kafka publish + producer lifecycle
│           └── reimbursement/     # Domain slice: persistence for the reimbursement table
│               ├── repository.py           # managed_pool(), insert_pending, insert_human_review, is_duplicate
│               └── use_cases/
│                   └── send_human_review.py  # render_history(), send_human_review()
├── docker-compose.yml          # Full local stack (app infra + LangFuse + 3 services)
├── pyproject.toml              # Workspace root — pytest config, dev dependency group
└── uv.lock
```

## Module Organization

### `api/reimbursement/create`

- **Purpose:** the one implemented vertical slice — accepts, validates, and publishes a batch of reimbursement requests.
- **Location:** `src/api/src/reimbursement/create/`
- **Key files:** `route.py` (endpoint + OpenAPI schema derivation), `payload.py` (streaming byte-cap), `validation.py` (`BATCH_ADAPTER`, `validate_batch`), `producer.py` (`build_envelope`, `publish`).
- **Pattern:** future operations (e.g. a listing endpoint) are expected to land as sibling directories under `reimbursement/`, each self-contained with its own tests directory mirror.

### `api` root (`src/api/src/*.py`)

- **Purpose:** app-wide infrastructure that no single vertical slice owns — FastAPI app construction, lifespan/producer wiring, the app-wide error contract, and the migration runner.
- **Location:** `src/api/src/{main,dependencies,errors,migrate}.py`.

### `shared`

- **Purpose:** code genuinely reusable across `api`, `agent`, and `publisher` — cross-service pydantic models, exception classes, Kafka config/publish primitives, and (new) the `reimbursement` domain's persistence layer.
- **Location:** `src/shared/src/shared/`.
- **Key files:** `config.py` (`load_config()` — single cached env-config entrypoint), `producer.py` (`managed_producer`, `publish` — technology-specific but domain-agnostic), `models.py` (`ReimbursementRequest`, `RequestEnvelope`, `ReimbursementEnvelope`, `AttemptError`, `SampleMessage`, `HealthStatus`), `errors.py` (`PayloadTooLarge`, `BatchInvalid`, `PublishFailed`, `sanitize()`), `failure_log.py` (`write()` — last-resort structured log), `reimbursement/repository.py` + `reimbursement/use_cases/send_human_review.py` (asyncpg persistence + the human-review escalation action).

### `publisher`

- **Purpose:** consumes `Request`, creates one `reimbursement` row per item, and publishes one `Reimbursement` message per item — the middle link between `api`'s intake and the Agent's (not yet built) decision layer.
- **Location:** `src/publisher/src/{consumer,processing}.py`, flattened like `api` (no `publisher/` package dir, `package = false`).
- **Key files:** `consumer.py` (Kafka consumer lifecycle, offset commit), `processing.py` (the decision tree — insert+publish, retry-with-requeue, `retry > 3` escalation, duplicate detection). Fully implemented and tested — see `TESTING.md`.

### `agent`

- **Purpose (intended):** consumes `Reimbursement`, runs the LLM/rules decision layer (LangGraph), records the decision.
- **Current state:** still a single `consumer.py` that subscribes to a placeholder topic (`sample-topic`) and prints whatever `shared.models.SampleMessage` it receives — structural scaffolding only, not real business logic. Unlike `publisher`, this has not changed.

## Where Things Live

**POST /api/v1/reimbursement:**
- Route + OpenAPI: `src/api/src/reimbursement/create/route.py`
- Validation: `src/api/src/reimbursement/create/validation.py`
- Body-size enforcement: `src/api/src/reimbursement/create/payload.py`
- Kafka publish: `src/api/src/reimbursement/create/producer.py` → `src/shared/src/shared/producer.py`
- Cross-cutting config: `src/shared/src/shared/config.py`

**Database schema:**
- Migrations: `src/api/src/migrations/*.sql`
- Runner: `src/api/src/migrate.py`

## Special Directories

**`.specs/`:** `tlc-spec-driven`'s working memory — `STATE.md` (append-only architectural decision log, `AD-NNN`), `features/<name>/` (per-feature spec/design/tasks). Not part of this context set; see the root `CLAUDE.md`/`AGENTS.global.md` for how to use it.

**`docs/original/`:** the sample reimbursement request dataset (`sample.json`) that several documented sizing decisions (body ceiling, batch cardinality) are calibrated against.

## Monorepo Package Map

| Package | Path | Responsibility |
| ------- | ---- | -------------- |
| `api` | `src/api` | Public HTTP API — intake, validation, publish to Kafka |
| `agent` | `src/agent` | LLM-based decision evaluation (stub) |
| `publisher` | `src/publisher` | Consume `Request`, persist to `reimbursement`, publish `Reimbursement` (implemented) |
| `shared` | `src/shared` | Shared kernel: models, config, Kafka producer, `reimbursement` persistence |
