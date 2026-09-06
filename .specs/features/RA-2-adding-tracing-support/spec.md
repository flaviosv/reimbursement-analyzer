# Adding Tracing Support Specification

## Problem Statement

A reimbursement request today produces logs scattered across three independent
processes — `api`, `publisher`, `reimbursement` — correlated only by
convention (`reimbursement.uuid` threaded manually into log lines) and by a
separate LangFuse trace for the LLM decision step alone. There is no single,
queryable record of *how a request actually moved through the system*: which
service touched it, in what order, how long each hop took, and whether a
retry/requeue happened along the way. This repo's non-functional requirement
(`docs/SCOPE.md` `Audit trail`/`Traceability`, mirrored in this repo's
`CLAUDE.md`) treats full traceability of every operation as a hard,
functional requirement — not aspirational — precisely because decisions here
are partly driven by probabilistic LLM output in a financial domain. This
feature is the distributed-tracing half of that requirement: it wires
OpenTelemetry (OTel) into all three services, exporting to the
already-provisioned Elastic APM Server, so one request's full lifecycle is
reconstructable as a single linked trace.

## Goals

- [ ] Every one of `api`, `publisher`, `reimbursement` emits OTel spans, exported via OTLP/HTTP to the Elastic APM Server, using upstream OTel Python libraries only (no vendor-specific Elastic APM agent).
- [ ] A single reimbursement request's full lifecycle — `api`'s HTTP handling → `publisher`'s consume/produce → `reimbursement`'s consume — appears as **one** linked distributed trace (one trace ID), not three independent span sets, propagated through Kafka message headers.
- [ ] The standing instrumentation requirement is captured in this repo's `CLAUDE.md` so it governs all future development, not just this feature.
- [ ] The feature is verified against real, running services (on the sibling `local-env` project's k3s/Tilt setup) confirming an actual trace lands in the Elastic APM Server — not verified by code inspection alone.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| --- | --- |
| Vendor-specific Elastic APM agent | Seed request is explicit: upstream OTel Python libraries only, exporting OTLP/HTTP to the APM Server's OTLP ingest endpoint. |
| OTel metrics or logs signals | Seed request and all grilling decisions scope this feature to tracing (spans) only. |
| A new/non-default sampling strategy or sampling env var | Decision (round 1–2, #3): default `ParentBased(AlwaysOn)`, no new sampling configuration surface. |
| Per-route manual span instrumentation in `api` | Decision #3 (seed): `FastAPIInstrumentor.instrument_app(app)` once at startup is the entire `api` integration — no per-route changes. |
| Restructuring the `publisher`/`reimbursement` consume loops | Decision #4 (seed): the explicit `process_message` span wraps existing handling logic; the loop shape is untouched. |
| Unifying `publisher`'s own signal handler with `shared.signals.install_shutdown_handlers` (used by `reimbursement`) | Pre-existing inconsistency, called out in grilling as out-of-scope-to-fix; this feature hooks tracer shutdown into each service's existing shutdown point as-is. |
| A new ephemeral local Docker/compose OTel collector or other new local infra | Decision #5: no new local infra is built; verification uses the real in-cluster APM Server via the sibling `local-env` project. |
| Renaming/rewiring the `reimbursement.uuid` span attribute to a future broader `correlation_id` | Decision #2's forward-looking note: a separate, currently in-flight session is introducing `correlation_id`; this feature uses `reimbursement.uuid` now and is expected to be revisited once that lands. |
| Retrying or re-sending a span that failed to export | Standard OTel `BatchSpanProcessor`/OTLP exporter behavior (best-effort export with its own internal retry policy) is used as-is; no custom re-send/dead-letter mechanism is built for span data (see Assumptions). |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here — nothing is left silently unclear. (Frontier was confirmed empty by the user before this Specify pass — these are precision gaps found while writing testable ACs, not undiscussed scope.)

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --- | --- | --- | --- |
| Scope of "stamp `reimbursement.uuid` at every hop" on `api`'s auto-instrumented span | Stamped only on the routes where the uuid is already resolved as a path parameter — `GET /api/v1/reimbursement/{uuid}` and `PUT /api/v1/reimbursement/{uuid}` — never on `POST /api/v1/reimbursement` (create). | The create route's request span cannot carry a `reimbursement.uuid` attribute that does not exist yet — the uuid is minted downstream when `publisher` inserts the row. "Every hop" is read as "every hop where the value is knowable," the only reading precise enough to test. | n (precision gap resolved per Specify's closure gate; not itself discussed in grilling) |
| Behavior when the OTLP endpoint (Elastic APM Server) is unreachable, at startup or during export | No custom fallback sink is built for span data. The service starts and keeps processing messages/requests regardless of APM Server reachability; span export failures surface only through whatever default error signal the OTel SDK/OTLP exporter itself produces. | `CLAUDE.md`'s "no silent fallback" directive is written for the trace of *how a decision was reached* (LLM prompt/response, rule evaluation — the LangFuse trace, which already has its own fallback-to-file-logging rule). Distributed-tracing spans are operational telemetry about the request's plumbing, not the decision record itself; building a bespoke durable fallback for raw span data is disproportionate scope, and the SDK's own best-effort export already keeps the business path from ever blocking on the APM Server. | n |
| Which parts of `README.md` count as "the docker-compose section" to drop alongside `docker-compose.yml` | The whole `## Run` section (stack startup, service/port table, health-check, `docker compose down -v`), **plus** every other passage whose instructions stop working once `docker-compose.yml` is gone: the "Database migrations" section's "Applying them happens automatically on `docker compose up`" line, and the "### End-to-end suite" section's `docker compose up -d` step and its "stack must already be up" framing. | Decision #5 already establishes `docker-compose.yml` as stale; leaving other passages that invoke a command that no longer works would recreate the same staleness the decision set out to remove. Narrowly deleting only the section literally titled "Run" would leave two more dangling, broken instructions behind. | n |

**Open questions:** none — all resolved or logged above.

---

## User Stories

### P1: One linked distributed trace across the full request lifecycle ⭐ MVP

**User Story**: As an on-call engineer investigating a reimbursement incident, I want a single distributed trace spanning `api` → `publisher` → `reimbursement` so that I can see the full lifecycle of one request without manually correlating logs across three processes.

**Why P1**: This is the entire point of the feature — without cross-service trace-context propagation, each service would emit isolated, uncorrelated spans, which is no better than today's per-service logging.

**Acceptance Criteria**:

1. WHEN `api`, `publisher`, or `reimbursement` starts THEN the service SHALL initialize a `TracerProvider` with resource attribute `service.name` set to `reimbursement-analyzer-api`, `reimbursement-analyzer-publisher`, or `reimbursement-analyzer-reimbursement` respectively, register it as the process's global tracer provider, and configure a `BatchSpanProcessor` exporting spans via OTLP/HTTP to the endpoint read from `OTEL_EXPORTER_OTLP_ENDPOINT` (defaulting to `http://apm-server.shared-services.svc.cluster.local:8200` when unset).
2. WHEN the service process shuts down at its existing shutdown hook (`api`'s FastAPI `lifespan` after `yield`; `publisher`/`reimbursement`'s `_serve()` after `run(...)` returns, before the resource-managing `async with` block exits) THEN the service SHALL flush pending spans and shut down the `TracerProvider` cleanly before the process exits.
3. WHEN `api` starts THEN it SHALL call `FastAPIInstrumentor.instrument_app(app)` exactly once at startup, with no per-route code changes, so every inbound HTTP request produces a span automatically.
4. WHEN `api` or `publisher` publishes a Kafka message through `shared.producer.publish` — including a retry/requeue re-publish of the same logical item — THEN it SHALL inject the current trace context into that message's Kafka headers before it is produced.
5. WHEN `publisher` or `reimbursement` consumes a Kafka message THEN it SHALL extract any trace context present in that message's headers and start an explicit span (e.g. `process_message`) as a child of that context, wrapping the message's existing handling logic, without restructuring the consume loop's control flow.
6. WHEN `publisher` consumes a `Request` message and, in handling it, publishes a `Reimbursement` message (including a retry/requeue re-publish) THEN the injected trace context on that outbound message SHALL be a child of the current `process_message` span, so `reimbursement`'s later consumption of it links to the same trace `api` started — not a new, disconnected trace.
7. WHEN a full request lifecycle runs — `api` receives an HTTP request, publishes a `Request`; `publisher` consumes it and publishes a `Reimbursement`; `reimbursement` consumes that — THEN every span produced across all three services for that request SHALL share one trace ID, observable as a single connected trace in the Elastic APM Server.
8. WHEN `publisher` or `reimbursement` consumes a Kafka message that carries no trace-context headers (e.g., produced before this feature shipped, or by a path that predates header injection) THEN the consumer SHALL still start its own span, as a new trace root, rather than fail, skip processing, or raise.

**Independent Test**: With `api`, `publisher`, and `reimbursement` running as real services on the local k3s cluster (via the sibling `local-env` project's Tilt setup, which reaches the real `apm-server.shared-services.svc.cluster.local:8200`), submit a real `POST /api/v1/reimbursement` request and confirm — by querying the Elastic APM Server — one trace ID with spans contributed by all three services.

---

### P2: Span attributes for message identity and cross-system correlation

**User Story**: As an on-call engineer, I want each span to carry identifying attributes so that I can pivot from a trace directly to the Kafka message it processed and to the LangFuse trace of the LLM decision it drove.

**Why P2**: Builds directly on P1's linked trace — without these attributes the trace shows *that* spans are connected but not *which* message or which business row each span was about.

**Acceptance Criteria**:

1. WHEN `publisher` or `reimbursement`'s `process_message` span is created for a consumed message THEN it SHALL include that message's topic, partition, and offset as span attributes.
2. WHEN a span is created at a hop where `reimbursement.uuid` is already known — `publisher`'s `process_message` span, `reimbursement`'s `process_message` span, and `api`'s auto-instrumented request span for `GET /api/v1/reimbursement/{uuid}` and `PUT /api/v1/reimbursement/{uuid}` (per the Assumptions table entry above) — THEN the service SHALL set `reimbursement.uuid` as a span attribute on that span.
3. WHEN `reimbursement`'s LangGraph decision graph runs inside a traced `process_message` span THEN the existing LangFuse `langfuse_session_id=<reimbursement.uuid>` correlation SHALL continue to function unchanged — this feature adds an OTel span attribute alongside it, and does not modify or replace the LangFuse wiring.

**Independent Test**: Trigger a `GET /api/v1/reimbursement/{uuid}` request and a `publisher`/`reimbursement` consume cycle, and confirm in the Elastic APM Server that the resulting spans carry `reimbursement.uuid` plus (for the consumer spans) topic/partition/offset attributes.

---

### P3: Repo hygiene — dependency placement, stale local-dev docs, and the standing directive

**User Story**: As a developer working on this repo after this feature ships, I want tracing dependencies centralized in the right place, no stale docker-compose instructions left behind, and a clear standing rule in `CLAUDE.md` so I instrument any future code path correctly without re-deriving the requirement.

**Why P3**: Necessary for the feature to be complete and non-misleading, but no single item here is required for a trace to actually work end to end (P1/P2 already deliver that).

**Acceptance Criteria**:

1. WHEN `shared`'s dependencies are declared THEN its `pyproject.toml` SHALL add `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http` (current stable versions compatible with Python 3.14.7), so `TracerProvider` init and the Kafka header inject/extract helpers live in one new `shared.tracing` module, following the `shared.config`/`shared.signals` precedent.
2. WHEN `api`'s dependencies are declared THEN its `pyproject.toml` SHALL additionally add `opentelemetry-instrumentation-fastapi`; `publisher`'s and `reimbursement`'s `pyproject.toml` files SHALL add no new tracing-specific dependency beyond what `shared` already brings in transitively.
3. WHEN this feature ships THEN `docker-compose.yml` SHALL be removed from the repo, and `README.md` SHALL no longer contain instructions that depend on it (per the Assumptions table's resolved scope for this).
4. WHEN this feature ships THEN this repo's `CLAUDE.md` SHALL gain a standing directive stating that `api`, `publisher`, and `reimbursement` are always instrumented with OTel/OTLP to the Elastic APM Server (`api`'s HTTP layer via auto-instrumentation; any new Kafka consumer or background task must wrap its processing in an explicit span), plus a short informational note that this repo's real local dev/verification target is the sibling `local-env` project's k3s/Tilt setup (not an operational instruction for other developers to follow verbatim, since that path is specific to this machine).

**Independent Test**: `git grep` for `docker-compose` in the repo (excluding this spec's own record of the decision) returns nothing under `README.md`, and `docker-compose.yml` is absent; `CLAUDE.md` contains the new directive; `shared`, `api`, `publisher`, `reimbursement`'s `pyproject.toml` files carry exactly the dependency placement above.

---

## Edge Cases

- WHEN `OTEL_EXPORTER_OTLP_ENDPOINT` is unset THEN the service SHALL fall back to `http://apm-server.shared-services.svc.cluster.local:8200` rather than failing to start.
- WHEN the Elastic APM Server is unreachable — at process startup or at any later export attempt — THEN the affected service SHALL still start (or keep running) and keep processing HTTP requests / Kafka messages; tracing failures never block or fail the business path (see Assumptions table).
- WHEN a message is redelivered after a crash mid-processing (pre-existing redelivery semantics, unchanged by this feature) THEN the redelivered message's `process_message` span SHALL be a distinct span from the original attempt's, since re-consuming necessarily calls `start_as_current_span` again — this feature does not deduplicate spans for redelivered messages.
- WHEN a retry/requeue re-publish happens inside `publisher` or `reimbursement` (existing retry-then-escalate / compensating-publish mechanisms, unchanged by this feature) THEN the re-published message's injected trace headers SHALL reflect whatever span is active at that point in the existing handling code — still a descendant of the same originating trace, per P1 AC6.
- WHEN `shared.producer.publish` is called from any current or future call site THEN header injection SHALL apply uniformly (there is exactly one `publish` function; this feature does not special-case some callers over others).

---

## Requirement Traceability

Each requirement gets a unique ID for tracking across design, tasks, and validation.

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| OTEL-01 | P1: One linked distributed trace | Tasks (T3, T7, T9, T11) | Verified (⚠️ minor: no dedicated publisher/reimbursement `_serve()` init test, see validation.md Gap #3) |
| OTEL-02 | P1: One linked distributed trace | Tasks (T7, T9, T11) | Verified (⚠️ minor: same as OTEL-01, Gap #3) |
| OTEL-03 | P1: One linked distributed trace | Tasks (T7) | Verified |
| OTEL-04 | P1: One linked distributed trace | Tasks (T3, T4) | Verified |
| OTEL-05 | P1: One linked distributed trace | Tasks (T3, T10, T11) | Verified |
| OTEL-06 | P1: One linked distributed trace | Tasks (T10, T11) | Verified |
| OTEL-07 | P1: One linked distributed trace | Tasks (T12) | Verified (⚠️ mechanism proven against a real Kafka broker; full 3-real-service-in-APM-Server proof is the separate live-infra verification step, see validation.md Gap #2) |
| OTEL-08 | P1: One linked distributed trace | Tasks (T3, T12) | Verified |
| OTEL-09 | P2: Span attributes for correlation | Tasks (T3, T10, T11) | Verified |
| OTEL-10 | P2: Span attributes for correlation | Tasks (T8, T10, T11) | Verified (fixed post-Verifier, commit `ab382f9` — `escalate_item`'s uuid stamp) |
| OTEL-11 | P2: Span attributes for correlation | Tasks (T11) | Verified |
| OTEL-12 | P3: Repo hygiene | Tasks (T1, T2) | Verified |
| OTEL-13 | P3: Repo hygiene | Tasks (T1) | Verified |
| OTEL-14 | P3: Repo hygiene | Tasks (T13) | Verified |
| OTEL-15 | P3: Repo hygiene | Tasks (T15) | Verified |

**ID mapping:** OTEL-01..08 = P1 AC1..8 (in order); OTEL-09..11 = P2 AC1..3; OTEL-12..15 = P3 AC1..4.

**ID format:** `OTEL-[NUMBER]` — chosen distinct from the existing `TRC-NN` prefix (the separate, already-shipped `traceability-correlation-ids` feature) since this is infrastructure-level distributed tracing, not the business `correlation_id`/log-threading work `TRC` covers.

**Status values:** Pending → In Design → In Tasks → Implementing → Verified

**Coverage:** 15 total, 15 verified (2 with a flagged, non-blocking caveat — see `validation.md`). Independent Verifier: PASS. See `.specs/features/RA-2-adding-tracing-support/validation.md` for full evidence.

---

## Success Criteria

How we know the feature is successful:

- [ ] A real `POST /api/v1/reimbursement` request submitted against the services running on the local k3s cluster produces one trace ID with spans contributed by `api`, `publisher`, and `reimbursement`, confirmed present in the Elastic APM Server — not merely inferred from code review.
- [ ] Every `process_message` span (publisher and reimbursement) carries topic/partition/offset attributes, and `reimbursement.uuid` appears as a span attribute at every hop where the spec resolves it to be known.
- [ ] `docker-compose.yml` and its README instructions are gone from the repo; no new local ephemeral tracing infra was added in their place.
- [ ] `CLAUDE.md` carries the standing OTel instrumentation directive plus the sibling-project local-dev note.
- [ ] No service fails to start or crashes when the Elastic APM Server is unreachable.
