# Project Structure

**Root:** repo root — a uv workspace monorepo (`members = ["packages/*"]`)

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
├── packages/
│   ├── api/                   # Public HTTP API (FastAPI) — installed package via uv_build, nested src-layout (AD-031, amended)
│   │   ├── src/api/             # api's own modules, matching shared's nested src-layout shape (dotted-importable as api.*)
│   │   │   ├── main.py            # App entrypoint, lifespan (producer + DB pool construction)
│   │   │   ├── dependencies.py    # FastAPI route dependency accessors (get_producer, get_pool)
│   │   │   ├── errors.py          # App-wide exception handlers, MessageResponse
│   │   │   ├── middleware.py      # CorrelationIdMiddleware — mints/echoes X-Request-ID, scopes shared.logging's ContextVar per request
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
│   │   ├── tests/                # Mirrors src/api/ layout; helpers.py centralizes shared test builders
│   │   └── Dockerfile            # Multi-stage: builder / dev / migrate / prod
│   ├── reimbursement/          # Consume Reimbursement, resolve by uuid, and decide — all implemented (LangGraph graph)
│   │   ├── src/reimbursement/    # reimbursement's own modules, matching shared's nested src-layout shape
│   │   │   ├── consumer.py         # Composition root: pool/producer/consumer lifecycle, offset commit
│   │   │   ├── validation.py       # Decision tree: resolve, staleness guard, requeue, retry-ceiling escalation, decision-stage-failure escalation
│   │   │   ├── config.py           # AgentConfig (consumer_group_id, ai: AIConfig, models: AgentModelsConfig), load_agent_config()
│   │   │   ├── schema.py           # State TypedDict — reimbursement: shared.models.Reimbursement
│   │   │   └── agent/                # LangGraph decision graph — implemented, invoked from validation.py
│   │   │       ├── agent.py            # StateGraph builder — 5 nodes wired with edges, compiled, LangFuse-traced
│   │   │       ├── nodes/               # extract_fields, validate, apply_policies, analysis, apply_agent_decision
│   │   │       │                        # — deterministic rules + LLM-as-judge guardrail, each with its own test file
│   │   │       └── prompts/             # extract_fields.py, analysis.py — get_*_prompt() factories, render system prompts
│   │   └── tests/                 # consumer/validation/config plus one test file per agent/ node + full-graph wiring
│   │       ├── conftest.py         # Kafka container fixture, local to this package
│   │       ├── agent_fakes.py      # FakePool/FakeConnection; re-exports shared.testing.FakeProducer
│   │       ├── test_config.py
│   │       ├── test_consumer.py
│   │       ├── test_validation.py
│   │       └── test_integration.py   # Real Kafka + Postgres round trip
│   ├── publisher/              # Consume Request, persist, publish Reimbursement — implemented
│   │   ├── src/publisher/        # publisher's own modules, matching shared's nested src-layout shape (dotted-importable as publisher.*, AD-031 amended)
│   │   │   ├── consumer.py         # Composition root: pool/producer/consumer lifecycle, offset commit
│   │   │   └── processing.py       # Decision tree: insert+publish, retry/requeue, escalation, duplicates
│   │   └── tests/
│   │       ├── conftest.py         # Publisher-local Kafka container fixture
│   │       ├── fakes.py            # Test doubles as classes (FakeProducer, FakePool, RealPool)
│   │       ├── test_consumer.py
│   │       ├── test_processing.py
│   │       ├── test_integration.py   # Real Kafka + Postgres round trip
│   │       └── test_trace_propagation_integration.py   # Real Kafka trace-context propagation round trip
│   └── shared/                 # Shared kernel, installable package
│       └── src/shared/
│           ├── config.py          # KafkaConfig, DatabaseConfig, FailureLogConfig, PublisherConfig, load_config()
│           ├── db.py              # managed_pool() — Postgres pool lifecycle (relocated from reimbursement/repository.py, AD-029)
│           ├── errors.py          # Cross-service exception classes, sanitize()
│           ├── failure_log.py     # Last-resort structured JSON log (critical level)
│           ├── logging.py         # configure_logging() (ECS-JSON via ecs-logging), correlation-id ContextVar + CorrelationIdFilter, log_event()
│           ├── models.py          # Cross-service pydantic models, AttemptError, ReimbursementEnvelope, Reimbursement
│           ├── producer.py        # Generic Kafka publish + producer lifecycle
│           └── reimbursement/     # Domain slice: persistence + use cases for the reimbursement/human_review tables
│               ├── repository.py           # insert_pending, insert_human_review, get_by_uuid, update_human_review, fetch_reimbursement_page, approve, reject, find_reimbursement_state, record_human_review_decision, is_duplicate
│               └── use_cases/
│                   ├── send_human_review.py    # render_history(), send_human_review(), escalate_existing()
│                   ├── publish_pending.py      # publish_pending() — insert commits immediately, compensating delete on publish failure (AD-033)
│                   ├── list_reimbursements.py  # list_reimbursements() — status whitelist + limit/offset gates
│                   └── review_reimbursement.py # approve_reimbursement(), reject_reimbursement() — the approve/reject transaction
│           ├── signals.py         # install_shutdown_handlers() — SIGINT/SIGTERM → asyncio.Event (used by reimbursement only so far)
│           ├── testing.py         # FakeProducer, in_memory_tracer() — test doubles genuinely reused across service test suites
│           └── tracing.py         # OTel wiring — init_tracer/shutdown_tracer/traced_message_span/stamp_span, Kafka header inject/extract
├── tests/e2e/                  # Real-stack, real-Groq e2e suite (@pytest.mark.e2e, excluded by default) — real stack was docker-compose, now the sibling local-env k3s/Tilt setup (AD-040)
│   ├── conftest.py               # Env loading + stack-readiness gate (poll-and-fail-fast; never starts/stops the stack)
│   ├── polling.py                 # wait_for_status, find_uuid_by_request_id
│   ├── payload_builders.py        # Per-bucket payload fixtures engineered for unambiguous real-model steering
│   ├── langfuse_helper.py         # trace_exists_for_session — polls LangFuse's observations API
│   └── test_{happy_path,auto_reject,human_review,retry_ghost_stale}.py  # One file per decision-outcome bucket
├── conftest.py                 # Workspace-level Postgres fixture (shared by every package's tests) + the e2e collection-skip hook
├── pyproject.toml              # Workspace root — pytest config, dev dependency group
└── uv.lock
```

## Module Organization

### `api/reimbursement/create`

- **Purpose:** accepts, validates, and publishes a batch of reimbursement requests.
- **Location:** `packages/api/src/api/reimbursement/create/`
- **Key files:** `route.py` (endpoint + OpenAPI schema derivation), `payload.py` (streaming byte-cap), `validation.py` (`BATCH_ADAPTER`, `validate_batch`), `producer.py` (`build_envelope`, `publish`).
- **Pattern:** each operation lands as a sibling directory under `reimbursement/`, self-contained with its own tests directory mirror — see `list/` and `update/` below, added following this exact pattern.

### `api/reimbursement/list`

- **Purpose:** `GET /api/v1/reimbursement` — lists reimbursements, optionally filtered by status (comma-separated, single query param only), paginated (`limit`/`offset`), each row paired with its most recent `human_review` entry if any.
- **Location:** `packages/api/src/api/reimbursement/list/`
- **Key files:** `route.py` (endpoint), `params.py` (`LimitQuery`/`OffsetQuery`, `parse_status_filter` — rejects a repeated `?status=` query param), `response.py` (`ReimbursementListItem.from_record`, `HumanReviewSummary`, `ReimbursementListResponse`).

### `api/reimbursement/get`

- **Purpose:** `GET /api/v1/reimbursement/{uuid}` — looks up a single reimbursement by uuid.
- **Location:** `packages/api/src/api/reimbursement/get/`
- **Key files:** `route.py` (endpoint, `ReimbursementDetailResponse`).

### `api/reimbursement/update`

- **Purpose:** `PUT /api/v1/reimbursement/{uuid}` — records a human reviewer's approve or reject decision on a row already at `human-review`/`auto-rejected`/`human-rejected`. Human-driven decision recording, not the automated decision policy.
- **Location:** `packages/api/src/api/reimbursement/update/`
- **Key files:** `route.py` (endpoint, uuid consistency check), `validation.py` (`ApproveReview`/`RejectReview` discriminated union, `validate_review`).

### `api` root (`packages/api/src/api/*.py`)

- **Purpose:** app-wide infrastructure that no single vertical slice owns — FastAPI app construction, lifespan/producer wiring, the app-wide error contract, the correlation-id middleware, and the migration runner.
- **Location:** `packages/api/src/api/{main,dependencies,errors,middleware,migrate}.py`.

### `shared`

- **Purpose:** code genuinely reusable across `api`, `reimbursement`, and `publisher` — cross-service pydantic models, exception classes, Kafka config/publish primitives, the Postgres pool lifecycle, structured logging + correlation-id propagation, and the `reimbursement` domain's persistence + use-case layer (the largest slice: list/filter, the approve/reject review transaction, the insert+publish unit, and the original escalation path).
- **Location:** `packages/shared/src/shared/`.
- **Key files:** `config.py` (`load_config()` — single cached env-config entrypoint, incl. `LoggingConfig`), `db.py` (`managed_pool` — Postgres pool lifecycle), `logging.py` (`configure_logging()` — ECS-JSON root-logger setup; `get_correlation_id`/`set_correlation_id`/`reset_correlation_id`; `CorrelationIdFilter`; `log_event()`), `producer.py` (`managed_producer`, `publish` — technology-specific but domain-agnostic), `tracing.py` (`init_tracer()`/`shutdown_tracer()`/`traced_message_span()`/`stamp_span()` — OTel wiring shared by every service), `models.py` (`ReimbursementRequest`, `RequestEnvelope`, `ReimbursementEnvelope`, `Reimbursement`, `AttemptError`, `SampleMessage`, `HealthStatus`), `errors.py` (`PayloadTooLarge`, `BatchInvalid`, `PublishFailed`, `ReimbursementFilterInvalid`, `ReviewInvalid`, `ReimbursementNotFound`, `ReimbursementNotEligible`, `ReimbursementUuidMismatch`, `sanitize()`), `failure_log.py` (`write()` — last-resort structured log), `reimbursement/repository.py` (every SQL statement — insert, delete, fetch, approve, reject, record decision) + `reimbursement/use_cases/{send_human_review,publish_pending,list_reimbursements,review_reimbursement}.py` (the escalation action, the insert+publish unit, the list/filter gates, and the approve/reject transaction).

### `publisher`

- **Purpose:** consumes `Request`, creates one `reimbursement` row per item, and publishes one `Reimbursement` message per item — the middle link between `api`'s intake and `reimbursement`'s decision layer.
- **Location:** `packages/publisher/src/publisher/{consumer,processing}.py`, installed via `uv_build`'s nested src-layout like `api`/`shared` (AD-031, amended).
- **Key files:** `consumer.py` (Kafka consumer lifecycle, offset commit), `processing.py` (the decision tree — insert+publish via `shared.reimbursement.use_cases.publish_pending`, retry-with-requeue, `retry > 3` escalation, duplicate detection). Fully implemented and tested — see `TESTING.md`.

### `reimbursement`

- **Purpose:** consumes `Reimbursement`, resolves the row by `uuid`, and settles it into one of resolved / stale / ghost / requeued / escalated / logged / invalid — then, on resolve, hands the row to its own decision graph, which classifies it auto-approved / auto-rejected / human-review. Named `reimbursement`, not `agent` — renamed and flattened this branch (`ce80603`) to match `publisher`/`api`'s layout; "the agent" now refers to the LangGraph decision graph nested inside it, not the package itself.
- **Location:** `packages/reimbursement/src/reimbursement/{consumer,validation,config,schema}.py` plus the `agent/` decision-graph subpackage — a real installed package via `uv_build`'s nested src-layout (AD-031, amended), like `api`/`publisher`/`shared`.
- **Key files:** `consumer.py` (Kafka consumer lifecycle, offset commit), `validation.py` (the decision tree — `handle_message`, resolve-by-uuid, staleness guard, ghost tolerance (R-001), transient-failure requeue, `retry > 3` escalation reusing `shared.reimbursement.use_cases.send_human_review.escalate_existing`, then `agent.decide()` on resolve, a failure from which escalates the same way, immediately), `config.py` (`AgentConfig` — `ai: AIConfig`/`models: AgentModelsConfig`, AD-032), `schema.py` (`State` — the decision graph's `TypedDict`, `reimbursement: shared.models.Reimbursement`). Both the consume/resolve layer and the decision graph are implemented and tested — see `TESTING.md`. **`agent/` (the decision graph):** `agent.py` builds and compiles a `langgraph.StateGraph` wiring its five nodes with real edges/conditional routing, constructs two independent Groq-backed chat models (one per node, AD-032), and traces every LLM call via LangFuse; `nodes/{extract_fields,validate,apply_policies,analysis,apply_agent_decision}.py` each hold real logic (deterministic thresholds in `apply_policies`, an LLM-as-judge guardrail in `analysis`).

## Where Things Live

**POST /api/v1/reimbursement:**
- Route + OpenAPI: `packages/api/src/api/reimbursement/create/route.py`
- Validation: `packages/api/src/api/reimbursement/create/validation.py`
- Body-size enforcement: `packages/api/src/api/reimbursement/create/payload.py`
- Kafka publish: `packages/api/src/api/reimbursement/create/producer.py` → `packages/shared/src/shared/producer.py`
- Cross-cutting config: `packages/shared/src/shared/config.py`

**GET /api/v1/reimbursement (list):**
- Route: `packages/api/src/api/reimbursement/list/route.py`
- Query-param parsing: `packages/api/src/api/reimbursement/list/params.py`
- Response shaping: `packages/api/src/api/reimbursement/list/response.py`
- Filter/pagination gates + fetch: `packages/shared/src/shared/reimbursement/use_cases/list_reimbursements.py` → `packages/shared/src/shared/reimbursement/repository.py` (`fetch_reimbursement_page`)

**GET /api/v1/reimbursement/{uuid} (detail):**
- Route: `packages/api/src/api/reimbursement/get/route.py`
- Fetch: `packages/shared/src/shared/reimbursement/use_cases/get_reimbursement.py` → `packages/shared/src/shared/reimbursement/repository.py`

**PUT /api/v1/reimbursement/{uuid} (approve/reject):**
- Route + uuid consistency check: `packages/api/src/api/reimbursement/update/route.py`
- Payload validation: `packages/api/src/api/reimbursement/update/validation.py`
- Approve/reject transaction + disambiguation: `packages/shared/src/shared/reimbursement/use_cases/review_reimbursement.py` → `packages/shared/src/shared/reimbursement/repository.py` (`approve`, `reject`, `find_reimbursement_state`, `record_human_review_decision`)

**DB pool lifecycle:** `packages/shared/src/shared/db.py` (`managed_pool`) → constructed in `packages/api/src/api/main.py`'s `lifespan`, exposed via `packages/api/src/api/dependencies.py`'s `get_pool`.

**Database schema:**
- Migrations: `packages/api/src/api/migrations/*.sql`
- Runner: `packages/api/src/api/migrate.py`

## Special Directories

**`.specs/`:** `tlc-spec-driven`'s working memory — `STATE.md` (append-only architectural decision log, `AD-NNN`), `features/<name>/` (per-feature spec/design/tasks). Not part of this context set; see the root `CLAUDE.md`/`AGENTS.global.md` for how to use it.

**`docs/original/`:** the sample reimbursement request dataset (`sample.json`) that several documented sizing decisions (body ceiling, batch cardinality) are calibrated against.

**`tests/e2e/`:** real-stack, no-fakes e2e suite — drives a real running stack through its external HTTP/Kafka surfaces only (previously `docker compose`; that file was removed this branch, AD-040 — the suite now assumes the sibling `local-env` k3s/Tilt setup is up instead), no `TestClient`/`dependency_overrides`/direct DB access from test code. Gated behind `@pytest.mark.e2e`, excluded by default via both `pyproject.toml`'s `addopts` and root `conftest.py`'s structural collection hook — see `TESTING.md`.

## Monorepo Package Map

| Package | Path | Responsibility |
| ------- | ---- | -------------- |
| `api` | `packages/api` | Public HTTP API — intake, validation, publish to Kafka |
| `publisher` | `packages/publisher` | Consume `Request`, persist to `reimbursement`, publish `Reimbursement` (implemented) |
| `reimbursement` | `packages/reimbursement` | Consume `Reimbursement`, resolve by uuid, requeue/escalate, and decide via the `agent/` LangGraph graph (all implemented) |
| `shared` | `packages/shared` | Shared kernel: models, config, Kafka producer, DB pool lifecycle, `reimbursement` persistence + use cases (create/list/review) |
