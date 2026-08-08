# Architecture

## Overview / Pattern

Event-driven microservices, three independently deployable services around a Kafka backbone, plus a shared kernel. The first two hops are implemented end to end: `api` accepts and queues, `publisher` consumes that queue, persists a row, and queues the next hop. `agent` — the service that would consume that next hop and produce a decision — is still an unimplemented stub, so the pipeline durably carries a request all the way to a `Reimbursement` Kafka message, but nothing yet turns that message into a decision.

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
                        │ consume
                        ▼
                 ┌─────────────┐  publish (Reimbursement topic)  ┌─────────────┐
                 │  publisher  │─────────────────────────────────▶│    agent    │
                 │(implemented)│         via Kafka, not a direct call │  (stub)  │
                 └──────┬──────┘                                  └─────────────┘
                        │ insert / update
                        ▼
                 ┌─────────────────────────────────┐
                 │           PostgreSQL             │
                 │  reimbursement / human_review    │
                 └───────────────────────────────────┘
```

`shared` (models, config, Kafka producer, `reimbursement` persistence) is imported by all three services but is not itself a running process. `publisher` and `agent` never call each other directly — `publisher` writes to Postgres and publishes to Kafka; nothing yet consumes what it publishes.

## Layers

| Layer | Responsibility | Key Files or Dirs |
| ----- | -------------- | ------------------ |
| HTTP intake | Accept, cap, validate a reimbursement batch | `src/api/src/reimbursement/create/{route,payload,validation}.py` |
| Publish (api → Request) | Build the wire envelope, hand it to Kafka | `src/api/src/reimbursement/create/producer.py`, `src/shared/src/shared/producer.py` |
| App infrastructure | FastAPI app/lifespan, DI accessors, app-wide error contract | `src/api/src/{main,dependencies,errors}.py` |
| Shared kernel | Cross-service models, config, exceptions, Kafka producer | `src/shared/src/shared/*.py` |
| Reimbursement persistence | asyncpg pool, SQL statements against `reimbursement`, human-review escalation | `src/shared/src/shared/reimbursement/{repository,use_cases/send_human_review}.py` |
| Consume + persist + publish | Take requests off `Request`, insert a row, publish to `Reimbursement`, retry/escalate/deduplicate | `src/publisher/src/{consumer,processing}.py` |
| Decision (planned) | Business rules + LLM evaluation, consuming `Reimbursement` | `src/agent` (not implemented) |

## Dependency Rules

- `api`, `agent`, `publisher` never import each other — the only shared code path is `shared`.
- `shared` has no dependency on any of the three services (one-directional).
- Inside `api`, vertical slices (`reimbursement/create/`) own their route, validation, and publish logic; app-root modules (`main.py`, `dependencies.py`, `errors.py`) hold only what is genuinely slice-independent — e.g. `dependencies.py`'s `get_producer` exists as a standalone module specifically to avoid a `main.py` ↔ `route.py` import cycle.
- `publisher` mirrors this split at its own scale: `consumer.py` (composition root + Kafka lifecycle) stays apart from `processing.py` (the decision tree) specifically so the branching logic unit-tests with no real Kafka consumer.

## Request / Data Flow

`POST /api/v1/reimbursement`:

1. `read_capped()` streams the body, aborting the instant the running byte count exceeds 1 MiB (`MAX_BODY_BYTES`) — before an oversized body is ever fully buffered.
2. `validate_batch()` parses the capped bytes as a JSON array of up to 500 `ReimbursementRequest` items (`MAX_BATCH_ITEMS`), all-or-nothing.
3. `build_envelope()` splices `{"retry":0,"published_at":...,"payload":<raw bytes>}` around the already-validated raw body — no re-serialisation, so the published payload is byte-for-byte identical to the request body.
4. `shared.producer.publish()` produces the envelope to the `Request` Kafka topic and awaits the delivery future, raising `PublishFailed` on a broker error or timeout.
5. The route returns `201 {"msg": "<n> request(s) accepted"}`, or an error status via the app-wide handler for whichever typed exception fired.

`Request` → `publisher` → row + `Reimbursement`:

1. `publisher/consumer.py` consumes one `Request` message at a time (`enable.auto.commit=False`) and hands it to `processing.handle_message`, which never raises.
2. Each item in the message is validated as a `ReimbursementRequest`, then fanned out under a `Semaphore(item_concurrency)` (default 10).
3. Per item: `repository.insert_pending` and `shared.producer.publish` (a `ReimbursementEnvelope` carrying only the row's `uuid`) both run inside one `conn.transaction()` — a publish failure rolls the insert back with no explicit rollback call (AD-017).
4. A duplicate (`is_duplicate`, constraint-name match) is dropped and logged as a structured event, never retried. Any other failure is requeued to `Request` with `retry + 1` and an appended `AttemptError`. Past `retry > 3`, the item is escalated to a `human-review` row (`send_human_review`) instead of being retried again; if that escalation itself fails, it falls to `shared.failure_log`.
5. The consumer offset commits once every item in the message has settled — never mid-batch.

Everything past that point (a consumer turning a `Reimbursement` message into a decision) is not yet implemented — `agent` does not yet consume that topic.

## Communication Patterns

- **REST** — client → `api`, single versioned prefix `/api/v1/...`.
- **Kafka pub/sub** — `api` → `Request` topic → `publisher` (consumed and implemented) → `Reimbursement` topic → `agent` (published, but not yet consumed — no code subscribes to `Reimbursement` yet). Message contracts: `shared.models.RequestEnvelope` (`retry`, `published_at`, `errors`, `payload`) and `shared.models.ReimbursementEnvelope` (`uuid`, `retry`, `published_at`, `errors` — no payload; the Agent is expected to resolve it from the row by `uuid`).

## Key Components

| Component | Role |
| --------- | ---- |
| `main.lifespan` | Owns the app's resources for its lifetime — currently constructs/closes the one Kafka `AIOProducer`, stored on `app.state.producer` |
| `dependencies.get_producer` | FastAPI `Depends()` accessor for the live producer — overridden with a fake in tests |
| `errors.register_handlers` | Registers the app-wide `{"msg": "..."}` error contract for every typed exception |
| `shared.config.load_config` | `@lru_cache`'d single entrypoint for every env-derived config value |
| `shared.producer.publish` | Stateless, topic-agnostic Kafka publish primitive shared by every service |
| `shared.reimbursement.repository` | asyncpg pool lifecycle (`managed_pool`) + every SQL statement against `reimbursement` (`insert_pending`, `insert_human_review`, `is_duplicate`) |
| `shared.reimbursement.use_cases.send_human_review` | Renders an `AttemptError` history to prose and escalates a row to `human-review` — the one action "preserve this request for a human, explained" |
| `shared.failure_log.write` | Last-resort structured JSON log at critical level, for a failure nothing else could handle |
| `publisher.processing.handle_message` | The publisher's entire decision tree — insert+publish, retry/requeue, escalation, duplicate detection — never raises |

## Data Model

- **`reimbursement`** — one row per submitted request: `uuid` (PK, `uuidv7()`), `request_id`, `submitted_by`, `submitted_at`, `original_payload` (JSONB), `status` (`pending` / `auto-approved` / `auto-rejected` / `human-review` / `human-approved` / `human-rejected`), `receipts_value`, `receipts_date`, `currency`, `decision_reason`, `human_review_notes`, `created_at`, `updated_at`. Unique on `(request_id, lower(submitted_by))`. **Now actively written** — `publisher` inserts a `pending` row per item, or a `human-review` row past the retry ceiling.
- **`human_review`** — one row per review event, FK to `reimbursement.uuid` (`ON DELETE RESTRICT`), `status` (`approved`/`rejected`), `reviewed_by`, `reason`, `created_at`. **Append-only**: a database trigger (`human_review_append_only`) rejects any `UPDATE`/`DELETE`. Still unwritten — the retry-ceiling escalation sets `reimbursement.status = 'human-review'` directly; nothing yet writes an actual review event to this table.

## Database Access Patterns

Plain SQL migrations via `yoyo`, applied by a one-shot `migrate` job (advisory-lock serialised — see `src/api/src/migrate.py`) that every other service waits on before starting. No ORM, no query builder. `asyncpg` is now genuinely used by `publisher` via `shared.reimbursement.repository.managed_pool` — one pool per process, explicitly sized (never left at `create_pool`'s default `min_size=10, max_size=10`, which would otherwise equal the publisher's item concurrency and leave zero headroom). The implicit-transaction pattern (AD-017) wraps a write and its paired Kafka publish in one `conn.transaction()`, so a publish failure rolls the write back with no explicit rollback call. `agent` still declares `asyncpg` as a dependency but does not import it anywhere.

## State Management

Stateless services; Kafka is the durable state-transfer mechanism between them. In-process state: `app.state.producer` (the live `AIOProducer`) in `api`, constructed once at FastAPI lifespan startup; `publisher` similarly composes a pool + producer + consumer for its process lifetime in `consumer.py`'s `_serve()`.

## Error Handling Strategy

A small set of typed exceptions (`shared.errors.{PayloadTooLarge, BatchInvalid, PublishFailed}` plus FastAPI's own `RequestValidationError`/`HTTPException`) are raised by business logic and translated to one uniform `{"msg": "..."}` JSON response only at the app boundary (`errors.register_handlers`) — domain code never builds an HTTP response directly. `PublishFailed` deliberately catches `Exception` broadly rather than narrowing to specific broker exceptions (AD-021), folding the failing exception's class name into the message so the failure mode stays identifiable in logs without narrowing the catch surface.

`publisher.processing` takes a different, complementary approach: rather than exceptions propagating to a boundary handler, every branch of `handle_message` returns an `ItemOutcome` enum member (`PUBLISHED | DUPLICATE | REQUEUED | ESCALATED | LOGGED | INVALID`) and the function itself never raises. This makes per-item independence structural under `asyncio.gather` fan-out — one item's failure literally cannot propagate to interrupt another's.

## Observability

Three-tier logging, all via stdlib `logging` (no OpenTelemetry, no metrics, no correlation-ID propagation observed):

1. **Structured informational events** — `logger.info(json.dumps({"event": "reimbursement.duplicate_dropped", ...}))` for countable, expected outcomes (e.g. a dropped duplicate), so a log-based monitor can count occurrences without parsing prose.
2. **Sanitized stdout errors** — `logger.error(...)` combined with `shared.errors.sanitize(exc)`, which renders only the exception type and (if present) the violated constraint name — never Postgres's `DETAIL` line, which can embed PII like a submitter's email.
3. **Last-resort failure log** — `shared.failure_log.write()` emits one full structured JSON record (including the item itself) at `logging.CRITICAL` to a dedicated named logger, for a failure nothing else in the chain could handle. Never raises; ops attach a `FileHandler` or shipper to the logger name for durability, no code change needed.

LangFuse is provisioned in `docker-compose.yml` for the future `agent` service's LLM call tracing but is not yet wired into any code.

## API Versioning

URL path prefix (`/api/v1/reimbursement`). No deprecation policy or version-coexistence pattern exists yet — only one version is defined.

## Notable Patterns

- **Byte-splice envelope construction** — the Kafka envelope is built by string-concatenating a literal prefix around the already-validated raw request bytes, not by re-parsing/re-serialising a model, so the published payload is guaranteed byte-identical to what the client sent.
- **Streaming body-size cap** — `read_capped()` counts bytes while streaming and aborts mid-stream, specifically so an oversized body is never fully buffered before rejection (a memory-exhaustion defense a post-hoc validation-layer check cannot provide).
- **Single cached config loader** — `shared.config.load_config()` is the one place every `os.getenv()` read happens; plain frozen dataclasses hold no env-reading logic of their own.
- **Advisory-lock migration serialisation** — `migrate.py` uses a PostgreSQL session-scoped advisory lock (not `yoyo`'s own table-row lock) specifically because the advisory lock releases automatically if the runner's connection drops, avoiding a permanently stuck lock after a `SIGKILL`.
- **Outcome-enum decision tree** — `publisher.processing.handle_message` and every handler it calls return an `ItemOutcome` member instead of raising; combined with `asyncio.gather` (no `return_exceptions`), this makes one item's failure structurally unable to affect another's, and gives every branch a countable value for free.
- **Transaction-wraps-publish** — a DB write and its paired Kafka publish run inside the same `conn.transaction()`, so a publish failure rolls the write back implicitly (AD-017). The same shape as the byte-splice pattern above: correctness is enforced by what the code structurally cannot do, not by a rule the next author has to remember.
- **Requeue-with-history retry** — rather than retrying in place, a failed item is republished as a new envelope with `retry` incremented and an `AttemptError` appended to a running list (`AttemptError.next(errors, stage, exc)`), so the consumer that eventually sees `retry > 3` can render the full failure history into a human-review row without any out-of-band state.
