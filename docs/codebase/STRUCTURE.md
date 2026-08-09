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
│   ├── api/                   # Public HTTP API (FastAPI) — installed package via setuptools package-dir (AD-031)
│   │   ├── src/                 # api's own modules directly (no wrapping api/ folder on disk; dotted-importable as api.*)
│   │   │   ├── main.py            # App entrypoint, lifespan (producer + DB pool construction)
│   │   │   ├── dependencies.py    # FastAPI route dependency accessors (get_producer, get_pool)
│   │   │   ├── errors.py          # App-wide exception handlers, MessageResponse
│   │   │   ├── migrate.py         # Migration runner (advisory-lock serialised)
│   │   │   ├── migrations/        # Plain SQL migrations (yoyo)
│   │   │   └── reimbursement/
│   │   │       ├── create/          # Vertical slice: POST /api/v1/reimbursement
│   │   │       │   ├── route.py       # Endpoint
│   │   │       │   ├── payload.py     # Streaming body-size cap
│   │   │       │   ├── validation.py  # Batch validation (TypeAdapter)
│   │   │       │   └── producer.py    # Envelope building + publish wrapper
│   │   │       ├── list/            # Vertical slice: GET /api/v1/reimbursement
│   │   │       │   ├── route.py       # Endpoint
│   │   │       │   ├── params.py      # LimitQuery/OffsetQuery, parse_status_filter
│   │   │       │   └── response.py    # ReimbursementListItem.from_record, response shaping
│   │   │       └── update/          # Vertical slice: PUT /api/v1/reimbursement/{uuid}
│   │   │           ├── route.py       # Endpoint
│   │   │           └── validation.py  # ApproveReview/RejectReview discriminated union
│   │   ├── tests/                # Mirrors src/ layout; helpers.py centralizes shared test builders
│   │   └── Dockerfile            # Multi-stage: builder / dev / migrate / prod
│   ├── reimbursement/          # Consume Reimbursement, resolve by uuid (implemented) + LangGraph decision scaffold (not wired)
│   │   ├── src/                  # reimbursement's own modules directly (no wrapping package dir; package-dir maps the name)
│   │   │   ├── consumer.py         # Composition root: pool/producer/consumer lifecycle, offset commit
│   │   │   ├── validation.py       # Decision tree: resolve, staleness guard, requeue, retry-ceiling escalation
│   │   │   ├── config.py           # AgentConfig (consumer_group_id, consume_timeout_seconds), load_agent_config()
│   │   │   ├── schema.py           # State TypedDict — reimbursement: shared.models.Reimbursement
│   │   │   └── agent/                # LangGraph decision graph — scaffolded, not invoked from validation.py yet
│   │   │       ├── agent.py            # StateGraph builder — nodes registered, no edges/compile/invoke yet
│   │   │       ├── nodes/               # extract_fields, validate, apply_policies, analysis, apply_agent_decision
│   │   │       │                        # — every node is a stub returning its own name, no logic yet
│   │   │       └── prompts/             # extract_fields.py, analysis.py — both empty placeholders
│   │   └── tests/                 # Covers consumer/validation/config only — the agent/ scaffold has no tests yet
│   │       ├── conftest.py         # Kafka container fixture, local to this package
│   │       ├── agent_fakes.py      # FakePool/FakeConnection; re-exports shared.testing.FakeProducer
│   │       ├── test_config.py
│   │       ├── test_consumer.py
│   │       ├── test_validation.py
│   │       └── test_integration.py   # Real Kafka + Postgres round trip
│   ├── publisher/              # Consume Request, persist, publish Reimbursement — implemented
│   │   ├── src/                  # publisher's own modules directly (no wrapping publisher/ folder on disk; dotted-importable as publisher.*, AD-031)
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
│           ├── db.py              # managed_pool() — Postgres pool lifecycle (relocated from reimbursement/repository.py, AD-029)
│           ├── errors.py          # Cross-service exception classes, sanitize()
│           ├── failure_log.py     # Last-resort structured JSON log (critical level)
│           ├── models.py          # Cross-service pydantic models, AttemptError, ReimbursementEnvelope, Reimbursement
│           ├── producer.py        # Generic Kafka publish + producer lifecycle
│           └── reimbursement/     # Domain slice: persistence + use cases for the reimbursement/human_review tables
│               ├── repository.py           # insert_pending, insert_human_review, get_by_uuid, update_human_review, fetch_reimbursement_page, approve, reject, find_reimbursement_state, record_human_review_decision, is_duplicate
│               └── use_cases/
│                   ├── send_human_review.py    # render_history(), send_human_review(), escalate_existing()
│                   ├── publish_pending.py      # publish_pending() — insert_pending + Kafka publish as one unit (publisher's own transaction)
│                   ├── list_reimbursements.py  # list_reimbursements() — status whitelist + limit/offset gates
│                   └── review_reimbursement.py # approve_reimbursement(), reject_reimbursement() — the approve/reject transaction
│           ├── signals.py         # install_shutdown_handlers() — SIGINT/SIGTERM → asyncio.Event (used by reimbursement only so far)
│           └── testing.py         # FakeProducer — the one test double genuinely reused across service test suites
├── docker-compose.yml          # Full local stack (app infra + LangFuse + 3 services)
├── pyproject.toml              # Workspace root — pytest config, dev dependency group
└── uv.lock
```

## Module Organization

### `api/reimbursement/create`

- **Purpose:** accepts, validates, and publishes a batch of reimbursement requests.
- **Location:** `src/api/src/reimbursement/create/`
- **Key files:** `route.py` (endpoint + OpenAPI schema derivation), `payload.py` (streaming byte-cap), `validation.py` (`BATCH_ADAPTER`, `validate_batch`), `producer.py` (`build_envelope`, `publish`).
- **Pattern:** each operation lands as a sibling directory under `reimbursement/`, self-contained with its own tests directory mirror — see `list/` and `update/` below, added following this exact pattern.

### `api/reimbursement/list`

- **Purpose:** `GET /api/v1/reimbursement` — lists reimbursements, optionally filtered by status (comma-separated, single query param only), paginated (`limit`/`offset`), each row paired with its most recent `human_review` entry if any.
- **Location:** `src/api/src/reimbursement/list/`
- **Key files:** `route.py` (endpoint), `params.py` (`LimitQuery`/`OffsetQuery`, `parse_status_filter` — rejects a repeated `?status=` query param), `response.py` (`ReimbursementListItem.from_record`, `HumanReviewSummary`, `ReimbursementListResponse`).

### `api/reimbursement/update`

- **Purpose:** `PUT /api/v1/reimbursement/{uuid}` — records a human reviewer's approve or reject decision on a row already at `human-review`/`auto-rejected`/`human-rejected`. Human-driven decision recording, not the automated decision policy.
- **Location:** `src/api/src/reimbursement/update/`
- **Key files:** `route.py` (endpoint, uuid consistency check), `validation.py` (`ApproveReview`/`RejectReview` discriminated union, `validate_review`).

### `api` root (`src/api/src/*.py`)

- **Purpose:** app-wide infrastructure that no single vertical slice owns — FastAPI app construction, lifespan/producer wiring, the app-wide error contract, and the migration runner.
- **Location:** `src/api/src/{main,dependencies,errors,migrate}.py`.

### `shared`

- **Purpose:** code genuinely reusable across `api`, `reimbursement`, and `publisher` — cross-service pydantic models, exception classes, Kafka config/publish primitives, the Postgres pool lifecycle, and the `reimbursement` domain's persistence + use-case layer (the largest slice: list/filter, the approve/reject review transaction, the insert+publish unit, and the original escalation path).
- **Location:** `src/shared/src/shared/`.
- **Key files:** `config.py` (`load_config()` — single cached env-config entrypoint), `db.py` (`managed_pool` — Postgres pool lifecycle), `producer.py` (`managed_producer`, `publish` — technology-specific but domain-agnostic), `models.py` (`ReimbursementRequest`, `RequestEnvelope`, `ReimbursementEnvelope`, `Reimbursement`, `AttemptError`, `SampleMessage`, `HealthStatus`), `errors.py` (`PayloadTooLarge`, `BatchInvalid`, `PublishFailed`, `ReimbursementFilterInvalid`, `ReviewInvalid`, `ReimbursementNotFound`, `ReimbursementNotEligible`, `ReimbursementUuidMismatch`, `sanitize()`), `failure_log.py` (`write()` — last-resort structured log), `reimbursement/repository.py` (every SQL statement — insert, fetch, approve, reject, record decision) + `reimbursement/use_cases/{send_human_review,publish_pending,list_reimbursements,review_reimbursement}.py` (the escalation action, the insert+publish unit, the list/filter gates, and the approve/reject transaction).

### `publisher`

- **Purpose:** consumes `Request`, creates one `reimbursement` row per item, and publishes one `Reimbursement` message per item — the middle link between `api`'s intake and `reimbursement`'s (not yet wired) decision layer.
- **Location:** `src/publisher/src/{consumer,processing}.py`, installed via package-dir like `api` (AD-031).
- **Key files:** `consumer.py` (Kafka consumer lifecycle, offset commit), `processing.py` (the decision tree — insert+publish via `shared.reimbursement.use_cases.publish_pending`, retry-with-requeue, `retry > 3` escalation, duplicate detection). Fully implemented and tested — see `TESTING.md`.

### `reimbursement`

- **Purpose:** consumes `Reimbursement`, resolves the row by `uuid`, and settles it into one of resolved / stale / ghost / requeued / escalated / logged / invalid — the middle link between `publisher`'s handoff and the service's own (scaffolded, not yet wired) decision policy. Named `reimbursement`, not `agent` — renamed and flattened this branch (`ce80603`) to match `publisher`/`api`'s layout; "the agent" now refers to the LangGraph decision graph nested inside it, not the package itself.
- **Location:** `src/reimbursement/src/{consumer,validation,config,schema}.py` plus the `agent/` decision-graph subpackage — a real installed package via setuptools package-dir (AD-031), like `api`/`publisher`.
- **Key files:** `consumer.py` (Kafka consumer lifecycle, offset commit), `validation.py` (the decision tree — `handle_message`, resolve-by-uuid, staleness guard, ghost tolerance (R-001), transient-failure requeue, `retry > 3` escalation reusing `shared.reimbursement.use_cases.send_human_review.escalate_existing`), `config.py` (`AgentConfig`), `schema.py` (`State` — the decision graph's `TypedDict`, `reimbursement: shared.models.Reimbursement`). The consume/resolve layer is fully implemented and tested — see `TESTING.md`. **`agent/` (decision-graph scaffold, not yet wired or tested):** `agent.py` builds a `langgraph.StateGraph` and registers its five nodes, but adds no edges and never compiles/invokes it; `nodes/{extract_fields,validate,apply_policies,analysis,apply_agent_decision}.py` are each a one-line stub returning their own name; `prompts/{extract_fields,analysis}.py` are empty. None of this is called from `validation.py`'s `RESOLVED` branch yet — `langchain`/`langgraph` remain declared dependencies with no functioning call path. See `.specs/features/agent-decide-reimbursement/` for the design in progress.

## Where Things Live

**POST /api/v1/reimbursement:**
- Route + OpenAPI: `src/api/src/reimbursement/create/route.py`
- Validation: `src/api/src/reimbursement/create/validation.py`
- Body-size enforcement: `src/api/src/reimbursement/create/payload.py`
- Kafka publish: `src/api/src/reimbursement/create/producer.py` → `src/shared/src/shared/producer.py`
- Cross-cutting config: `src/shared/src/shared/config.py`

**GET /api/v1/reimbursement (list):**
- Route: `src/api/src/reimbursement/list/route.py`
- Query-param parsing: `src/api/src/reimbursement/list/params.py`
- Response shaping: `src/api/src/reimbursement/list/response.py`
- Filter/pagination gates + fetch: `src/shared/src/shared/reimbursement/use_cases/list_reimbursements.py` → `src/shared/src/shared/reimbursement/repository.py` (`fetch_reimbursement_page`)

**PUT /api/v1/reimbursement/{uuid} (approve/reject):**
- Route + uuid consistency check: `src/api/src/reimbursement/update/route.py`
- Payload validation: `src/api/src/reimbursement/update/validation.py`
- Approve/reject transaction + disambiguation: `src/shared/src/shared/reimbursement/use_cases/review_reimbursement.py` → `src/shared/src/shared/reimbursement/repository.py` (`approve`, `reject`, `find_reimbursement_state`, `record_human_review_decision`)

**DB pool lifecycle:** `src/shared/src/shared/db.py` (`managed_pool`) → constructed in `src/api/src/main.py`'s `lifespan`, exposed via `src/api/src/dependencies.py`'s `get_pool`.

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
| `publisher` | `src/publisher` | Consume `Request`, persist to `reimbursement`, publish `Reimbursement` (implemented) |
| `reimbursement` | `src/reimbursement` | Consume `Reimbursement`, resolve by uuid, requeue/escalate (implemented); `agent/` decision-graph scaffold not yet wired |
| `shared` | `src/shared` | Shared kernel: models, config, Kafka producer, DB pool lifecycle, `reimbursement` persistence + use cases (create/list/review) |
