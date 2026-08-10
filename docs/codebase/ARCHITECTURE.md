# Architecture

## Overview / Pattern

Event-driven microservices, three independently deployable services around a Kafka backbone, plus a shared kernel. All three hops in the pipeline are implemented end to end: `api` accepts and queues, `publisher` consumes that queue, persists a row, and queues the next hop, `reimbursement` consumes that hop, resolves the row by `uuid`, and now decides it. The actual policy (auto-approve / auto-reject / human-review classification, `docs/SCOPE.md`) is a LangGraph `StateGraph` under `reimbursement`'s nested `agent/` subpackage, invoked from `validation.py`'s resolve path: deterministic rules (reject >90-day-old receipts, auto-approve ≤R$200, mandatory human-review >R$2000) fire first, and an LLM-as-judge guardrail resolves the ambiguous R$200–2000 zone. `reimbursement`'s own retry-ceiling escalation to `human-review` remains a separate failure-handling safety valve, not that policy.

`api` also has a second, synchronous access pattern alongside the async Kafka pipeline: `GET /api/v1/reimbursement` (list), `GET /api/v1/reimbursement/{uuid}` (single-record detail), and `PUT /api/v1/reimbursement/{uuid}` (record a human reviewer's approve/reject decision) read and write Postgres directly, request-response, with no Kafka hop involved. `PUT` is human-driven decision *recording*, not the automated decision policy above — a reviewer calls it after deciding for themselves on a row already at `human-review`/`auto-rejected`/`human-rejected`.

## High-Level Structure

```
                 ┌─────────────┐
  HTTP client ──▶│     api     │
                 │  (FastAPI)  │
                 └──┬───────┬──┘
        publish (Request)  │  read (GET list) / write (PUT approve-reject)
                    ▼       │
                 ┌─────────────┐         publish (Reimbursement topic)        ┌─────────────┐
                 │  publisher  │───────────────────────────────────────────▶  │reimbursement│
                 │(implemented)│              via Kafka, not a direct call    │(resolve +   │
                 └──────┬──────┘                                             │decision impl)│
                        │                                                    └──────┬──────┘
                        │ insert / update                                read / escalate │
                        ▼                                                             ▼
                 ┌───────────────────────────────────────────────────────────────────────┐
                 │                            PostgreSQL                                  │
                 │                    reimbursement / human_review                        │
                 └───────────────────────────────────────────────────────────────────────┘
```

`shared` (models, config, Kafka producer, DB pool lifecycle, `reimbursement` persistence + use cases) is imported by all three services but is not itself a running process. `publisher` and the `reimbursement` service never call each other directly — `publisher` writes to Postgres and publishes to Kafka; `reimbursement` consumes what it publishes and reads/conditionally writes the same `reimbursement` row, independently. `api` now also reads/writes Postgres directly (bypassing Kafka entirely) for its `GET`/`PUT` endpoints — the first and only service with both an async (Kafka-mediated) and a synchronous (direct-DB) path to the same tables.

## Layers

| Layer | Responsibility | Key Files or Dirs |
| ----- | -------------- | ------------------ |
| HTTP intake | Accept, cap, validate a reimbursement batch | `packages/api/src/api/reimbursement/create/{route,payload,validation}.py` |
| Publish (api → Request) | Build the wire envelope, hand it to Kafka | `packages/api/src/api/reimbursement/create/producer.py`, `packages/shared/src/shared/producer.py` |
| App infrastructure | FastAPI app/lifespan, DI accessors (producer + DB pool), app-wide error contract | `packages/api/src/api/{main,dependencies,errors}.py` |
| List (GET) | Parse/validate status+pagination filters, fetch a page, shape the response | `packages/api/src/api/reimbursement/list/{route,params,response}.py` |
| Get detail (GET) | Resolve one reimbursement by uuid, shape the response | `packages/api/src/api/reimbursement/get/route.py` |
| Review (PUT) | Validate an approve/reject payload, check path/body uuid consistency, delegate to the review use case | `packages/api/src/api/reimbursement/update/{route,validation}.py` |
| Shared kernel | Cross-service models, config, exceptions, Kafka producer, DB pool lifecycle | `packages/shared/src/shared/*.py` |
| Reimbursement persistence | asyncpg pool, every SQL statement against `reimbursement`/`human_review` | `packages/shared/src/shared/reimbursement/repository.py` |
| Reimbursement use cases | List/filter gates; human-review escalation; the approve/reject transaction + 404-vs-400 disambiguation | `packages/shared/src/shared/reimbursement/use_cases/{list_reimbursements,send_human_review,review_reimbursement}.py` |
| Consume + persist + publish | Take requests off `Request`, insert a row, publish to `Reimbursement`, retry/escalate/deduplicate | `packages/publisher/src/publisher/{consumer,processing}.py` |
| Consume + resolve | Take messages off `Reimbursement`, resolve the row by `uuid`, apply the staleness guard, tolerate a ghost, requeue transient failures, escalate past the retry ceiling | `packages/reimbursement/src/reimbursement/{consumer,validation}.py` |
| Decision | Business rules + LLM evaluation — classify resolved rows as auto-approve/auto-reject/human-review, invoked from `validation.py`'s resolve path | `packages/reimbursement/src/reimbursement/agent/{agent.py,nodes/*,prompts/*}` — LangGraph graph, five wired nodes, LangFuse-traced |

## Dependency Rules

- `api`, `reimbursement`, `publisher` never import each other — the only shared code path is `shared`.
- `shared` has no dependency on any of the three services (one-directional).
- Inside `api`, vertical slices (`reimbursement/create/`) own their route, validation, and publish logic; app-root modules (`main.py`, `dependencies.py`, `errors.py`) hold only what is genuinely slice-independent — e.g. `dependencies.py`'s `get_producer` exists as a standalone module specifically to avoid a `main.py` ↔ `route.py` import cycle.
- `publisher` mirrors this split at its own scale: `consumer.py` (composition root + Kafka lifecycle) stays apart from `processing.py` (the decision tree) specifically so the branching logic unit-tests with no real Kafka consumer.
- `reimbursement` mirrors the same split again: `consumer.py` (composition root + Kafka lifecycle) stays apart from `validation.py` (the resolve/staleness/escalation decision tree), same reasoning as `publisher`'s own split. Its `agent/` subpackage (the LangGraph decision graph) is a separate concern from either, invoked by `validation.py` once a row resolves.

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
3. Per item: `shared.reimbursement.use_cases.publish_pending.publish_pending` — `repository.insert_pending` (still inside its own narrow `conn.transaction()`, a savepoint boundary closed before the publish is attempted, AD-034) plus `shared.producer.publish` (a `ReimbursementEnvelope` carrying only the row's `uuid`). No transaction spans the publish call itself: the insert commits on its own first, and a publish failure triggers a compensating `repository.delete_pending` (gated `WHERE uuid = $1 AND status = 'pending'`) instead of a DB rollback, with every delete outcome durably logged before the existing requeue fires (AD-033, amending AD-017 for this one unit of work only — AD-017's transaction-wraps-publish pattern remains the documented shape for every other write-plus-side-effect unit of work).
4. A duplicate (`is_duplicate`, constraint-name match) is dropped and logged as a structured event, never retried. Any other failure is requeued to `Request` with `retry + 1` and an appended `AttemptError`. Past `retry > 3`, the item is escalated to a `human-review` row (`send_human_review`) instead of being retried again; if that escalation itself fails, it falls to `shared.failure_log`.
5. The consumer offset commits once every item in the message has settled — never mid-batch.

`Reimbursement` topic → `reimbursement` service → resolve / requeue / escalate:

1. `reimbursement/consumer.py` consumes one `Reimbursement` message at a time (`enable.auto.commit=False`) and hands it to `validation.handle_message`, which never raises.
2. Past the retry ceiling (`retry > 3`), the message is escalated directly — reusing `shared.reimbursement.use_cases.send_human_review.escalate_existing`, the same action `publisher` uses — without touching the resolve path at all.
3. Otherwise: `repository.get_by_uuid` resolves the row. A missing row (a ghost, R-001) is tolerated and logged, not retried — retrying can never make a genuine ghost row appear. A message older than the row's own `updated_at` (the staleness guard) is dropped and logged, informational only.
4. A transient DB error while resolving requeues the message to `Reimbursement` itself (the only topic `reimbursement` owns) with `retry + 1` and an appended `AttemptError`.
5. Once a row resolves, `validation.py` invokes `agent.decide()` (the decision graph below) before the consumer offset commits — never mid-message.

`agent.decide()` — the decision graph (`reimbursement/agent/`), one `ainvoke` per resolved row:

1. `extract_fields` — one unconditional LLM call resolves `value`/`currency`/`receipts_date` from the allow-listed payload fields (`claimed_amount_brl`, `claimed_category`, `raw_ocr_text`; AGD-01..04, AGD-26 excludes PII).
2. `validate` — gates on completeness: a missing `value` or `receipts_date` routes straight to `apply_agent_decision` with `status="human-review"`, bypassing the rules below.
3. `apply_policies` — deterministic rules, reject checked first: receipts older than 90 days → `auto-rejected`; else `value <= 200` → `auto-approved`; else `value > 2000` → mandatory `human-review`. Any of the three persists immediately (via `apply_decision`) and ends the graph. Otherwise (the ambiguous R$200–2000 zone), it sets `requires_llm_judgment=True` and routes to `analysis`. Staleness is computed against the server clock (`datetime.now(UTC).date()`), not the client-supplied `submitted_at` field on the request payload — `submitted_at` is no longer read by this node at all, so a requester can no longer backdate it to defeat the 90-day reject rule (security-hardening fix).
4. `analysis` — an LLM-as-judge guardrail: sees only the resolved `value`/`currency`/`receipts_date`, judges internal plausibility, and returns `consistent` (→ `auto-approved`) or not (→ `human-review`) with a `reasoning` string that becomes the durable `decision_reason`.
5. `apply_agent_decision` — the single, decision-agnostic finalizer: persists whatever `status`/`decision_reason` is already in state (from `validate` or `analysis`), never authoring its own.

Every LLM call across `extract_fields`/`analysis` is traced via a memoized LangFuse `CallbackHandler` passed into the graph's `callbacks` config; both write nodes (`apply_policies`/`apply_agent_decision`) acquire a DB connection only for their own write, not held across the LLM round-trips.

`GET /api/v1/reimbursement/{uuid}` (detail, direct DB, no Kafka):

1. FastAPI's own path coercion validates the `uuid` path param, raising `RequestValidationError` (400) before the route body runs for a malformed one.
2. `get_reimbursement` queries the row directly by `uuid`; a miss raises `ReimbursementNotFound` (404).
3. `ReimbursementDetailResponse`/`ReimbursementItem.from_record` shape the single row into the response.

`GET /api/v1/reimbursement` (list, direct DB, no Kafka):

1. `params.parse_status_filter` reads the raw multi-value `status` query params — a repeated `?status=a&status=b` is rejected outright (AD-028); the single remaining value (if any) is split on `,` into unchecked segments.
2. `list_reimbursements` runs two sequential gates before touching the DB: every status segment must be in the client-facing whitelist (`pending` deliberately excluded — internal-only, AD-003), then `0 <= limit <= MAX_LIST_LIMIT` and `offset >= 0`. Either violation raises `ReimbursementFilterInvalid` (400) before any query runs.
3. `repository.fetch_reimbursement_page` runs one query: `reimbursement` rows (optionally status-filtered), `created_at DESC`, paged by `limit`/`offset`, each paired via `LEFT JOIN LATERAL` with its own most recent `human_review` row if one exists.
4. `ReimbursementListItem.from_record` shapes each row; `hr_*`-prefixed columns being NULL together means no review exists yet. A filter matching zero rows is still `200` with an empty `data` array — this endpoint never 404s (AD-027).

`PUT /api/v1/reimbursement/{uuid}` (approve/reject, direct DB, no Kafka):

1. `validation.validate_review` parses the body against the `ApproveReview`/`RejectReview` discriminated union (on the `status` field); a shape failure raises `ReviewInvalid` (422).
2. The route checks path/body uuid consistency (`ReimbursementUuidMismatch`, 400) if the body carries one.
3. `review_reimbursement.approve_reimbursement`/`reject_reimbursement` runs one transaction: an atomic `UPDATE ... WHERE status = ANY(eligible_statuses) RETURNING` (eligible from `human-review`, `auto-rejected`, or `human-rejected`) — reject is further gated on `receipts_value`/`receipts_date`/`currency` already being non-null (no backfill on reject, unlike approve which requires and backfills them) — followed by `record_human_review_decision`, appending one `human_review` row.
4. Zero rows affected by the `UPDATE` routes to `_disambiguate`: re-queries the row to distinguish "no such uuid" (`ReimbursementNotFound`, 404) from "row exists but ineligible/incomplete" (`ReimbursementNotEligible`, 400) — always raises, never returns (`NoReturn`). No explicit row lock precedes the `UPDATE`: Postgres's own write-serialization already makes two concurrent transitions mutually exclusive, so the loser's `WHERE status = ANY(...)` simply matches zero rows once the winner commits.

## Communication Patterns

- **REST** — client → `api`, single versioned prefix `/api/v1/...`. `GET`/`PUT` are synchronous request-response against Postgres directly; only `POST` goes through Kafka.
- **Kafka pub/sub** — `api` (POST only) → `Request` topic → `publisher` (consumed and implemented) → `Reimbursement` topic → `reimbursement` service (consumed and implemented — resolve, then decide via the LangGraph graph). `reimbursement` also republishes to `Reimbursement` itself on a transient resolve failure, the same requeue-to-own-topic shape `publisher` uses on `Request`. Message contracts: `shared.models.RequestEnvelope` (`retry`, `published_at`, `errors`, `payload`) and `shared.models.ReimbursementEnvelope` (`uuid`, `retry`, `published_at`, `errors` — no payload; `reimbursement` resolves it from the row by `uuid` into `shared.models.Reimbursement`, the `{uuid, original_payload}` shape the decision graph's `State` carries).

## Key Components

| Component | Role |
| --------- | ---- |
| `main.lifespan` | Owns the app's resources for its lifetime — constructs/closes the Kafka `AIOProducer` and the Postgres pool, stored on `app.state.producer`/`app.state.pool` |
| `dependencies.get_producer` / `get_pool` | FastAPI `Depends()` accessors for the live producer/pool — overridden with fakes in tests |
| `errors.register_handlers` | Registers the app-wide `{"msg": "..."}` error contract for every typed exception |
| `shared.config.load_config` | `@lru_cache`'d single entrypoint for every env-derived config value |
| `shared.db.managed_pool` | Postgres pool lifecycle — construct/yield/close, same shape as `managed_producer` |
| `shared.producer.publish` | Stateless, topic-agnostic Kafka publish primitive shared by every service |
| `shared.reimbursement.repository` | Every SQL statement against `reimbursement`/`human_review` — `insert_pending`, `insert_human_review`, `is_duplicate`, `get_by_uuid`, `update_human_review`, `fetch_reimbursement_page`, `approve`, `reject`, `find_reimbursement_state`, `record_human_review_decision` |
| `shared.reimbursement.use_cases.send_human_review` | Renders an `AttemptError` history to prose and escalates a row to `human-review` — the one action "preserve this request for a human, explained" |
| `shared.reimbursement.use_cases.publish_pending` | `insert_pending` (commits on its own) + Kafka publish, with a compensating `delete_pending` on publish failure rather than relying on the caller's transaction (AD-033) — `publisher`'s insert+publish counterpart to `send_human_review` |
| `shared.reimbursement.use_cases.list_reimbursements` | The list endpoint's status-whitelist + limit/offset gates — never touches the DB before both pass |
| `shared.reimbursement.use_cases.review_reimbursement` | The approve/reject transaction (atomic `UPDATE ... RETURNING` + `record_human_review_decision`) and the 404-vs-400 disambiguation on a zero-rows-affected update |
| `shared.failure_log.write` | Last-resort structured JSON log at critical level, for a failure nothing else could handle |
| `shared.signals.install_shutdown_handlers` | Arms SIGINT/SIGTERM to set an `asyncio.Event` rather than killing the process; used by `reimbursement` |
| `publisher.processing.handle_message` | The publisher's entire decision tree — insert+publish, retry/requeue, escalation, duplicate detection — never raises |
| `reimbursement.validation.handle_message` | The service's entire resolve decision tree — resolve by uuid, staleness guard, ghost tolerance, retry/requeue, retry-ceiling escalation — never raises |
| `reimbursement.consumer.run` | The service's consume loop — one message at a time, offset committed only after `handle_message` settles it |
| `reimbursement.agent.agent.decide` | Entry point into the decision graph — threads the DB pool (not a live connection) and the LangFuse callback config into one `graph.ainvoke()` per resolved row |
| `reimbursement.agent.nodes.{ApplyPolicies,ApplyAgentDecision}` | The graph's two write nodes — deterministic-rule persistence and the decision-agnostic final persist, respectively |

## Data Model

- **`reimbursement`** — one row per submitted request: `uuid` (PK, `uuidv7()`), `request_id`, `submitted_by`, `submitted_at`, `original_payload` (JSONB), `status` (`pending` / `auto-approved` / `auto-rejected` / `human-review` / `human-approved` / `human-rejected`), `receipts_value`, `receipts_date`, `currency`, `decision_reason`, `human_review_notes`, `created_at`, `updated_at`. Unique on `(request_id, lower(submitted_by))`; an index on `created_at` supports `GET`'s default no-filter ordering. **Actively read and written by all three services**: `publisher` inserts a `pending` row per item (or a `human-review` row past its own retry ceiling); `reimbursement` reads it by `uuid` to resolve into a `shared.models.Reimbursement`, then its decision graph writes `status`/`decision_reason`/`receipts_value`/`receipts_date`/`currency` (`auto-approved`/`auto-rejected`/`human-review`), or `status='human-review'` alone past its own resolve-retry ceiling (via the same `escalate_existing` action `publisher` uses); `api` reads a filtered/paginated page (or a single row by `uuid`) for `GET`, and atomically transitions a row to `human-approved`/`human-rejected` (backfilling `receipts_value`/`receipts_date`/`currency` on approve) for `PUT`.
- **`human_review`** — one row per review event, FK to `reimbursement.uuid` (`ON DELETE RESTRICT`), `status` (`approved`/`rejected`), `reviewed_by`, `reason`, `created_at`. **Append-only**: a database trigger (`human_review_append_only`) rejects any `UPDATE`/`DELETE`. **Now actively written**: `api`'s `PUT` endpoint appends one row per reviewer decision via `review_reimbursement`'s `record_human_review_decision`, inside the same transaction as the `reimbursement` row's status update. `publisher`'s and `reimbursement`'s own retry-ceiling escalations still only set `reimbursement.status = 'human-review'` directly — they do not write a `human_review` row, since no reviewer has decided anything yet at that point.

## Database Access Patterns

Plain SQL migrations via `yoyo`, applied by a one-shot `migrate` job (advisory-lock serialised — see `packages/api/src/api/migrate.py`) that every other service waits on before starting. No ORM, no query builder. `asyncpg` is genuinely used by all three services now, via the same `shared.db.managed_pool` (relocated here from `shared.reimbursement.repository`, AD-029) — one pool per process, explicitly sized (never left at `create_pool`'s default `min_size=10, max_size=10`, which would otherwise equal the publisher's item concurrency and leave zero headroom). `api`'s own pool, constructed in `main.py`'s `lifespan`, overrides `pool_min_size` to 0 — a request-serving process has bursty, not baseline, DB usage, unlike `publisher`/`reimbursement`'s steady consume-loop pool. AD-017's implicit-transaction pattern wraps a write and its paired Kafka publish in one `conn.transaction()`, so a publish failure rolls the write back with no explicit rollback call — still the documented shape for every write-plus-side-effect unit of work in the codebase except one: `publisher`'s own insert+publish path moved off it under AD-033 (the insert commits immediately, its own narrow transaction closed before the publish is attempted per AD-034, and a publish failure is compensated by an explicit `delete_pending` rather than a rollback). `reimbursement.validation` wraps no such transaction, since it has no write-plus-publish pairing to protect. `api`'s `review_reimbursement` use case wraps its own transaction differently: an atomic `UPDATE ... WHERE status = ANY(...) RETURNING` plus a paired `INSERT` into `human_review`, with no explicit row lock beforehand — Postgres's own write-serialization already makes two concurrent transitions mutually exclusive, so a losing concurrent `UPDATE` simply matches zero rows once the winner commits (a confirmed design decision, not an oversight). All three services' pool acquisitions are bounded by `DatabaseConfig.acquire_timeout_seconds` (wait for a free connection) and `command_timeout` (per-query ceiling), so a stuck connection cannot hang a consume loop or a request indefinitely.

## State Management

Stateless services; Kafka is the durable state-transfer mechanism between them. In-process state: `app.state.producer` (the live `AIOProducer`) in `api`, constructed once at FastAPI lifespan startup; `publisher` similarly composes a pool + producer + consumer for its process lifetime in `consumer.py`'s `_serve()`.

## Error Handling Strategy

A set of typed exceptions (`shared.errors.{PayloadTooLarge, BatchInvalid, PublishFailed, ReimbursementFilterInvalid, ReviewInvalid, ReimbursementNotFound, ReimbursementNotEligible, ReimbursementUuidMismatch}` plus FastAPI's own `RequestValidationError`/`HTTPException`) are raised by business logic and translated to one uniform `{"msg": "..."}` JSON response only at the app boundary (`errors.register_handlers`) — domain code never builds an HTTP response directly. Each new exception maps to one status code: `ReimbursementFilterInvalid` → 400 (bad list filter), `ReviewInvalid` → 422 (malformed PUT payload shape), `ReimbursementNotFound` → 404 (unknown uuid), `ReimbursementNotEligible` → 400 (row exists but ineligible/incomplete for the requested decision), `ReimbursementUuidMismatch` → 400 (body/path uuid disagree). `PublishFailed` deliberately catches `Exception` broadly rather than narrowing to specific broker exceptions (AD-021), folding the failing exception's class name into the message so the failure mode stays identifiable in logs without narrowing the catch surface.

`publisher.processing` takes a different, complementary approach: rather than exceptions propagating to a boundary handler, every branch of `handle_message` returns an `ItemOutcome` enum member (`PUBLISHED | DUPLICATE | REQUEUED | ESCALATED | LOGGED | INVALID`) and the function itself never raises. This makes per-item independence structural under `asyncio.gather` fan-out — one item's failure literally cannot propagate to interrupt another's.

`reimbursement.validation` uses the identical shape for its own decision tree: every branch of `handle_message` returns a `MessageOutcome` member (`RESOLVED | STALE | GHOST | REQUEUED | ESCALATED | LOGGED | INVALID`) and never raises — one bad message can never stop the consume loop behind it.

## Observability

Three-tier logging, all via stdlib `logging` (no OpenTelemetry, no metrics):

1. **Structured informational events** — `logger.info(json.dumps({"event": "reimbursement.duplicate_dropped", ...}))` for countable, expected outcomes (e.g. a dropped duplicate), so a log-based monitor can count occurrences without parsing prose.
2. **Sanitized stdout errors** — `logger.error(...)` combined with `shared.errors.sanitize(exc)`, which renders only the exception type and (if present) the violated constraint name — never Postgres's `DETAIL` line, which can embed PII like a submitter's email.
3. **Last-resort failure log** — `shared.failure_log.write()` emits one full structured JSON record (including the item itself) at `logging.CRITICAL` to a dedicated named logger, for a failure nothing else in the chain could handle. Never raises; ops attach a `FileHandler` or shipper to the logger name for durability, no code change needed.

`reimbursement.validation` follows the same three-tier shape with one deliberate refinement, driven by a stated project requirement (errors must surface at a severity a monitoring tool can triage on): genuine failures — a message that fails to parse, a transient DB error while resolving or escalating — always log via tier 2 (`logger.error` + `sanitize()`), never tier 1. Tolerated, non-error conditions (a ghost row, a stale message) stay at tier 1 (`logger.info`) even though they are the *reason* a message doesn't resolve — a ghost is an expected outcome under R-001, not a failure. A successful escalation past the retry ceiling itself logs at `logger.error` (not `.info`), on the reasoning that a human now needs to act on it, not because anything went wrong.

**Correlation-id propagation** (`api`, `reimbursement/agent`): `api`'s 4 routes (`packages/api/src/api/reimbursement/{create,update,list,get}/route.py`) each log a correlation id and outcome via a new `shared.logging.log_event(logger, level, event, **fields)` helper (`packages/shared/src/shared/logging.py` — builds `{"event": ..., **fields}`, `json.dumps(..., default=str)`, never raises) — `request_id` on `POST`, `uuid` on `GET`/`GET`-by-uuid/`PUT`. `api/errors.py`'s catch-all handler includes the path `uuid` whenever the failing route has one. `api/main.py` now calls `logging.basicConfig(level=logging.INFO)` at startup — previously `api` had no root-logger config at all, so its own `.info()` lines silently never emitted; this brings it to parity with `publisher`/`reimbursement`'s own entrypoints, which already called this. All 5 `reimbursement/agent/nodes/*.py` decision nodes now include the reimbursement uuid on every existing `FLOW: ...` log line. Together, a reimbursement's full lifecycle (api write → agent nodes → LangFuse trace) is grep- and LangFuse-session-filter-reconstructable end to end without re-running anything. This new helper is adopted only by `api`'s own new call sites — `publisher`/`reimbursement.validation`'s existing three-tier call sites above are unchanged.

LangFuse (provisioned in `docker-compose.yml`) is wired into `reimbursement`'s LangGraph decision graph (`agent/agent.py`'s `_langfuse_handlers()`): a memoized `langfuse.langchain.CallbackHandler` is passed to every `graph.ainvoke(...)` call, alongside `metadata={"langfuse_session_id": str(reimbursement.uuid)}` — every trace for one reimbursement (including retries) groups under one LangFuse Session, filterable by uuid. If the package is missing or the handler can't be constructed, a durable `failure_log` record (`reimbursement.langfuse_fallback`) is written instead — no invocation runs trace-less and unlogged. See `docs/codebase/INTEGRATIONS.md` for the full data-flow description.

## API Versioning

URL path prefix (`/api/v1/reimbursement`). No deprecation policy or version-coexistence pattern exists yet — only one version is defined.

## Notable Patterns

- **Byte-splice envelope construction** — the Kafka envelope is built by string-concatenating a literal prefix around the already-validated raw request bytes, not by re-parsing/re-serialising a model, so the published payload is guaranteed byte-identical to what the client sent.
- **Streaming body-size cap** — `read_capped()` counts bytes while streaming and aborts mid-stream, specifically so an oversized body is never fully buffered before rejection (a memory-exhaustion defense a post-hoc validation-layer check cannot provide).
- **Single cached config loader** — `shared.config.load_config()` is the one place every `os.getenv()` read happens; plain frozen dataclasses hold no env-reading logic of their own.
- **Advisory-lock migration serialisation** — `migrate.py` uses a PostgreSQL session-scoped advisory lock (not `yoyo`'s own table-row lock) specifically because the advisory lock releases automatically if the runner's connection drops, avoiding a permanently stuck lock after a `SIGKILL`.
- **Outcome-enum decision tree** — `publisher.processing.handle_message` and every handler it calls return an `ItemOutcome` member instead of raising; combined with `asyncio.gather` (no `return_exceptions`), this makes one item's failure structurally unable to affect another's, and gives every branch a countable value for free.
- **Transaction-wraps-publish** — a DB write and its paired Kafka publish run inside the same `conn.transaction()`, so a publish failure rolls the write back implicitly (AD-017). The same shape as the byte-splice pattern above: correctness is enforced by what the code structurally cannot do, not by a rule the next author has to remember. `publisher`'s own insert+publish is the one exception now (AD-033): its insert commits immediately in its own narrow, insert-only transaction (AD-034), and a publish failure is compensated by an explicit `delete_pending` instead of a rollback — every other write-plus-side-effect unit of work still follows this bullet's original shape unchanged.
- **Requeue-with-history retry** — rather than retrying in place, a failed item is republished as a new envelope with `retry` incremented and an `AttemptError` appended to a running list (`AttemptError.from_exception(attempt, stage, exc)` — renamed from `.next(...)` this merge), so the consumer that eventually sees `retry > 3` can render the full failure history into a human-review row without any out-of-band state. `reimbursement.validation._requeue` reuses this exact shape for its own transient resolve failures.
- **Newly-shared, currently single-consumer modules** — `shared.signals.install_shutdown_handlers` and `shared.testing.FakeProducer` were extracted this merge as genuinely cross-service contracts, but only `reimbursement` (production code and tests, respectively) has adopted them so far — `publisher` still carries its own separate signal-handling code and test double. Not yet consolidated; a candidate for a later cleanup, not a divergence bug.
- **Insert+publish extracted to a shared use case** — `publisher.processing._insert_and_publish` no longer builds the `ReimbursementEnvelope` and calls `insert_pending`/`publish` inline; both now live in `shared.reimbursement.use_cases.publish_pending`, the insert+publish counterpart to `send_human_review` (AD-025's domain-slice pattern applied to a second action).
- **`NoReturn` disambiguation helper** — `review_reimbursement._disambiguate(conn, uuid) -> NoReturn` is called only on the zero-rows-affected path of an `UPDATE ... RETURNING`; it re-queries once to decide which of two typed exceptions to raise (`ReimbursementNotFound` vs `ReimbursementNotEligible`) and always raises, never returns — keeping that ambiguity-resolution logic out of `approve_reimbursement`/`reject_reimbursement`'s main path.
- **Discriminated-union payload for one endpoint, two shapes** — `reimbursement/update/validation.py`'s `ApproveReview`/`RejectReview` share a `status: Literal["approved"|"rejected"]` field; `Field(discriminator="status")` lets one `TypeAdapter` pick the right model instead of one model with every field optional plus manual cross-field validation.
