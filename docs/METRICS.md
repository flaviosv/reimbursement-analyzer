# Metrics

Prometheus metrics for `api`, `publisher`, and `reimbursement`. Each service
exposes its own `/metrics` endpoint in Prometheus text exposition format,
with no authentication — see "Why 3 scrape targets" below for the
architecture rationale, and `docs/codebase/CONCERNS.md` for the two
accepted MVP gaps this feature deliberately leaves open.

## Metric Catalog

16 metrics, exactly as approved in `.specs/features/RA-3-prometheus-metrics-mvp/spec.md`'s
Metric Catalog — no renames, no additions, no omissions.

| # | Metric | Type | Service | Labels | Description | Port/Endpoint |
|---|--------|------|---------|--------|--------------|----------------|
| 1 | `api_http_requests_total` | Counter | api | method, path, status_code | Every HTTP request handled by the API, including error responses | `GET /metrics` on `api`'s main HTTP port |
| 2 | `api_http_request_duration_seconds` | Histogram | api | method, path | HTTP request handling latency | `GET /metrics` on `api`'s main HTTP port |
| 3 | `reimbursement_status_count` | Gauge | api | status | Current number of reimbursement requests in each status, refreshed each scrape via a DB query | `GET /metrics` on `api`'s main HTTP port |
| 4 | `reimbursement_review_wait_seconds` | Histogram | api | — | Time an item spent in `human-review` before a reviewer resolved it | `GET /metrics` on `api`'s main HTTP port |
| 5 | `reimbursement_status_transitions_total` | Counter | api + reimbursement | from, to | Every observed status transition, wherever it happens | `api`'s main HTTP port; `reimbursement`'s `METRICS_PORT` (default `9102`) |
| 6 | `reimbursement_agent_decision_duration_seconds` | Histogram | reimbursement | — | Wall-clock time of the full `agent.decide()` graph run | `reimbursement`'s `METRICS_PORT` (default `9102`) |
| 7 | `reimbursement_time_to_decision_seconds` | Histogram | reimbursement | — | End-to-end time from request creation to a terminal automated decision (includes Kafka queue wait) | `reimbursement`'s `METRICS_PORT` (default `9102`) |
| 8 | `reimbursement_agent_node_duration_seconds` | Histogram | reimbursement | node, model | Per-node latency inside the decision graph; `model` populated only for `extract_fields`/`analysis` | `reimbursement`'s `METRICS_PORT` (default `9102`) |
| 9 | `reimbursement_agent_llm_calls_total` | Counter | reimbursement | model, outcome | LLM invocations, by model and success/failure | `reimbursement`'s `METRICS_PORT` (default `9102`) |
| 10 | `reimbursement_policy_rule_triggered_total` | Counter | reimbursement | rule | Which deterministic policy rule fired | `reimbursement`'s `METRICS_PORT` (default `9102`) |
| 11 | `reimbursement_decision_failure_escalations_total` | Counter | reimbursement | — | Decisions force-escalated to human-review because a single decision-pipeline attempt failed — never retried | `reimbursement`'s `METRICS_PORT` (default `9102`) |
| 12 | `reimbursement_messages_consumed_total` | Counter | reimbursement | topic | Kafka messages consumed | `reimbursement`'s `METRICS_PORT` (default `9102`) |
| 13 | `reimbursement_messages_requeued_total` | Counter | reimbursement | topic | Messages requeued after a transient processing failure | `reimbursement`'s `METRICS_PORT` (default `9102`) |
| 14 | `publisher_messages_consumed_total` | Counter | publisher | topic | Kafka messages consumed | `publisher`'s `METRICS_PORT` (default `9101`) |
| 15 | `publisher_messages_requeued_total` | Counter | publisher | topic | Messages requeued after a transient processing failure | `publisher`'s `METRICS_PORT` (default `9101`) |
| 16 | `publisher_duplicate_dropped_total` | Counter | publisher | — | Duplicate messages dropped by idempotency handling (closes `docs/RISKS.md` R-004) | `publisher`'s `METRICS_PORT` (default `9101`) |

Type coverage: 1 Gauge, 5 Histograms, 10 Counters.

## Rule-ID Enum (`PolicyRule`)

`packages/reimbursement/src/reimbursement/agent/nodes/apply_policies.py` labels
`reimbursement_policy_rule_triggered_total` with a fixed 3-value enum, one
value per deterministic threshold:

| Enum value | Threshold | Outcome |
|------------|-----------|---------|
| `stale-receipt-reject` | Receipt is older than 90 days (checked against the server clock, not the client-supplied `submitted_at`) | `auto-rejected` |
| `low-value-auto-approve` | Resolved value is `<= 200` | `auto-approved` |
| `high-value-human-review` | Resolved value is `> 2000` | `human-review` |

A value strictly between `200` and `2000` fires no deterministic rule and
falls through to the `analysis` node's LLM-as-judge step instead — no rule
label is incremented for that path.

**Semantic:** `reimbursement_policy_rule_triggered_total` counts rule
*evaluation* at decision time, not confirmed DB *persistence* of the outcome
(contrast with `reimbursement_status_transitions_total`, which only increments
when the write actually persists a row transition). If a rule fires but the
subsequent write to persist the decision fails, the rule counter reflects the
evaluated rule, and the transitions counter reflects the failed write. The two
counters should correlate 1:1 under normal operation, but may diverge briefly
on DB failures; operators should not treat divergence as a sign of the rule
itself failing, but rather as evidence of DB write failures during that
incident.

## The Decision-Latency Histogram Split

Three separate histograms measure three different things in the decision
pipeline, and deliberately share no bucket configuration with each other so
a scrape can never conflate them:

- **`reimbursement_agent_decision_duration_seconds`** — the in-process
  wall-clock time of one `agent.decide()` graph run only. Buckets
  `(0.1, 0.5, 1, 2, 5, 10, 20, 30, 60, 120)` (sub-second to 2 minutes),
  bounded above by `AIConfig.timeout_seconds`'s 30s-per-call default times
  up to 2 sequential LLM calls.
- **`reimbursement_time_to_decision_seconds`** — end-to-end time from
  request creation to a terminal automated decision, including Kafka queue
  wait. Buckets `(1, 5, 15, 30, 60, 300, 900, 1800, 3600, 21600)` (1 second
  to 6 hours) — a materially wider range than the in-process histogram
  above, since it includes time the request spent waiting outside any
  process.
- **`reimbursement_agent_node_duration_seconds`** — per-node latency inside
  the graph (`extract_fields`, `validate`, `apply_policies`, `analysis`,
  `apply_agent_decision`), labeled by `node` and, only for `extract_fields`/
  `analysis`, by `model`. Buckets `(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)`
  — finer-grained than the whole-graph histogram, since a single node is
  always faster than the full run.

A fourth histogram, **`reimbursement_review_wait_seconds`** (owned by
`api`), measures a different axis entirely — human review queue time, not
automated pipeline latency — on a minutes-to-days scale: buckets
`(60, 300, 900, 3600, 14400, 43200, 86400, 259200, 604800)` (1 minute to 1
week). It shares no bucket configuration with any of the three histograms
above.

## Why 3 Independent Scrape Targets

`api`, `publisher`, and `reimbursement` are three separately deployable
processes; each exposes its own `/metrics` endpoint backed by that
process's own default `prometheus_client` registry, with no shared
registry, aggregation, or federation layer between them. In-process
counters constructed in one process are invisible to another process's
`/metrics` endpoint — there is no mechanism to bridge them short of
building a federation layer, which this MVP deliberately does not build
(grilling session decision 8). Prometheus scrapes all 3 targets
independently instead.

## Counter Naming Rule

Every `Counter(...)` in code is constructed **without** the `_total`
suffix (e.g. `Counter("reimbursement_messages_consumed", ...)`) —
`prometheus_client` appends `_total` automatically at exposition time. The
names in the Metric Catalog table above already show the correct exposed
form; the underlying `Counter(...)` constructor calls in
`shared/metrics.py`, `api/metrics.py`, `publisher/metrics.py`, and
`reimbursement/metrics.py` must never repeat the suffix.

## Known Gaps

Both accepted MVP gaps this feature deliberately leaves open — no
authentication on any `/metrics` endpoint, and the 3 new scrape targets
needing k3s manifest changes — are tracked in
[`docs/codebase/CONCERNS.md`](codebase/CONCERNS.md) (Security Considerations
and Missing Critical Features, respectively), not re-documented here.
