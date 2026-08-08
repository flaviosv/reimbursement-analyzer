# Architecture

## Overview / Pattern

Event-driven microservices, three independently deployable services around a Kafka backbone, plus a shared kernel. Only the first hop (`api`) is implemented end to end; `publisher` and `agent` are unimplemented stubs, so today the system is effectively "accept and queue," with no consumer yet turning a queued request into a decision.

## High-Level Structure

```
                 ┌─────────────┐
  HTTP client ──▶│     api     │
                 │  (FastAPI)  │
                 └──────┬──────┘
                        │ publish (Request topic)
                        ▼
                 ┌─────────────┐
                 │    Kafka    │
                 │   (KRaft)   │
                 └──────┬──────┘
                        │ consume (stub only)
                        ▼
                 ┌─────────────┐        ┌─────────────┐
                 │  publisher  │───────▶│    agent    │
                 │   (stub)    │ (planned, not wired) │  (stub)
                 └──────┬──────┘        └──────┬──────┘
                        │                       │
                        ▼                       ▼
                 ┌─────────────────────────────────┐
                 │           PostgreSQL             │
                 │  reimbursement / human_review    │
                 └───────────────────────────────────┘
```

`shared` (models, config, Kafka producer primitives) is imported by all three services but is not itself a running process.

## Layers

| Layer | Responsibility | Key Files or Dirs |
| ----- | -------------- | ------------------ |
| HTTP intake | Accept, cap, validate a reimbursement batch | `src/api/src/reimbursement/create/{route,payload,validation}.py` |
| Publish | Build the wire envelope, hand it to Kafka | `src/api/src/reimbursement/create/producer.py`, `src/shared/src/shared/producer.py` |
| App infrastructure | FastAPI app/lifespan, DI accessors, app-wide error contract | `src/api/src/{main,dependencies,errors}.py` |
| Shared kernel | Cross-service models, config, exceptions | `src/shared/src/shared/*.py` |
| Persistence | Reimbursement/human-review schema | `src/api/src/migrations/*.sql` (schema only — no code path writes to it yet) |
| Decision (planned) | Business rules + LLM evaluation | `src/agent` (not implemented) |
| Consume + persist (planned) | Take requests off Kafka, store them, republish | `src/publisher` (not implemented) |

## Dependency Rules

- `api`, `agent`, `publisher` never import each other — the only shared code path is `shared`.
- `shared` has no dependency on any of the three services (one-directional).
- Inside `api`, vertical slices (`reimbursement/create/`) own their route, validation, and publish logic; app-root modules (`main.py`, `dependencies.py`, `errors.py`) hold only what is genuinely slice-independent — e.g. `dependencies.py`'s `get_producer` exists as a standalone module specifically to avoid a `main.py` ↔ `route.py` import cycle.

## Request / Data Flow

`POST /api/v1/reimbursement`:

1. `read_capped()` streams the body, aborting the instant the running byte count exceeds 1 MiB (`MAX_BODY_BYTES`) — before an oversized body is ever fully buffered.
2. `validate_batch()` parses the capped bytes as a JSON array of up to 500 `ReimbursementRequest` items (`MAX_BATCH_ITEMS`), all-or-nothing.
3. `build_envelope()` splices `{"retry":0,"published_at":...,"payload":<raw bytes>}` around the already-validated raw body — no re-serialisation, so the published payload is byte-for-byte identical to the request body.
4. `shared.producer.publish()` produces the envelope to the `Request` Kafka topic and awaits the delivery future, raising `PublishFailed` on a broker error or timeout.
5. The route returns `201 {"msg": "<n> request(s) accepted"}`, or an error status via the app-wide handler for whichever typed exception fired.

Everything past step 4 (a consumer turning that message into a stored `reimbursement` row and eventually a decision) is not yet implemented.

## Communication Patterns

- **REST** — client → `api`, single versioned prefix `/api/v1/...`.
- **Kafka pub/sub** — `api` → `Request` topic → (planned) `publisher`/`agent`. Message contract: `shared.models.RequestEnvelope` (`retry`, `published_at`, `payload`).

## Key Components

| Component | Role |
| --------- | ---- |
| `main.lifespan` | Owns the app's resources for its lifetime — currently constructs/closes the one Kafka `AIOProducer`, stored on `app.state.producer` |
| `dependencies.get_producer` | FastAPI `Depends()` accessor for the live producer — overridden with a fake in tests |
| `errors.register_handlers` | Registers the app-wide `{"msg": "..."}` error contract for every typed exception |
| `shared.config.load_config` | `@lru_cache`'d single entrypoint for every env-derived config value |
| `shared.producer.publish` | Stateless, topic-agnostic Kafka publish primitive shared by every service |

## Data Model

- **`reimbursement`** — one row per submitted request: `uuid` (PK, `uuidv7()`), `request_id`, `submitted_by`, `submitted_at`, `original_payload` (JSONB), `status` (`pending` / `auto-approved` / `auto-rejected` / `human-review` / `human-approved` / `human-rejected`), `receipts_value`, `receipts_date`, `currency`, `decision_reason`, `human_review_notes`, `created_at`, `updated_at`. Unique on `(request_id, lower(submitted_by))`.
- **`human_review`** — one row per review event, FK to `reimbursement.uuid` (`ON DELETE RESTRICT`), `status` (`approved`/`rejected`), `reviewed_by`, `reason`, `created_at`. **Append-only**: a database trigger (`human_review_append_only`) rejects any `UPDATE`/`DELETE`.

No code currently reads or writes either table — the schema exists ahead of the persistence layer.

## Database Access Patterns

Plain SQL migrations via `yoyo`, applied by a one-shot `migrate` job (advisory-lock serialised — see `src/api/src/migrate.py`) that every other service waits on before starting. No ORM, no query builder, no connection pool exists yet — `asyncpg` is a declared dependency of `api`/`agent`/`publisher` but is not imported anywhere in this codebase.

## State Management

Stateless services; Kafka is the durable state-transfer mechanism between them. The one piece of in-process state is `app.state.producer` (the live `AIOProducer`), constructed once at FastAPI lifespan startup and torn down at shutdown.

## Error Handling Strategy

A small set of typed exceptions (`shared.errors.{PayloadTooLarge, BatchInvalid, PublishFailed}` plus FastAPI's own `RequestValidationError`/`HTTPException`) are raised by business logic and translated to one uniform `{"msg": "..."}` JSON response only at the app boundary (`errors.register_handlers`) — domain code never builds an HTTP response directly. `PublishFailed` deliberately catches `Exception` broadly rather than narrowing to specific broker exceptions (AD-021), folding the failing exception's class name into the message so the failure mode stays identifiable in logs without narrowing the catch surface.

## Observability

Structured/leveled logging via stdlib `logging` only (e.g. `errors.py`'s `logger.exception` on unhandled errors, `producer.py`'s failure logging). LangFuse is provisioned in `docker-compose.yml` for the future `agent` service's LLM call tracing but is not yet wired into any code. No OpenTelemetry, no metrics, no correlation-ID propagation observed.

## API Versioning

URL path prefix (`/api/v1/reimbursement`). No deprecation policy or version-coexistence pattern exists yet — only one version is defined.

## Notable Patterns

- **Byte-splice envelope construction** — the Kafka envelope is built by string-concatenating a literal prefix around the already-validated raw request bytes, not by re-parsing/re-serialising a model, so the published payload is guaranteed byte-identical to what the client sent.
- **Streaming body-size cap** — `read_capped()` counts bytes while streaming and aborts mid-stream, specifically so an oversized body is never fully buffered before rejection (a memory-exhaustion defense a post-hoc validation-layer check cannot provide).
- **Single cached config loader** — `shared.config.load_config()` is the one place every `os.getenv()` read happens; plain frozen dataclasses hold no env-reading logic of their own.
- **Advisory-lock migration serialisation** — `migrate.py` uses a PostgreSQL session-scoped advisory lock (not `yoyo`'s own table-row lock) specifically because the advisory lock releases automatically if the runner's connection drops, avoiding a permanently stuck lock after a `SIGKILL`.
