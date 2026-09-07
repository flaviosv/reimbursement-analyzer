# Grilling Session: RA-3 — Prometheus Metrics MVP

## Goal

Add a `/metrics` (Prometheus exposition format) endpoint to the `api` service, and equivalent metrics ports to `publisher` and `reimbursement`, with no auth for this MVP. Identify the full candidate metric set (grounded in the actual codebase, not guessed), converge with the user on a final approved list, then implement.

## Codebase research (via subagent, grounded facts used throughout this session)

- **Status enum** (Postgres `CHECK` constraint, `packages/api/src/api/migrations/0001.create-reimbursement.sql`): `pending`, `auto-approved`, `auto-rejected`, `human-review`, `human-approved`, `human-rejected` — six states. Current status lives on the `reimbursement` table (no full transition-history table — only current status + `updated_at`). A separate append-only `human_review` table (`0002.create-human-review.sql`) records each reviewer decision (`status`, `reviewed_by`, `reason`, `created_at`).
- **Status writes** happen in exactly three places via `packages/shared/src/shared/reimbursement/repository.py`: `insert_pending`/`insert_human_review` (from `publisher.processing`), `update_decision` (from the decision graph's `apply_decision`), and `approve`/`reject` (from `api`'s `PUT` route). Each is a single atomic UPDATE — never multiple statuses in one call.
- **LangGraph decision pipeline** (`packages/reimbursement/src/reimbursement/agent/agent.py`, `_wire()`): 5 nodes — `extract_fields` → `validate` → (conditional) `apply_policies` → (conditional) `analysis` → `apply_agent_decision` → END. Entry point `agent.decide()`. No timing instrumentation exists anywhere today. LangFuse tracing is wired at the graph-invocation boundary.
- **3 deterministic rules** in `apply_policies.py`, inline `if/elif`, no ID today: age > 90 days → `auto-rejected`; value ≤ 200 → `auto-approved`; value > 2000 → `human-review`.
- **Model name** is known per-decision in memory (`AgentModelsConfig`, logged) but never persisted or counted. No token/cost tracking exists.
- **Human review**: no dedicated queue table — a row simply has `status = 'human-review'`. No existing time-in-state computation.
- **Kafka**: two topics (`Request`, `Reimbursement`), `MAX_RETRY = 3` in both `publisher` and `reimbursement` consumers; retry-exhausted rows escalate to `human-review` in Postgres (the DB row is the de-facto dead-letter mechanism). `docs/RISKS.md` R-004 already proposes `publisher_duplicate_dropped_total` as a named, un-implemented gap, and sets the `<service>_<event>_total` naming precedent.
- **API layer**: FastAPI, app assembled in `packages/api/src/api/main.py`, existing `@app.get("/health", ...)` pattern to follow for wiring `/metrics`. Zero references to `prometheus` anywhere in the repo today.
- **Process topology**: `api`, `publisher`, `reimbursement` are three separately deployable processes on k3s, each with its own Dockerfile. `api` runs single-worker uvicorn (no `--workers`), horizontal scaling is per-pod — so **no Prometheus multiprocess mode needed**.
- `docs/codebase/ARCHITECTURE.md` states outright there is no metrics infrastructure yet ("no OpenTelemetry, no metrics" — log-based observability only).

## Key decisions

1. **Gauges vs. Counters**: current-state population (e.g. "items in X status") → **Gauge**; cumulative transition/event counts → **Counter**. Standard Prometheus counter-reset-on-restart semantics accepted (no persistence needed).
2. **Label cardinality rule (hard constraint)**: labels restricted to small fixed enums — `status` (6 values), transition `from`/`to`, a new rule-ID enum (3 values), `node` (5 values), `model` (small fixed config set), `topic`, HTTP `method`/route-template `path`, `outcome`. **Never**: the existing free-text `decision_reason` field, `request_id`/`correlation_id`/`user_id`, or reviewer email (`reviewed_by` — PII, excluded regardless). The `path` label on HTTP metrics must use the FastAPI **route template** (e.g. `/reimbursements/{id}`), never the resolved URL with a real ID in it.
3. **Decision-latency histograms — ship two, not one**: (a) `agent_decision_duration_seconds` — wall-clock time of the `agent.decide()` call itself (in-process, precise); (b) `time_to_decision_seconds` — end-to-end from request creation to a terminal automated decision, including Kafka queue wait. These measure different things and must not share buckets.
4. **Time-in-human-review**: its own histogram (`review_wait_seconds`), with much wider buckets than the automated-path histograms (minutes to days) — measures whether the human review queue is backing up, which the plain Gauge count alone can't show.
5. **Rule-ID labeling**: add a small fixed enum to the 3 deterministic policy rules in `apply_policies.py` (none exists today) so a Counter can show which rule fired.
6. **LLM-call Counter**: labeled by `model` + `outcome` (success/failure).
7. **Failure-driven escalation gets its own Counter**, separate from the rule-based `value > 2000` escalation — a rising rate here signals "the AI pipeline is breaking," not "more high-value requests."
8. **Architecture: 3 separate `/metrics` endpoints**, not one centralized endpoint. `publisher` and `reimbursement` have no HTTP server today — each needs a small metrics HTTP port added (mirroring their existing `configure_logging()` startup call), since in-process counters in one process are invisible to another process's endpoint. This is a real deployment-shape change: 3 scrape targets instead of 1.
9. **Per-node agent latency**: one histogram (`agent_node_duration_seconds`) labeled by `node` (5 fixed values), not 5 separate histogram objects. The `model` label is added to this same histogram (populated only for the two LLM-calling nodes, `extract_fields`/`analysis`) rather than creating a fully separate LLM-call-duration histogram — avoids ~95% overlap with the per-node histogram for those two nodes today.
10. **Full message-lifecycle counters on both `publisher` and `reimbursement`**: messages consumed, messages requeued (transient-failure retry), each labeled by `topic`.
11. **HTTP-level RED metrics on `api`**: `api_http_requests_total` (labels: method, path, status_code) and `api_http_request_duration_seconds` (labels: method, path) — traffic/error visibility on the API itself, separate from reimbursement decision outcomes.
12. **Explicit non-goals**: no auth on any `/metrics` endpoint in this MVP (tracked as a `docs/codebase/CONCERNS.md` follow-up, alongside the 3-scrape-target deployment note); no DB connection-pool metrics (belongs to a dedicated Postgres exporter, not app code — would duplicate infrastructure one layer down).
13. **Naming verified against official `prometheus_client` Python docs (Context7)**: the client auto-strips/re-adds the `_total` suffix on exposition, so Counters are constructed in code *without* `_total` in the name (e.g. `Counter('reimbursement_messages_consumed', ...)`), and the client appends it automatically. All documented metric names below already reflect the correct exposed form.

## Final approved metric list (16 metrics)

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
| 11 | `reimbursement_decision_failure_escalations_total` | Counter | reimbursement | — | Decisions force-escalated to human-review after the LLM/agent pipeline failed and retries were exhausted |
| 12 | `reimbursement_messages_consumed_total` | Counter | reimbursement | topic | Kafka messages consumed |
| 13 | `reimbursement_messages_requeued_total` | Counter | reimbursement | topic | Messages requeued after a transient processing failure |
| 14 | `publisher_messages_consumed_total` | Counter | publisher | topic | Kafka messages consumed |
| 15 | `publisher_messages_requeued_total` | Counter | publisher | topic | Messages requeued after a transient processing failure |
| 16 | `publisher_duplicate_dropped_total` | Counter | publisher | — | Duplicate messages dropped by idempotency handling (closes `docs/RISKS.md` R-004) |

Type coverage: 1 Gauge, 5 Histograms, 10 Counters.

## Deliverables expected from implementation

- `docs/METRICS.md` documenting the shipped metrics (table above, plus any implementation notes worth keeping for future readers).
- `/metrics` endpoint on `api`; equivalent metrics ports on `publisher` and `reimbursement`.
- `docs/codebase/CONCERNS.md` updated with two follow-ups: (a) no auth on any `/metrics` endpoint yet, (b) 3 new scrape targets needed in k3s manifests (out of scope for this feature's own deployment config unless trivial).
- Rule-ID enum added to `apply_policies.py`'s 3 deterministic rules.

## User approval

User approved this exact final table and all listed decisions before implementation started (no further pauses expected — `human_review=no` for the rest of the pipeline).
