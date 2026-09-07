# Prometheus Metrics MVP Specification

## Problem Statement

`docs/codebase/ARCHITECTURE.md` states plainly that this project has no metrics infrastructure today — only log-based observability. `api`, `publisher`, and `reimbursement` are three separately deployable k3s processes connected by Kafka, driving financial reimbursement decisions partly from probabilistic LLM output, and none of them expose a single counter, gauge, or histogram: not HTTP traffic/error rates, not decision latency, not how many items are stuck in human review, not which deterministic rule or LLM call produced an outcome. This feature adds a Prometheus `/metrics` exposition endpoint to all three services, instrumenting the 16 metrics converged on and approved during grilling (`grilling-session.md`), so the system's request handling, decision pipeline, and Kafka message lifecycle become observable without re-deriving anything from raw logs.

## Goals

- [ ] `api` exposes `GET /metrics` (Prometheus text exposition format, no auth) alongside its existing `/health` endpoint.
- [ ] `publisher` and `reimbursement` each expose their own `/metrics` HTTP port (no auth) — 3 independent scrape targets, not one centralized endpoint.
- [ ] All 16 approved metrics (see Metric Catalog) are implemented exactly as named, typed, labeled, and scoped in the approved table — no additions, no omissions, no renames.
- [ ] `apply_policies.py`'s 3 deterministic rules gain a fixed rule-ID enum (none exists today), used to label `reimbursement_policy_rule_triggered_total`.
- [ ] `docs/METRICS.md` documents the shipped metric catalog for future readers.
- [ ] `docs/codebase/CONCERNS.md` records the two accepted MVP gaps this feature deliberately leaves open: no `/metrics` auth, and 3 new scrape targets needing k3s manifest changes.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| ------- | ------ |
| Authentication on any `/metrics` endpoint | Explicit non-goal for this MVP (grilling session decision 12); tracked as a `docs/codebase/CONCERNS.md` follow-up, not built here. |
| DB connection-pool metrics | Belongs to a dedicated Postgres exporter, not app code — would duplicate infrastructure one layer down (grilling session decision 12). |
| A single centralized/federated metrics endpoint | Architecture decision: 3 separate scrape targets instead (grilling session decision 8) — in-process counters in one process are invisible to another process's endpoint; no federation layer is built to bridge that. |
| Prometheus multiprocess mode | Not needed — `api` runs single-worker uvicorn with no `--workers`, one process per k3s pod, horizontal scaling is per-pod (grilling session codebase research). |
| k3s manifest / scrape-target deployment config changes | Tracked as a `docs/codebase/CONCERNS.md` follow-up; out of scope for this feature's own deployment config unless trivial. |
| Backfilling historical status transitions | `reimbursement_status_transitions_total` observes transitions as they happen from this feature's deploy point forward; no reconstruction of past transitions from existing rows. |
| Any change to reimbursement decision-making behavior | This feature only adds instrumentation and a rule-ID label; the deterministic thresholds (90 days / ≤200 / >2000) and their outcomes are unchanged. |
| Free-text `decision_reason`, `request_id`/`correlation_id`/`user_id`, or reviewer email (`reviewed_by`) as metric labels | Hard cardinality/PII constraint — unbounded-cardinality or PII fields must never become label values (grilling session decision 2). |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here — nothing is left silently unclear. The grilling session (`grilling-session.md`) is settled input and is not re-litigated; items below are implementation-level gaps it left open, or defaults chosen while writing this spec, consistent with the user's "no further pauses expected" note.

| # | Assumption / decision | Chosen default | Rationale | Confirmed? |
| - | ---------------------- | --------------- | --------- | ---------- |
| 1 | Rule-ID enum values for `apply_policies.py` | `stale-receipt-reject`, `low-value-auto-approve`, `high-value-human-review` | Grilling session mandates the enum exist (decision 5) but never names the 3 values; names describe each rule's condition and outcome, matching the reject-first evaluation order already in code. | n — logged as default, not raised with user |
| 2 | Metrics HTTP port for `publisher`/`reimbursement` | New `METRICS_PORT` env var per service (`publisher` default `9101`, `reimbursement` default `9102`), served via `prometheus_client`'s own `start_http_server()` | Neither service has an HTTP server today; `prometheus_client` ships a purpose-built minimal server for exactly this case, avoiding a new web-framework dependency in either service. Verified against official `prometheus_client` docs per grilling session decision 13. | n — logged as default, not raised with user |
| 3 | Histogram bucket boundaries | Left to implementation/Design, not fixed numerically in this spec | The only hard requirement carried from grilling session is that `agent_decision_duration_seconds`/`time_to_decision_seconds`/`agent_node_duration_seconds` (sub-second-to-minute scale) and `review_wait_seconds` (minutes-to-days scale) never share a bucket set (decisions 3-4). Exact bucket lists are an implementation detail. | n — logged as default, not raised with user |
| 4 | `prometheus_client` dependency placement | Added to `packages/shared/pyproject.toml` | All three services need it; matches the existing `ecs-logging` precedent (RA-1) for a library every service imports, and AD-025's "shared owns cross-service" convention. | n — logged as default, not raised with user |
| 5 | `reimbursement_review_wait_seconds` entry-time computation | Read from the `reimbursement` row's own `updated_at` column at the moment the PUT request is handled, before that request's own write overwrites it | No transition-history table exists (grilling session codebase research: "only current status + `updated_at`"). Valid because a row enters `human-review` at most once and leaves it at most once, so `updated_at` at read time reliably marks entry time. | n — logged as default, not raised with user |
| 6 | `reimbursement_status_count` scrape-time DB failure | Skip the gauge for that scrape (log a warning); the rest of `/metrics` still returns normally, not a failed scrape | Consistent with "no silent fallback" only applying to decision-affecting traces (root `CLAUDE.md`) — a metrics scrape is not a reimbursement decision, so degrading gracefully here is appropriate; failing the whole endpoint over one query would lose all 16 metrics' visibility, not just one. | n — logged as default, not raised with user |
| 7 | `path` label value for an unmatched route (404) | A fixed literal (e.g. `"unmatched"`), never the raw requested path | Keeps `path` cardinality bounded to the app's actual registered route count, per the hard cardinality rule (grilling session decision 2) — an unmatched path is attacker/client-controlled and otherwise unbounded. | n — logged as default, not raised with user |

**Open questions:** none — all resolved or logged above.

---

## Metric Catalog (Approved, Final — 16 Metrics)

Copied verbatim from `grilling-session.md`'s approved table. No metric here may be renamed, retyped, relabeled, or dropped without a new grilling/decision round.

| # | Metric | Type | Service | Labels | Description |
|---|--------|------|---------|--------|--------------|
| 1 | `api_http_requests_total` | Counter | api | method, path, status_code | Every HTTP request handled by the API, including error responses |
| 2 | `api_http_request_duration_seconds` | Histogram | api | method, path | HTTP request handling latency |
| 3 | `reimbursement_status_count` | Gauge | api | status | Current number of reimbursement requests in each status, refreshed each scrape via a DB query |
| 4 | `reimbursement_review_wait_seconds` | Histogram | api | — | Time an item spent in `human-review` before a reviewer resolved it |
| 5 | `reimbursement_status_transitions_total` | Counter | api + reimbursement | from, to | Every observed status transition, wherever it happens |
| 6 | `reimbursement_agent_decision_duration_seconds` | Histogram | reimbursement | — | Wall-clock time of the full `agent.decide()` graph run |
| 7 | `reimbursement_time_to_decision_seconds` | Histogram | reimbursement | — | End-to-end time from request creation to a terminal automated decision (includes Kafka queue wait) |
| 8 | `reimbursement_agent_node_duration_seconds` | Histogram | reimbursement | node, model | Per-node latency inside the decision graph; `model` populated only for `extract_fields`/`analysis` |
| 9 | `reimbursement_agent_llm_calls_total` | Counter | reimbursement | model, outcome | LLM invocations, by model and success/failure |
| 10 | `reimbursement_policy_rule_triggered_total` | Counter | reimbursement | rule | Which deterministic policy rule fired |
| 11 | `reimbursement_decision_failure_escalations_total` | Counter | reimbursement | — | Decisions force-escalated to human-review because a single decision-pipeline attempt failed — never retried |
| 12 | `reimbursement_messages_consumed_total` | Counter | reimbursement | topic | Kafka messages consumed |
| 13 | `reimbursement_messages_requeued_total` | Counter | reimbursement | topic | Messages requeued after a transient processing failure |
| 14 | `publisher_messages_consumed_total` | Counter | publisher | topic | Kafka messages consumed |
| 15 | `publisher_messages_requeued_total` | Counter | publisher | topic | Messages requeued after a transient processing failure |
| 16 | `publisher_duplicate_dropped_total` | Counter | publisher | — | Duplicate messages dropped by idempotency handling (closes `docs/RISKS.md` R-004) |

Type coverage: 1 Gauge, 5 Histograms, 10 Counters.

**Naming rule (hard constraint, grilling session decision 13):** every Counter above is constructed in code *without* the `_total` suffix (e.g. `Counter("reimbursement_messages_consumed", ...)`) — `prometheus_client` auto-appends `_total` at exposition time. The names in this table already show the correct exposed form; code must not double the suffix.

---

## User Stories

### P1: Metrics Endpoint Infrastructure — 3 Independent Scrape Targets ⭐ MVP

**User Story**: As an SRE, I want each of `api`, `publisher`, and `reimbursement` to expose its own `/metrics` endpoint so Prometheus can scrape each process's in-process counters directly, without any cross-process aggregation layer.

**Why P1**: Every other story in this spec only produces observable output once an endpoint exists to expose it — this is the foundation, and it is also the one deliberate deployment-shape change (3 scrape targets instead of 1) that every other story assumes.

**Acceptance Criteria**:

1. WHEN the `api` service starts THEN system SHALL expose `GET /metrics` returning the current process's metrics in Prometheus text exposition format, registered alongside the existing `/health` route, with no authentication required.
2. WHEN the `publisher` service starts THEN system SHALL start a dedicated metrics HTTP server on a configurable port (see Assumption 2) exposing `/metrics` in Prometheus exposition format, independent of `api`'s endpoint, with no authentication required.
3. WHEN the `reimbursement` service starts THEN system SHALL start its own dedicated metrics HTTP server on a configurable port (see Assumption 2) exposing `/metrics` in Prometheus exposition format, independent of the other two services' endpoints, with no authentication required.
4. WHEN any of the three `/metrics` endpoints is scraped THEN system SHALL return only the metrics owned by that single process — no shared registry, aggregation, or federation across services.
5. WHEN a Counter metric is constructed anywhere in code THEN system SHALL name it without a `_total` suffix, per the Metric Catalog's naming rule.

**Independent Test**: Start all three services locally; `curl` each of the three `/metrics` URLs and confirm each returns HTTP 200 with Prometheus-format text, with no request headers or credentials supplied.

---

### P1: API HTTP RED Metrics ⭐ MVP

**User Story**: As an SRE, I want traffic and latency visibility on the API's own HTTP layer, independent of what a request's payload eventually decides, so I can see if the API itself is slow or erroring before looking at reimbursement outcomes.

**Why P1**: Named explicitly in the approved table (metrics 1-2) as the API's own RED (Rate/Errors/Duration) signal.

**Acceptance Criteria**:

1. WHEN `api` finishes handling any HTTP request (success or error) THEN system SHALL increment `api_http_requests_total` (Counter), labeled by `method`, `path`, `status_code`.
2. WHEN `api` finishes handling any HTTP request THEN system SHALL record its duration in `api_http_request_duration_seconds` (Histogram), labeled by `method`, `path`.
3. WHEN either metric's `path` label is recorded THEN system SHALL use the FastAPI route template (e.g. `/api/v1/reimbursement/{uuid}`), never the resolved URL containing a real path-parameter value.
4. WHEN a request matches no registered route (404) THEN system SHALL record its `path` label as the fixed literal from Assumption 7, not the raw unmatched path, keeping `path` cardinality bounded to the app's actual route table.

**Independent Test**: Send a mix of requests (a valid route with a real UUID, a 404 on a nonexistent path) to `api`, then scrape `/metrics` and confirm `api_http_requests_total`/`api_http_request_duration_seconds` show route-template `path` values only — no UUID and no raw 404 path ever appears as a label value.

---

### P1: Reimbursement Status & Review Observability ⭐ MVP

**User Story**: As a reviewer-team lead, I want to see how many reimbursements sit in each status right now and how long items actually wait in human review, so I can tell if the review queue is backing up.

**Why P1**: Named explicitly in the approved table (metrics 3-5); directly serves the "is the human review queue backing up" question the plain status count alone can't answer (grilling session decision 4).

**Acceptance Criteria**:

1. WHEN `api`'s `/metrics` endpoint is scraped THEN system SHALL query the current count of `reimbursement` rows per status and set `reimbursement_status_count` (Gauge, labeled by `status`) to that query's result, refreshed at scrape time — not incrementally maintained in memory.
2. WHEN a human reviewer resolves an item via `PUT` (to `human-approved` or `human-rejected`) THEN system SHALL record the elapsed time since that item entered `human-review` in `reimbursement_review_wait_seconds` (Histogram, no labels), per Assumption 5's entry-time computation, using distinctly wider bucket boundaries than the automated-decision-latency histograms in the next story.
3. WHEN a reimbursement row's status changes via a write in `api` (a human `PUT` decision) or in `reimbursement` (an automated decision) THEN system SHALL increment `reimbursement_status_transitions_total` (Counter, labeled by `from`, `to`) in the service where that write happened — `publisher`'s initial row-creation writes (`insert_pending`, `insert_human_review`) are not counted, since there is no prior status to transition from.

**Independent Test**: Drive one item through `pending` → an automated decision (via `reimbursement`) and a second item through `pending` → `human-review` → a `PUT` resolution (via `api`); scrape both services' `/metrics` and confirm `reimbursement_status_count` reflects current DB state, `reimbursement_status_transitions_total` shows one transition recorded in each service, and `reimbursement_review_wait_seconds` recorded exactly one observation for the reviewed item.

---

### P1: Decision Pipeline Latency Metrics ⭐ MVP

**User Story**: As an engineer investigating a slow decision pipeline, I want separate latency signals for the in-process decision call, the full request-to-decision path including queue wait, and each graph node, so I can localize where time is actually going.

**Why P1**: Named explicitly in the approved table (metrics 6-8); grilling session decisions 3 and 9 are explicit that these measure different things and must never share buckets or be collapsed into one histogram.

**Acceptance Criteria**:

1. WHEN `reimbursement`'s `agent.decide()` call completes (success or failure) THEN system SHALL record its wall-clock duration in `reimbursement_agent_decision_duration_seconds` (Histogram, no labels).
2. WHEN a reimbursement request reaches a terminal automated decision (`auto-approved`, `auto-rejected`, or the mandatory-human-review handoff on the `>2000` rule) THEN system SHALL record the elapsed time from that request's creation to that decision in `reimbursement_time_to_decision_seconds` (Histogram, no labels), inclusive of Kafka queue wait, sharing no bucket configuration with AC1's histogram.
3. WHEN any node in the LangGraph decision graph (`extract_fields`, `validate`, `apply_policies`, `analysis`, `apply_agent_decision`) completes THEN system SHALL record its duration in `reimbursement_agent_node_duration_seconds` (Histogram, labeled by `node`, and additionally by `model` only for the `extract_fields` and `analysis` nodes — `model` is omitted for the other three).

**Independent Test**: Trigger one end-to-end automated decision; scrape `reimbursement`'s `/metrics` and confirm all three histograms recorded exactly one observation for that run, `agent_node_duration_seconds` shows one observation per node actually executed, and only `extract_fields`/`analysis` observations carry a non-empty `model` label.

---

### P1: Decision Outcome Metrics & Rule-ID Enum ⭐ MVP

**User Story**: As an engineer, I want to see which deterministic rule or LLM outcome actually produced each decision, and to distinguish pipeline failures from legitimate high-value escalations, so a rising escalation rate tells me whether the AI pipeline is breaking or business volume is shifting.

**Why P1**: Named explicitly in the approved table (metrics 9-11); grilling session decision 7 is explicit that failure-driven escalation must never be confused with the `value > 2000` rule's own escalation.

**Acceptance Criteria**:

1. WHEN `apply_policies.py` evaluates a reimbursement THEN system SHALL identify which deterministic rule fired (if any) using a new fixed 3-value rule-ID enum (Assumption 1), replacing today's unlabeled `if`/`elif` branching.
2. WHEN a deterministic rule fires in `apply_policies.py` THEN system SHALL increment `reimbursement_policy_rule_triggered_total` (Counter, labeled by `rule`) with that rule's enum value.
3. WHEN the decision pipeline invokes a model call (`extract_fields` or `analysis`) THEN system SHALL increment `reimbursement_agent_llm_calls_total` (Counter, labeled by `model`, `outcome`) with `outcome` set to `success` or `failure`.
4. WHEN a decision is force-escalated to `human-review` specifically because the LLM/agent pipeline failed and retries were exhausted — not because the `value > 2000` rule fired — THEN system SHALL increment `reimbursement_decision_failure_escalations_total` (Counter, no labels), kept structurally separate from AC2's rule-triggered counter.

**Independent Test**: Trigger one reimbursement through each of the 3 deterministic rules and confirm `reimbursement_policy_rule_triggered_total` shows exactly one increment per rule's enum value; separately, force an LLM/agent failure through retry exhaustion and confirm it increments `reimbursement_decision_failure_escalations_total` but does **not** increment the `high-value-human-review` rule label.

---

### P1: Message Lifecycle Metrics ⭐ MVP

**User Story**: As an SRE, I want to see Kafka message throughput, retry pressure, and dropped duplicates on both `publisher` and `reimbursement`, so a backlog or a silent duplicate-drop anomaly is visible instead of only discoverable by grepping logs.

**Why P1**: Named explicitly in the approved table (metrics 12-16); metric 16 directly closes `docs/RISKS.md` R-004, an already-documented, previously un-implemented gap.

**Acceptance Criteria**:

1. WHEN `reimbursement`'s consumer consumes a message from the `Reimbursement` topic THEN system SHALL increment `reimbursement_messages_consumed_total` (Counter, labeled by `topic`).
2. WHEN `reimbursement`'s consumer requeues a message after a transient processing failure THEN system SHALL increment `reimbursement_messages_requeued_total` (Counter, labeled by `topic`).
3. WHEN `publisher`'s consumer consumes a message from the `Request` topic THEN system SHALL increment `publisher_messages_consumed_total` (Counter, labeled by `topic`).
4. WHEN `publisher`'s consumer requeues a message after a transient processing failure THEN system SHALL increment `publisher_messages_requeued_total` (Counter, labeled by `topic`).
5. WHEN `publisher` drops a duplicate message via the `reimbursement_request_submitter_key` unique-constraint short-circuit THEN system SHALL increment `publisher_duplicate_dropped_total` (Counter, no labels).

**Independent Test**: Replay one already-processed message through `publisher` (triggering the duplicate-drop path) and force one transient failure in each of `publisher` and `reimbursement`; scrape both `/metrics` endpoints and confirm each of the 5 counters incremented exactly once for its corresponding event.

---

### P1: Metrics Documentation & Concerns Update ⭐ MVP

**User Story**: As a future contributor or on-call engineer, I want a single document listing every shipped metric and its meaning, and the project's known-gaps document to record what this MVP deliberately left open, so I don't have to reverse-engineer either from source.

**Why P1**: Explicitly listed as a deliverable in `grilling-session.md`; without it, the feature ships 16 metrics with no discoverable reference and two accepted gaps (no auth, 3 scrape targets) with no durable record.

**Acceptance Criteria**:

1. WHEN this feature ships THEN system SHALL add `docs/METRICS.md` documenting all 16 metrics from the Metric Catalog (name, type, service, labels, description) plus implementation notes worth keeping for future readers (the rule-ID enum values, the two-histogram decision-latency split and why, why 3 separate scrape targets rather than one).
2. WHEN this feature ships THEN system SHALL add two follow-up entries to `docs/codebase/CONCERNS.md`: (a) no authentication on any of the three `/metrics` endpoints — extending the existing "No authentication on the public API" entry's scope rather than duplicating it as a new entry; (b) three new Prometheus scrape targets are needed in k3s manifests, not addressed by this feature's own deployment config.
3. WHEN this feature ships THEN system SHALL NOT add DB connection-pool metrics or a centralized/federated metrics endpoint — both remain explicit non-goals (Out of Scope).

**Independent Test**: Read `docs/METRICS.md` standalone and confirm all 16 metrics are listed with matching name/type/service/labels against the Metric Catalog; read `docs/codebase/CONCERNS.md` and confirm both new follow-ups are present and distinguishable from the pre-existing "No authentication on the public API" entry.

---

## Edge Cases

- WHEN a metric object is constructed more than once against the same process's default registry (e.g. module re-import during tests) THEN system SHALL avoid duplicate-registration errors via a "construct once at module load, import the module-level instance" pattern — no per-request or per-call construction.
- WHEN `api` runs as a single-worker uvicorn process (its current and only supported mode) THEN system SHALL rely on the default in-process `prometheus_client` registry with no multiprocess-mode configuration — this is a standing constraint, not something this feature's code must detect or guard against.
- WHEN a FastAPI route has multiple dynamic path segments (e.g. a future `/a/{x}/b/{y}`) THEN system SHALL still resolve the `path` label to that route's single registered template, keeping cardinality equal to the app's route count regardless of segment count.
- WHEN the DB query backing `reimbursement_status_count` fails at scrape time THEN system SHALL skip that gauge for the scrape and log a warning, per Assumption 6, rather than failing the entire `/metrics` response.
- WHEN a consumed Kafka message on either service carries no correlation/traceability metadata relevant to a metric (e.g. `topic` is always present on every message envelope) THEN system SHALL never omit a metric's required label — `topic` is always derivable from which topic the consumer polled, not from message content.
- WHEN `reimbursement_decision_failure_escalations_total` and `reimbursement_policy_rule_triggered_total`'s `high-value-human-review` value could both describe a row landing in `human-review` THEN system SHALL increment at most one of the two for a given decision — a rule-based escalation (`value > 2000`) increments only the rule counter; a failure-driven escalation increments only the failure counter, never both.

---

## Requirement Traceability

Each requirement gets a unique ID for tracking across design, tasks, and validation.

| Requirement ID | Story | Phase | Status |
| --------------- | ----- | ----- | ------ |
| METRICS-01 | P1: Metrics Endpoint Infrastructure (AC1: `api` exposes `GET /metrics`, no auth) | Design | Pending |
| METRICS-02 | P1: Metrics Endpoint Infrastructure (AC2: `publisher` exposes its own `/metrics` port, no auth) | Design | Pending |
| METRICS-03 | P1: Metrics Endpoint Infrastructure (AC3: `reimbursement` exposes its own `/metrics` port, no auth) | Design | Pending |
| METRICS-04 | P1: Metrics Endpoint Infrastructure (AC4: no cross-process aggregation/federation) | Design | Pending |
| METRICS-05 | P1: Metrics Endpoint Infrastructure (AC5: Counters constructed without `_total` suffix) | Design | Pending |
| METRICS-06 | P1: API HTTP RED Metrics (AC1: `api_http_requests_total` increments per request) | Design | Pending |
| METRICS-07 | P1: API HTTP RED Metrics (AC2: `api_http_request_duration_seconds` records duration) | Design | Pending |
| METRICS-08 | P1: API HTTP RED Metrics (AC3: `path` label uses route template, never resolved URL) | Design | Pending |
| METRICS-09 | P1: API HTTP RED Metrics (AC4: 404 `path` label is a fixed literal, not raw path) | Design | Pending |
| METRICS-10 | P1: Reimbursement Status & Review Observability (AC1: `reimbursement_status_count` refreshed per scrape) | Design | Pending |
| METRICS-11 | P1: Reimbursement Status & Review Observability (AC2: `reimbursement_review_wait_seconds` on PUT resolution) | Design | Pending |
| METRICS-12 | P1: Reimbursement Status & Review Observability (AC3: `reimbursement_status_transitions_total` in api + reimbursement) | Design | Pending |
| METRICS-13 | P1: Decision Pipeline Latency Metrics (AC1: `reimbursement_agent_decision_duration_seconds`) | Design | Pending |
| METRICS-14 | P1: Decision Pipeline Latency Metrics (AC2: `reimbursement_time_to_decision_seconds`, distinct buckets from AC1) | Design | Pending |
| METRICS-15 | P1: Decision Pipeline Latency Metrics (AC3: `reimbursement_agent_node_duration_seconds`, `model` only on 2 nodes) | Design | Pending |
| METRICS-16 | P1: Decision Outcome Metrics & Rule-ID Enum (AC1: rule-ID enum added to `apply_policies.py`) | Design | Pending |
| METRICS-17 | P1: Decision Outcome Metrics & Rule-ID Enum (AC2: `reimbursement_policy_rule_triggered_total`) | Design | Pending |
| METRICS-18 | P1: Decision Outcome Metrics & Rule-ID Enum (AC3: `reimbursement_agent_llm_calls_total`) | Design | Pending |
| METRICS-19 | P1: Decision Outcome Metrics & Rule-ID Enum (AC4: `reimbursement_decision_failure_escalations_total`, structurally distinct) | Design | Pending |
| METRICS-20 | P1: Message Lifecycle Metrics (AC1: `reimbursement_messages_consumed_total`) | Design | Pending |
| METRICS-21 | P1: Message Lifecycle Metrics (AC2: `reimbursement_messages_requeued_total`) | Design | Pending |
| METRICS-22 | P1: Message Lifecycle Metrics (AC3: `publisher_messages_consumed_total`) | Design | Pending |
| METRICS-23 | P1: Message Lifecycle Metrics (AC4: `publisher_messages_requeued_total`) | Design | Pending |
| METRICS-24 | P1: Message Lifecycle Metrics (AC5: `publisher_duplicate_dropped_total`, closes R-004) | Design | Pending |
| METRICS-25 | P1: Metrics Documentation & Concerns Update (AC1: `docs/METRICS.md` added) | Design | Pending |
| METRICS-26 | P1: Metrics Documentation & Concerns Update (AC2: `docs/codebase/CONCERNS.md` two follow-ups added) | Design | Pending |
| METRICS-27 | P1: Metrics Documentation & Concerns Update (AC3: no DB pool metrics / no federated endpoint) | Design | Pending |

**ID format:** `METRICS-[NUMBER]` — single prefix, sequential across all stories (all P1, one MVP scope — no sub-domain split was warranted).

**Status values:** Pending → In Design → In Tasks → Implementing → Verified

**Coverage:** 27 total, 0 mapped to tasks, 27 unmapped ⚠️ (Tasks phase not yet run)

---

## Success Criteria

How we know the feature is successful:

- [ ] All three services (`api`, `publisher`, `reimbursement`) expose independently scrapeable Prometheus-format `/metrics` endpoints, no auth required.
- [ ] All 16 approved metrics are present and populated with real observations under normal operation, verifiable by exercising each corresponding code path and scraping the relevant endpoint — no metric requires re-running or re-deriving from logs to confirm.
- [ ] `apply_policies.py`'s three deterministic rules are labeled by the new fixed rule-ID enum; `reimbursement_policy_rule_triggered_total` shows one label value per rule with no free-text leakage.
- [ ] No HTTP metric's `path` label ever contains a resolved URL value (a UUID or other real id) — confirmed by exercising a parameterized route and inspecting the scraped label.
- [ ] `docs/METRICS.md` exists and matches the Metric Catalog exactly; `docs/codebase/CONCERNS.md` carries both new follow-up entries.
