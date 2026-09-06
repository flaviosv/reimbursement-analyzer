# JSON Structured Logging & Request Correlation Specification

## Problem Statement

This service drives monetary reimbursement decisions partly from probabilistic LLM output across three async-decoupled services (api, publisher, reimbursement) connected by Kafka. Today, logs are unstructured text (`logging.basicConfig(level=logging.INFO)`, 4 call sites) and carry no request-scoped identifier, so a single client request cannot be traced across services, through Kafka, or into the LangFuse LLM trace that decided it — violating this project's hard non-functional traceability requirement (root `CLAUDE.md`). One existing helper, `shared/logging.log_event()`, already manually `json.dumps()`s fields into the log *message* string, which means its output is not queryable as structured fields in a log aggregator (Kibana/ECS) even today. This feature makes every log line structured JSON with ECS field names, and gives every HTTP request a `correlation_id` that threads through in-process logs, Kafka messages, and LangFuse trace metadata, so any decision can be reconstructed end-to-end from logs alone.

## Goals

- [ ] Every log line emitted by api, publisher, and reimbursement is a single JSON object with ECS-conformant field names, via one centralized `configure_logging()` helper in `packages/shared`.
- [ ] Log verbosity is controlled by a `LOG_LEVEL` env var (default `"debug"`) read once per service at startup.
- [ ] Every HTTP request into the API carries a `correlation_id` (minted as `uuid.uuid7()` or echoed from an inbound `X-Request-ID` header), returned as the `X-Request-ID` response header, and automatically present on every log line emitted while that request is in flight — with no per-call-site `extra={}` needed for this field.
- [ ] The `correlation_id` survives the Kafka hop into publisher and reimbursement (via a new envelope field), so their logs for a message can be tied back to the originating HTTP request.
- [ ] The `correlation_id` is attached to the LangGraph decision graph's LangFuse trace metadata alongside the existing `langfuse_session_id`, so an LLM decision trace is queryable by the HTTP request that triggered it.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| ------- | ------ |
| Correlation-ID propagation via Kafka message headers | Decision: reuse existing typed pydantic envelope parsing via a new field instead of adding new confluent-kafka header plumbing (no site in this repo uses Kafka headers today). |
| Field allowlist / redaction / PII scrubbing for `log_event()` or the new formatter | Out of scope for this feature — `log_event()`'s existing "callers pass only pre-vetted primitive fields" contract is preserved unchanged; this repo's PII-handling directive is honored by not introducing any new PII-bearing log field, not by building a redaction layer. |
| Persisting `correlation_id` (or any correlation id) to the database | Decision: log/trace-level correlation (header + envelope + logs + LangFuse metadata) satisfies the traceability requirement without a schema migration; no new migration is in scope. |
| Renaming or changing the semantics of `ReimbursementRequest.request_id` | Pre-existing, unrelated, client-supplied business field (unique-indexed with `submitted_by`); this feature introduces `correlation_id` as a distinctly-named field specifically to avoid colliding with it. |
| Validating the format of an inbound `X-Request-ID` header | The API echoes whatever string a caller supplies verbatim (see Assumptions) — no rejection of malformed/non-UUID values. |
| Changing how/when `langfuse_session_id` itself is set | Only a new metadata key is added alongside it; the existing key and its value (`str(reimbursement.uuid)`) are untouched. |
| A log shipping/aggregation pipeline (Kibana, Filebeat, etc.) | This feature makes logs ECS-shaped and emits them to stdout via the standard `logging` module, as today; shipping them anywhere is existing/external infrastructure. |
| Structured logging or correlation IDs for the yoyo migration runner's *migration* output itself | `api/migrate.py`'s own `basicConfig` call site is in scope for `configure_logging()` (same as the other 3 call sites), but migrations run outside any HTTP/Kafka request scope, so no `correlation_id` ever applies there. |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here — nothing is left silently unclear. Items 1-8 were resolved with the user in the grilling session (`grilling-session.md`); items 9-11 are additional assumptions surfaced while writing this spec.

| # | Assumption / decision | Chosen default | Rationale | Confirmed? |
| - | ---------------------- | --------------- | --------- | ---------- |
| 1 | Correlation id format | `uuid.uuid7()` (stdlib, Python ≥3.14) | User explicitly asked for "the most modern UUID version"; RFC 9562 uuid7 is time-ordered, good for log/index sorting. | y |
| 2 | Inbound `X-Request-ID` handling | Respect and echo back if present; else generate fresh | Lets an upstream gateway/load balancer supply its own correlation id without the API overriding it. | y |
| 3 | In-process propagation mechanism | `contextvars.ContextVar` + a `logging.Filter` that injects it into every `LogRecord` | No per-call-site `extra={}` needed for this one field; works across `await` boundaries in FastAPI's async request handling. | y |
| 4 | Invalid `LOG_LEVEL` value | Fall back to `debug` (same as unset) + emit one warning log line | Never crash a service over a config typo; still surface the misconfiguration. | y |
| 5 | Naming | New id is `correlation_id` (contextvar, envelope field, log field); HTTP header stays the industry-standard `X-Request-ID` | Avoids collision with pre-existing business field `ReimbursementRequest.request_id`. | y |
| 6 | Kafka propagation mechanism | New `Optional[str] = None` field on `RequestEnvelope` and `ReimbursementEnvelope` | Reuses existing typed pydantic parsing at every produce/consume site; `Optional` keeps rolling deploys safe (an old producer's messages lack the field, a new consumer must not crash). | y |
| 7 | Correlation id persistence | None — no DB column, no migration | Log/trace-level correlation already satisfies the traceability requirement; a migration is unjustified added surface. | y |
| 8 | `log_event()` fix | Refactor to pass fields via `extra={}` instead of manually `json.dumps`-ing them into the message string; call signature unchanged | Otherwise its fields stay opaque inside one message string under the new ECS formatter, defeating this feature's purpose for every one of its existing call sites. | y |
| 9 | Inbound `X-Request-ID` format validation | None enforced — any non-empty string is accepted and echoed back verbatim | It is a diagnostic correlation aid, not a business/security key; rejecting slightly-malformed values would add a failure mode with no offsetting benefit. An empty-string header is treated as absent (a fresh id is generated) since an empty correlation id is useless for correlation. | n — logged as default, not raised with user |
| 10 | `correlation_id` absent on a consumed Kafka message (`None`, e.g. an older producer during a rolling deploy, or a message produced by a path this feature didn't reach) | Consumer logs proceed without a `correlation_id` field rather than failing | Consistent with decision 6's "safe rolling-deploy schema evolution" intent — optional really means optional at every consumer, not just at the pydantic level. | n — logged as default, not raised with user |
| 11 | ECS logging library | [`ecs-logging`](https://pypi.org/project/ecs-logging/) (PyPI), not a hand-rolled JSON formatter | Confirmed via web search to support Python 3.14 (v2.3.0, released 2026-01-19); purpose-built for stdlib `logging` → ECS field names, avoids reinventing field-name mapping. Not yet a dependency anywhere in this repo — must be added to `packages/shared/pyproject.toml`. | n — logged as default, not raised with user |

**Open questions:** none — all resolved or logged above.

---

## User Stories

### P1: JSON Structured Logging Foundation ⭐ MVP

**User Story**: As an on-call engineer, I want every service's logs emitted as one JSON object with ECS field names so I can filter and query them in a log aggregator instead of grepping free text.

**Why P1**: Every other story in this spec (correlation id in logs, across Kafka, in LangFuse) is only observable/useful once logs are structured — this is the foundation.

**Acceptance Criteria**:

1. WHEN any of api, publisher, or reimbursement starts up THEN system SHALL call a single `configure_logging()` helper (new, in `packages/shared`) exactly once, replacing that service's current `logging.basicConfig(level=logging.INFO)` call.
2. WHEN `configure_logging()` runs THEN system SHALL attach an ECS-field-name JSON formatter (via the `ecs-logging` library) to the root logger, so every subsequent `logging` call in that process emits one JSON object per line.
3. WHEN `api/migrate.py` (the yoyo migration runner) starts THEN system SHALL also call `configure_logging()` in place of its current `logging.basicConfig(level=logging.INFO, format=...)` call, since it lives inside the `api` package.
4. WHEN `LOG_LEVEL` is set to a valid Python logging level name (case-insensitive: `debug`, `info`, `warning`, `error`, `critical`) THEN system SHALL configure the root logger at that level.
5. WHEN `LOG_LEVEL` is unset THEN system SHALL configure the root logger at `debug` level.
6. WHEN any existing `shared.logging.log_event()` call site runs THEN system SHALL emit its `event` and `**fields` as real structured fields (via `extra={}`) rather than a single JSON-encoded message string, with `log_event()`'s call signature unchanged for existing callers.

**Independent Test**: Start any one of the three services with `LOG_LEVEL=info` unset vs. set, and inspect stdout — each line is valid JSON with ECS field names (e.g. `log.level`, `message`, `@timestamp`); trigger an existing `log_event()` call site (e.g. `POST /api/v1/reimbursement`) and confirm its fields (e.g. `event`, `request_ids`) appear as top-level JSON keys, not nested inside the message string.

---

### P1: HTTP-Scoped Correlation ID ⭐ MVP

**User Story**: As an SRE investigating an incident, I want every API request to carry a stable id that appears on every log line produced while handling it, so I can pull every log for one request without cross-referencing timestamps.

**Why P1**: This is the entry point that makes cross-service correlation possible at all — without it, downstream propagation (Kafka, LangFuse) has nothing to carry.

**Acceptance Criteria**:

1. WHEN an HTTP request arrives at the API without an `X-Request-ID` header THEN system SHALL mint a fresh `correlation_id` via `uuid.uuid7()`.
2. WHEN an HTTP request arrives at the API with a non-empty `X-Request-ID` header THEN system SHALL adopt that header's value as the `correlation_id` for the request instead of generating one (echoed, not re-validated as a UUID).
3. WHEN an HTTP request arrives at the API with an empty-string `X-Request-ID` header THEN system SHALL treat it as absent and mint a fresh `correlation_id`.
4. WHEN the API finishes handling any request (success or error response) THEN system SHALL return that request's `correlation_id` as the `X-Request-ID` response header.
5. WHEN any log line is emitted anywhere during that request's handling (application code, `log_event()` calls, error handlers) THEN system SHALL include the request's `correlation_id` as a field on that log line automatically, via a `ContextVar` set for the request plus a `logging.Filter` that injects it into every `LogRecord` — with no per-call-site `extra={}` needed for this field.
6. WHEN two or more requests are in flight concurrently THEN system SHALL never leak one request's `correlation_id` onto another request's log lines (contextvar isolation must hold across FastAPI's async/await request handling).

**Independent Test**: Send two concurrent `POST /api/v1/reimbursement` requests (one with an `X-Request-ID` header, one without); confirm each response's `X-Request-ID` header matches expectations (echoed vs. freshly minted), and that every log line captured during each request's handling carries only that request's own `correlation_id`, never the other's.

---

### P1: Kafka Correlation ID Propagation ⭐ MVP

**User Story**: As an SRE, I want the correlation id to survive the hop from the API into publisher and reimbursement via Kafka, so I can trace one request's full lifecycle across all three services, not just within the API process.

**Why P1**: Without this, correlation stops at the API boundary — the two async services that do the actual reimbursement work stay untraceable to the originating request.

**Acceptance Criteria**:

1. WHEN `RequestEnvelope` or `ReimbursementEnvelope` is constructed anywhere (model construction or the API's byte-spliced producer prefix) THEN system SHALL include an `Optional[str] = None` `correlation_id` field carrying the originating request's id.
2. WHEN the API publishes a batch to the `Request` Kafka topic THEN system SHALL set the envelope's `correlation_id` from the current request's `ContextVar` value — including in the byte-spliced prefix `api/reimbursement/create/producer.py` builds (not only in model-constructed envelopes).
3. WHEN publisher's requeue path constructs a new `RequestEnvelope` directly (its own second write path) THEN system SHALL carry forward the `correlation_id` from the envelope it is requeuing, unchanged.
4. WHEN publisher or reimbursement parses an incoming envelope off Kafka THEN system SHALL set that message's `correlation_id` into its own process's `ContextVar` before processing/logging that message, so every log line for that message's processing carries the id automatically (same Filter-based injection as the API).
5. WHEN a consumed envelope's `correlation_id` is `None` (e.g., produced before this feature shipped, or by a path this feature does not touch) THEN system SHALL process and log the message normally, simply omitting the `correlation_id` field from its logs — never raising or rejecting the message on that basis.

**Independent Test**: Publish a batch through the real API and follow one `request_id`'s item through publisher's and reimbursement's logs; confirm every log line touching that item — across all three services — carries the same `correlation_id` that the API's `X-Request-ID` response header returned.

---

### P1: LangFuse Trace Correlation ⭐ MVP

**User Story**: As an engineer debugging a specific reimbursement decision, I want the LangFuse trace for that decision tagged with the same correlation id as the originating HTTP request, so I can jump from an API log line straight to the LLM trace that decided it.

**Why P1**: Explicitly named in this feature's scope and directly serves this project's hard traceability NFR (root `CLAUDE.md`: "every trace carries `langfuse_session_id`... correlated the same way").

**Acceptance Criteria**:

1. WHEN `agent.decide()` invokes the LangGraph decision graph THEN system SHALL include `"correlation_id": <value>` in the same `config["configurable"]["metadata"]` dict that already carries `"langfuse_session_id"`, read from the current process's `correlation_id` context (the value consumed from the `ReimbursementEnvelope` per the Kafka Propagation story).
2. WHEN `correlation_id` is unavailable in that context (e.g., `None` per Kafka Propagation AC5) THEN system SHALL invoke `agent.decide()` without a `correlation_id` metadata key rather than passing `None` or raising.
3. WHEN this change ships THEN system SHALL require no change to `agent.decide()`'s function signature — the metadata value is read from the existing context, not passed as a new parameter.

**Independent Test**: Trigger a reimbursement decision end-to-end and inspect the resulting LangFuse trace's metadata; confirm it carries both `langfuse_session_id` and `correlation_id`, and that the latter matches the `X-Request-ID` the API returned for the originating request.

---

### P2: Invalid LOG_LEVEL Graceful Fallback

**User Story**: As an operator, I want a mistyped `LOG_LEVEL` value to degrade safely rather than crash a service, so a config typo never causes an outage.

**Why P2**: Robustness/edge-case handling on top of the P1 logging foundation — the service already works correctly with valid or unset `LOG_LEVEL`; this only guards against a typo.

**Acceptance Criteria**:

1. WHEN `LOG_LEVEL` is set to a value that is not a recognized Python logging level name THEN system SHALL configure the root logger at `debug` level (same as unset) rather than raising.
2. WHEN that fallback occurs THEN system SHALL emit exactly one `warning`-level log line naming the invalid value received, using the same `configure_logging()` call that performs the fallback.

**Independent Test**: Start any one service with `LOG_LEVEL=bogus`; confirm the service starts successfully, logs at `debug` level, and one warning line naming `bogus` appears among its startup logs.

---

### P3: Logging Convention Documentation

**User Story**: As a future contributor, I want this repo's `CLAUDE.md` to document the logging/correlation-id conventions so I follow them without re-deriving them from source.

**Why P3**: Improves long-term maintainability but blocks nothing functional — the feature works correctly whether or not this document exists.

**Acceptance Criteria**:

1. WHEN this feature ships THEN system SHALL add a "Logging" section to this repo's root `CLAUDE.md` covering: calling `configure_logging()` at every service entrypoint, the `LOG_LEVEL` env var and its default/fallback, and the `correlation_id` propagation chain (HTTP header → ContextVar → Kafka envelope field → LangFuse metadata).

---

## Edge Cases

- WHEN `configure_logging()` is called more than once in the same process (e.g., import side effects or test setup) THEN system SHALL not duplicate log handlers or emit duplicate JSON lines per log call.
- WHEN a `log_event()` caller passes a field name that collides with a reserved `LogRecord`/ECS attribute (e.g. `message`, `level`, `event` itself passed twice) THEN system SHALL follow the pre-existing "callers are responsible for passing only pre-vetted fields" contract — no new collision-detection is added by this feature.
- WHEN code runs outside any HTTP request or Kafka message scope (e.g., service startup/shutdown, the lifespan context manager, `api/migrate.py`) THEN system SHALL log normally with no `correlation_id` field present, never raising for its absence.
- WHEN the `ecs-logging` dependency is added THEN system SHALL declare it in `packages/shared/pyproject.toml` only, consumed transitively by api/publisher/reimbursement the same way other `shared`-owned dependencies already are.
- WHEN an inbound `X-Request-ID` header value is very long or contains characters that would be unsafe in a raw JSON log field THEN system SHALL rely on the JSON formatter's own string-escaping (already required for arbitrary field values) rather than adding new input sanitization specific to this field.

---

## Requirement Traceability

Each requirement gets a unique ID for tracking across design, tasks, and validation.

| Requirement ID | Story | Phase | Status |
| --------------- | ----- | ----- | ------ |
| LOG-01 | P1: JSON Structured Logging Foundation (AC1: `configure_logging()` called once per service) | Design | Pending |
| LOG-02 | P1: JSON Structured Logging Foundation (AC2: ECS JSON formatter on root logger) | Design | Pending |
| LOG-03 | P1: JSON Structured Logging Foundation (AC3: `api/migrate.py` also uses `configure_logging()`) | Design | Pending |
| LOG-04 | P1: JSON Structured Logging Foundation (AC4: valid `LOG_LEVEL` sets root logger level) | Design | Pending |
| LOG-05 | P1: JSON Structured Logging Foundation (AC5: unset `LOG_LEVEL` defaults to `debug`) | Design | Pending |
| LOG-06 | P1: JSON Structured Logging Foundation (AC6: `log_event()` uses `extra={}`, not message-string JSON) | Design | Pending |
| LOG-07 | P2: Invalid LOG_LEVEL Graceful Fallback (AC1: invalid value falls back to `debug`) | Design | Pending |
| LOG-08 | P2: Invalid LOG_LEVEL Graceful Fallback (AC2: one warning line naming the invalid value) | Design | Pending |
| CORR-01 | P1: HTTP-Scoped Correlation ID (AC1: mint `uuid.uuid7()` when header absent) | Design | Pending |
| CORR-02 | P1: HTTP-Scoped Correlation ID (AC2: echo non-empty inbound `X-Request-ID`) | Design | Pending |
| CORR-03 | P1: HTTP-Scoped Correlation ID (AC3: empty-string header treated as absent) | Design | Pending |
| CORR-04 | P1: HTTP-Scoped Correlation ID (AC4: `X-Request-ID` returned on every response) | Design | Pending |
| CORR-05 | P1: HTTP-Scoped Correlation ID (AC5: ContextVar + Filter auto-inject into every log line) | Design | Pending |
| CORR-06 | P1: HTTP-Scoped Correlation ID (AC6: no cross-request leakage under concurrency) | Design | Pending |
| CORR-07 | P1: Kafka Correlation ID Propagation (AC1: `correlation_id` field added to both envelopes) | Design | Pending |
| CORR-08 | P1: Kafka Correlation ID Propagation (AC2: API sets it on publish, incl. byte-spliced prefix) | Design | Pending |
| CORR-09 | P1: Kafka Correlation ID Propagation (AC3: publisher's requeue path carries it forward) | Design | Pending |
| CORR-10 | P1: Kafka Correlation ID Propagation (AC4: consumers set it into their own ContextVar) | Design | Pending |
| CORR-11 | P1: Kafka Correlation ID Propagation (AC5: `None` correlation_id never crashes a consumer) | Design | Pending |
| CORR-12 | P1: LangFuse Trace Correlation (AC1: `correlation_id` added to LangGraph metadata dict) | Design | Pending |
| CORR-13 | P1: LangFuse Trace Correlation (AC2: unavailable id omits the metadata key, never `None`/raise) | Design | Pending |
| CORR-14 | P1: LangFuse Trace Correlation (AC3: no signature change to `agent.decide()`) | Design | Pending |
| DOC-01 | P3: Logging Convention Documentation (AC1: CLAUDE.md "Logging" section) | Design | Pending |

**ID format:** `[CATEGORY]-[NUMBER]` — `LOG-*` for JSON/formatter/level requirements, `CORR-*` for correlation-id requirements, `DOC-*` for documentation.

**Status values:** Pending → In Design → In Tasks → Implementing → Verified

**Coverage:** 23 total, 0 mapped to tasks, 23 unmapped ⚠️ (Tasks phase not yet run)

---

## Success Criteria

How we know the feature is successful:

- [ ] Every log line across api, publisher, and reimbursement is a single valid JSON object with ECS field names (no free-text `logging.basicConfig` output remains anywhere in the 4 identified call sites).
- [ ] A single `X-Request-ID` value can be followed, unbroken, from an API response header through publisher's and reimbursement's logs to the LangFuse trace that decided the reimbursement — verifiable by grep/query alone, without re-running the request.
- [ ] `LOG_LEVEL` changes verbosity identically across all three services with one env var, defaulting to `debug` when unset or invalid.
- [ ] Existing `log_event()` call sites' fields (e.g. `request_ids`, `accepted_count`) are queryable as distinct JSON fields, not embedded in one message string.
