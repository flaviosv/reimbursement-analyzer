# RA-3 Prometheus Metrics MVP Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user — do not proceed without it.**

---

**Design**: `.specs/features/RA-3-prometheus-metrics-mvp/design.md`
**Status**: Draft

---

## Test Coverage Matrix

> Generated from codebase, project guidelines (docs/codebase/TESTING.md), and spec — confirmed before Execute.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------- | -------------------- | -------------------------------------------- | ----------------------------------------- |
| Unit (config, metrics module, fakes) | Unit | All branches; 1:1 to spec ACs; all listed edge cases | `packages/*/tests/test_*.py` | `uv run pytest -m "not integration and not e2e"` |
| Route / HTTP endpoint | Route-level (TestClient) | All routes in scope: happy path + every listed edge case + error paths | `packages/api/tests/**/test_route.py` | `uv run pytest -m "not integration and not e2e"` |
| Integration (DB queries, Kafka consumption) | Integration | Key query paths + error handling | `packages/*/tests/test_integration.py` | `uv run pytest` |
| DB schema / migration | Integration (real Postgres) | Constraint-focused | `packages/api/tests/test_*_schema.py` | `uv run pytest` |
| Entity / config / schema | none | — (build gate only) | — | build gate only |

**Provenance**: Guidelines from `docs/codebase/TESTING.md`; `Describe*/it_*` naming convention; `pytest` configured with `python_classes`/`python_functions`.

## Gate Check Commands

> Generated from codebase — confirm before Execute.

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | After changes with no Kafka dependency | `uv run pytest -m "not integration and not e2e"` |
| Full | Before considering a task/PR done | `uv run pytest` |
| E2E (optional) | After changes to a real-decision code path | `uv run pytest -m e2e` — requires `docker compose up -d` + real `GROQ_API_KEY` |

---

## Execution Plan

Phases are ordered and run sequentially — each phase completes before the next begins, and tasks within a phase execute in order.

### Phase 1: Dependency & Cross-Service Foundation

Tasks that establish the shared infrastructure all services build on.

```
T1 → T2 → T3 → T4
```

### Phase 2: API Metrics

Implement the HTTP RED metrics and status/review observability in the `api` service.

```
T5 → T6 → T7 → T8
```

### Phase 3: Publisher Metrics

Instrument the Kafka consumption, requeue, and duplicate-drop paths in `publisher`.

```
T9 → T10 → T11
```

### Phase 4: Reimbursement Metrics & Decision Pipeline

Wire metrics throughout the decision graph, validation loop, and Kafka consumption in `reimbursement`.

```
T12 → T13 → T14 → T15 → T16 → T17
```

### Phase 5: Documentation & Final Integration

Create the delivered documentation and verify all components wire together.

```
T18 → T19
```

---

## Task Breakdown

### T1: Add `prometheus-client` dependency to `packages/shared/pyproject.toml`

**What**: Add `prometheus-client>=0.24.1` to the shared package's dependencies, making it available transitively to all three services (api, publisher, reimbursement).

**Where**: `packages/shared/pyproject.toml`

**Depends on**: None

**Reuses**: Existing `ecs-logging` precedent (RA-1) — same pattern of a cross-service library declared once in shared.

**Requirement**: METRICS-05 (Counter naming rule depends on prometheus_client version being consistent across all services)

**Tools**:
- MCP: None (direct file edit)
- Skill: None

**Done when**:
- [ ] `prometheus-client>=0.24.1` is added to `[project] dependencies` in `packages/shared/pyproject.toml`
- [ ] No TypeErrors or import errors when `import prometheus_client` runs
- [ ] Build gate passes: `uv run pytest -m "not integration and not e2e"` completes without import errors

**Tests**: None (dependency addition, verified by build gate)

**Gate**: Quick

**Commit**: `chore(shared): add prometheus-client>=0.24.1 dependency`

---

### T2: Create `packages/shared/src/shared/metrics.py` with cross-service metric and server starter

**What**: Create the shared metrics module containing the one metric owned by two services (`reimbursement_status_transitions_total`), plus the `start_metrics_server()` helper both `publisher` and `reimbursement` call at startup. Module-level Counter constructed once at import time (Edge Cases rule).

**Where**: `packages/shared/src/shared/metrics.py` (new file)

**Depends on**: T1

**Reuses**: `prometheus_client.Counter`, `prometheus_client.start_http_server` from official docs; design.md Components section.

**Requirement**: METRICS-04, METRICS-05, METRICS-12, METRICS-20, METRICS-21, METRICS-22, METRICS-23 (all message-lifecycle metrics depend on this foundation; the cross-service transition counter is defined here)

**Tools**:
- MCP: None
- Skill: None

**Done when**:
- [ ] `reimbursement_status_transitions_total: Counter` constructed with `labelnames=["from", "to"]` (Counter name without `_total` suffix per spec naming rule)
- [ ] `start_metrics_server(port: int) -> None` function logs one `info` line and raises on bind failure
- [ ] Module exports both symbols at root level
- [ ] All call sites using `.labels(from_value, to_value)` positional order (not keyword, since `from` is reserved) — documented inline
- [ ] No import errors: `python3 -c "from shared.metrics import reimbursement_status_transitions_total, start_metrics_server"` succeeds
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`

**Tests**: Unit — create `packages/shared/tests/test_metrics.py` with tests for:
  - `reimbursement_status_transitions_total` is a Counter with correct labelnames
  - `.labels(value1, value2)` accepts positional arguments in `from`/`to` order
  - `.inc()` increments the counter
  - `start_metrics_server()` logs correct message and doesn't raise on a free port
  - `start_metrics_server()` raises (e.g., `OSError`) on port already in use (e.g., call twice with same port)

**Gate**: Quick

**Commit**: `feat(shared): add cross-service metrics module with status_transitions_total and start_metrics_server()`

---

### T3: Add `METRICS_PORT` configuration to `packages/publisher/src/publisher/config.py`

**What**: Add `metrics_port: int` field to `PublisherConfig` class, read from `int(os.getenv("METRICS_PORT", "9101"))` at load time. Single-service configuration (only publisher reads it), following AD-019/AD-023.

**Where**: `packages/publisher/src/publisher/config.py` (modify)

**Depends on**: T1

**Reuses**: Existing `PublisherConfig` class pattern; same pattern as `item_concurrency` (single-service tuning).

**Requirement**: METRICS-02 (AC2: publisher exposes metrics on configurable port)

**Tools**:
- MCP: None
- Skill: None

**Done when**:
- [ ] `PublisherConfig` class has new field `metrics_port: int = 9101`
- [ ] Field is read from `os.getenv("METRICS_PORT", "9101")` with `int()` cast
- [ ] `load_publisher_config()` returns config with this field set
- [ ] Type hints are correct (int, not str)
- [ ] No import errors
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`

**Tests**: Unit — create or update `packages/publisher/tests/test_config.py`:
  - `load_publisher_config()` defaults `metrics_port` to 9101 when env var unset
  - `load_publisher_config()` reads `METRICS_PORT=9200` and sets `metrics_port=9200`
  - Invalid `METRICS_PORT="not_a_number"` raises `ValueError` at config load time

**Gate**: Quick

**Commit**: `feat(publisher): add metrics_port config field`

---

### T4: Add `METRICS_PORT` configuration to `packages/reimbursement/src/reimbursement/config.py`

**What**: Add `metrics_port: int` field to `AgentConfig` class, read from `int(os.getenv("METRICS_PORT", "9102"))` at load time. Single-service configuration (only reimbursement reads it), following AD-019/AD-023. Identical pattern to T3.

**Where**: `packages/reimbursement/src/reimbursement/config.py` (modify)

**Depends on**: T1

**Reuses**: T3's pattern; existing `AgentConfig` class.

**Requirement**: METRICS-03 (AC3: reimbursement exposes metrics on configurable port)

**Tools**:
- MCP: None
- Skill: None

**Done when**:
- [ ] `AgentConfig` class has new field `metrics_port: int = 9102`
- [ ] Field is read from `os.getenv("METRICS_PORT", "9102")` with `int()` cast
- [ ] `load_agent_config()` returns config with this field set
- [ ] Type hints are correct (int, not str)
- [ ] No import errors
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`

**Tests**: Unit — create or update `packages/reimbursement/tests/test_config.py`:
  - `load_agent_config()` defaults `metrics_port` to 9102 when env var unset
  - `load_agent_config()` reads `METRICS_PORT=9300` and sets `metrics_port=9300`
  - Invalid `METRICS_PORT="not_a_number"` raises `ValueError` at config load time

**Gate**: Quick

**Commit**: `feat(reimbursement): add metrics_port config field`

---

### T5: Create `packages/api/src/api/metrics.py` with API-specific metrics

**What**: Create the API metrics module containing all metrics owned by `api`: `api_http_requests_total`, `api_http_request_duration_seconds`, `reimbursement_status_count`, `reimbursement_review_wait_seconds`, plus the `UNMATCHED_PATH_LABEL` constant, `ALL_STATUSES` tuple, and `refresh_status_gauge()` async function. All metrics constructed at module scope (Edge Cases rule).

**Where**: `packages/api/src/api/metrics.py` (new file)

**Depends on**: T2 (shared.metrics exists; imports from it)

**Reuses**: `prometheus_client.Counter`, `prometheus_client.Histogram`, `prometheus_client.Gauge` from official docs; design.md bucket specifications.

**Requirement**: METRICS-06, METRICS-07, METRICS-08, METRICS-09, METRICS-10, METRICS-11

**Tools**:
- MCP: None
- Skill: None

**Done when**:
- [ ] `api_http_requests_total: Counter` with labelnames `["method", "path", "status_code"]`
- [ ] `api_http_request_duration_seconds: Histogram` with labelnames `["method", "path"]`, buckets matching design.md (HTTP-latency defaults)
- [ ] `reimbursement_status_count: Gauge` with labelnames `["status"]`
- [ ] `reimbursement_review_wait_seconds: Histogram` with no labelnames, buckets `(60, 300, 900, 3600, 14400, 43200, 86400, 259200, 604800)` (1 min–1 week)
- [ ] `UNMATCHED_PATH_LABEL = "unmatched"` constant
- [ ] `ALL_STATUSES: tuple[str, ...]` tuple from spec (6 statuses: pending, human-review, auto-approved, auto-rejected, human-approved, human-rejected)
- [ ] `async def refresh_status_gauge(pool: asyncpg.Pool) -> None` queries db via repository, zero-fills ALL_STATUSES, logs warning and returns on failure (no raise)
- [ ] All metrics named without `_total` suffix in Counter construction (prometheus_client appends it)
- [ ] No import errors
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`

**Tests**: Unit — create `packages/api/tests/test_metrics.py`:
  - Each metric (counter, histogram, gauge) is correctly constructed with expected labelnames
  - `refresh_status_gauge()` queries the pool and sets the gauge
  - `refresh_status_gauge()` zero-fills statuses with 0 count
  - `refresh_status_gauge()` catches db query exceptions and logs warning without raising
  - Histogram buckets for `review_wait_seconds` are distinct from `http_request_duration_seconds` (different scale)

**Gate**: Quick

**Commit**: `feat(api): create metrics module with HTTP RED and status observability metrics`

---

### T6: Create `packages/api/src/api/middleware.py::MetricsMiddleware` class

**What**: Create a new raw ASGI middleware class in `api/middleware.py` (alongside existing `CorrelationIdMiddleware`) that records `api_http_requests_total` and `api_http_request_duration_seconds` for every HTTP request, extracting the route-template path label from `scope["route"]` and defaulting to `UNMATCHED_PATH_LABEL` on 404. Must use the same `send_wrapper` pattern as `CorrelationIdMiddleware` to avoid `BaseHTTPMiddleware` edge cases.

**Where**: `packages/api/src/api/middleware.py` (modify, add new class)

**Depends on**: T5 (api.metrics exists and exports the metric objects)

**Reuses**: `CorrelationIdMiddleware`'s raw ASGI shape; design.md Approach 1 specification.

**Requirement**: METRICS-06, METRICS-07, METRICS-08, METRICS-09

**Tools**:
- MCP: None
- Skill: None

**Done when**:
- [ ] `MetricsMiddleware` class created in `api/middleware.py`
- [ ] `__init__` stores `self.app = app`
- [ ] `__call__` is async, handles non-HTTP scope by passing through unchanged
- [ ] For HTTP requests: records method, status_code, and route path; measures duration via `time.monotonic()` in try/finally
- [ ] Route path comes from `scope.get("route")` populated by Starlette's router; defaults to `UNMATCHED_PATH_LABEL` if route is None
- [ ] Status code defaults to 500 if no `http.response.start` message sent (defensive fallback)
- [ ] Increments both counter and histogram with correct labels
- [ ] No syntax errors, middleware can be imported
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`

**Tests**: Unit — create `packages/api/tests/test_metrics_middleware.py`:
  - Middleware records request for a valid route with correct path template (not resolved UUID)
  - Middleware records status_code from `http.response.start` message
  - Middleware records duration (positive float)
  - Middleware records 404 with path label as `UNMATCHED_PATH_LABEL`
  - Middleware still records request even if an exception is raised in the app (try/finally guarantees)
  - Both metrics incremented exactly once per request

**Gate**: Quick

**Commit**: `feat(api): create metrics middleware for HTTP RED instrumentation`

---

### T7: Add `/metrics` route to `packages/api/src/api/main.py` and wire `MetricsMiddleware`

**What**: In `api/main.py`, add the `@app.get("/metrics")` route that calls `refresh_status_gauge()` and returns Prometheus-format text via `generate_latest()`. Register `MetricsMiddleware` via `app.add_middleware()` alongside the existing `CorrelationIdMiddleware`. Route must use `Depends(get_pool)` for the pool, return `Response` with correct media type, no auth.

**Where**: `packages/api/src/api/main.py` (modify)

**Depends on**: T5, T6

**Reuses**: Existing `/health` route pattern; design.md main.py specification.

**Requirement**: METRICS-01 (AC1: api exposes GET /metrics on main port, no auth)

**Tools**:
- MCP: None
- Skill: None

**Done when**:
- [ ] `@app.get("/metrics")` route added
- [ ] Route calls `await refresh_status_gauge(pool)` before returning
- [ ] Route returns `Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)`
- [ ] Route uses `Depends(get_pool)` to inject the asyncpg pool
- [ ] `MetricsMiddleware` registered via `app.add_middleware(MetricsMiddleware)` (before middleware order doesn't matter, after CorrelationIdMiddleware is idiomatic)
- [ ] No auth dependency on the route
- [ ] App still starts without errors
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`

**Tests**: Route-level (TestClient) — create `packages/api/tests/test_metrics_route.py`:
  - `GET /metrics` returns HTTP 200
  - Response `Content-Type` is Prometheus text format (`text/plain; version=0.0.4`)
  - Response body contains valid Prometheus-format text (lines of `name{labels} value`)
  - Gauge value for `reimbursement_status_count` is updated (can seed DB and verify count changes)
  - 404 path is included in `api_http_requests_total` with path label `UNMATCHED_PATH_LABEL`

**Gate**: Quick

**Commit**: `feat(api): add /metrics endpoint and register metrics middleware`

---

### T8: Update `packages/shared/src/shared/reimbursement/repository.py` with `count_by_status` query and CTE-enhanced `_APPROVE`/`_REJECT`

**What**: In `repository.py`, add `_COUNT_BY_STATUS` SQL constant and `count_by_status()` async function to query current status counts. Enhance `_APPROVE` and `_REJECT` SQL statements with `WITH old AS (...)` CTEs that capture pre-write `status` and `updated_at`, expose them as extra `RETURNING` columns (`from_status`, `from_updated_at`). No return-type change — still `asyncpg.Record | None`, but Record now has these two extra fields when successful.

**Where**: `packages/shared/src/shared/reimbursement/repository.py` (modify)

**Depends on**: T2 (metrics foundation exists; future tasks will consume these queries)

**Reuses**: Existing `_UPPER_SNAKE`/lowercase function naming convention; existing `asyncpg.Record` return pattern.

**Requirement**: METRICS-10, METRICS-11, METRICS-12 (latter depends on getting the from_status for transitions)

**Tools**:
- MCP: None
- Skill: None

**Done when**:
- [ ] `_COUNT_BY_STATUS` SQL constant defined: `"SELECT status, count(*) AS count FROM reimbursement GROUP BY status"`
- [ ] `async def count_by_status(conn: asyncpg.Connection) -> list[asyncpg.Record]` returns one row per status with `status` and `count` fields
- [ ] `_APPROVE` SQL enhanced with `WITH old AS (...)` CTE capturing pre-write status/updated_at
- [ ] `_APPROVE` `RETURNING` clause includes original columns plus `(SELECT status FROM old) AS from_status, (SELECT updated_at FROM old) AS from_updated_at`
- [ ] `_REJECT` SQL enhanced identically to `_APPROVE`
- [ ] No changes to function signatures or return types (still `asyncpg.Record | None`)
- [ ] SQL is correct PostgreSQL syntax (CTE + UPDATE + RETURNING)
- [ ] No import errors
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`

**Tests**: Integration (real Postgres) — create or update `packages/shared/tests/reimbursement/test_repository.py`:
  - `count_by_status()` returns one row per distinct status in the reimbursement table
  - `count_by_status()` returns empty list when table is empty
  - `approve()` (calls `_APPROVE`) returns record with `from_status` and `from_updated_at` fields
  - `reject()` (calls `_REJECT`) returns record with `from_status` and `from_updated_at` fields
  - `from_status` matches the row's status before the update
  - `from_updated_at` matches the row's `updated_at` before the update

**Gate**: Full

**Commit**: `feat(repository): add count_by_status query and CTE-enhanced approve/reject for status transitions`

---

### T9: Create `packages/publisher/src/publisher/metrics.py` with message-lifecycle metrics

**What**: Create the publisher metrics module containing the three metrics owned by publisher: `publisher_messages_consumed_total`, `publisher_messages_requeued_total`, `publisher_duplicate_dropped_total`. All Counters, constructed at module scope (Edge Cases rule). All Counter names without `_total` suffix.

**Where**: `packages/publisher/src/publisher/metrics.py` (new file)

**Depends on**: T2 (shared.metrics foundation exists)

**Reuses**: `prometheus_client.Counter`; design.md metrics.publisher specifications.

**Requirement**: METRICS-22, METRICS-23, METRICS-24 (message lifecycle, closes R-004)

**Tools**:
- MCP: None
- Skill: None

**Done when**:
- [ ] `publisher_messages_consumed_total: Counter` with labelnames `["topic"]`
- [ ] `publisher_messages_requeued_total: Counter` with labelnames `["topic"]`
- [ ] `publisher_duplicate_dropped_total: Counter` with no labelnames
- [ ] All counters named without `_total` suffix
- [ ] All exported at module root
- [ ] No import errors
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`

**Tests**: Unit — create `packages/publisher/tests/test_metrics.py`:
  - Each counter is correctly constructed with expected labelnames
  - `.labels()` and `.inc()` work correctly
  - `duplicate_dropped_total` counter is callable without labels

**Gate**: Quick

**Commit**: `feat(publisher): create metrics module with message-lifecycle counters`

---

### T10: Instrument `packages/publisher/src/publisher/processing.py` to increment message and duplicate counters

**What**: In `publisher/processing.py`, add instrumentation:
  - `_log_duplicate()` calls `publisher_duplicate_dropped_total.inc()` (single choke point for both duplicate-detection paths)
  - `_requeue()` increments `publisher_messages_requeued_total.labels(REQUEST_TOPIC).inc()` before returning `ItemOutcome.REQUEUED`

Reuse existing `_log_duplicate()` and `_requeue()` functions (do not create new ones); just add the metric calls.

**Where**: `packages/publisher/src/publisher/processing.py` (modify)

**Depends on**: T9 (publisher.metrics exists)

**Reuses**: Existing `_log_duplicate()` and `_requeue()` functions; REQUEST_TOPIC constant.

**Requirement**: METRICS-23, METRICS-24 (requeue path + duplicate drop path)

**Tools**:
- MCP: None
- Skill: None

**Done when**:
- [ ] `_log_duplicate()` increments `publisher_duplicate_dropped_total.inc()` (no labels)
- [ ] `_requeue()` increments `publisher_messages_requeued_total.labels(REQUEST_TOPIC).inc()` immediately before `return ItemOutcome.REQUEUED`
- [ ] Metric calls don't interfere with existing logic (just added, no control-flow changes)
- [ ] No import errors
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`

**Tests**: Unit — update `packages/publisher/tests/test_processing.py`:
  - When `_log_duplicate()` is called, `publisher_duplicate_dropped_total` counter increments once
  - When an item is requeued, `publisher_messages_requeued_total` increments once with topic label
  - On transient failure triggering requeue, counter is incremented exactly once
  - Test doubles (fakes) can inject behavior to trigger duplicate/requeue paths

**Gate**: Quick

**Commit**: `feat(publisher): instrument message requeue and duplicate-drop paths with metrics`

---

### T11: Instrument `packages/publisher/src/publisher/consumer.py` to start metrics server and count consumed messages

**What**: In `publisher/consumer.py`, add instrumentation:
  - `main()`: after `configure_logging()`, before `asyncio.run(_serve())`, call `start_metrics_server(load_publisher_config().metrics_port)`
  - `run()` (the message-loop function): after the `error is not None` guard (i.e., only for genuinely delivered messages), before `await handle_message(...)`, increment `publisher_messages_consumed_total.labels(REQUEST_TOPIC).inc()`

**Where**: `packages/publisher/src/publisher/consumer.py` (modify)

**Depends on**: T2, T9, T10

**Reuses**: Existing `load_publisher_config()`, `configure_logging()`, `handle_message()` flow; REQUEST_TOPIC constant.

**Requirement**: METRICS-02 (AC2: publisher starts metrics HTTP server), METRICS-22 (AC3: consumed messages counter)

**Tools**:
- MCP: None
- Skill: None

**Done when**:
- [ ] `start_metrics_server(load_publisher_config().metrics_port)` called in `main()` after `configure_logging()`
- [ ] Message consumed counter incremented in `run()` after error guard, before message processing
- [ ] Consumed counter labeled with `REQUEST_TOPIC`
- [ ] No import errors
- [ ] Metrics server starts without errors (can be verified in tests by mock)
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`

**Tests**: Unit — update `packages/publisher/tests/test_consumer.py`:
  - `start_metrics_server()` is called once during startup with correct port
  - Each genuinely consumed message (past error guard) increments counter exactly once
  - Protocol-level errors (before message body) do not increment counter

**Gate**: Quick

**Commit**: `feat(publisher): start metrics server at startup and count consumed messages`

---

### T12: Create `packages/reimbursement/src/reimbursement/metrics.py` with decision-pipeline metrics

**What**: Create the reimbursement metrics module containing the 8 metrics owned by reimbursement (all counters and histograms): `reimbursement_agent_decision_duration_seconds`, `reimbursement_time_to_decision_seconds`, `reimbursement_agent_node_duration_seconds`, `reimbursement_agent_llm_calls_total`, `reimbursement_policy_rule_triggered_total`, `reimbursement_decision_failure_escalations_total`, `reimbursement_messages_consumed_total`, `reimbursement_messages_requeued_total`. All constructed at module scope (Edge Cases rule). Counter names without `_total` suffix. Histograms with distinct bucket sets per design.md.

**Where**: `packages/reimbursement/src/reimbursement/metrics.py` (new file)

**Depends on**: T2 (shared.metrics foundation exists)

**Reuses**: `prometheus_client.Counter`, `prometheus_client.Histogram`; design.md bucket specifications.

**Requirement**: METRICS-13, METRICS-14, METRICS-15, METRICS-17, METRICS-18, METRICS-19, METRICS-20, METRICS-21

**Tools**:
- MCP: None
- Skill: None

**Done when**:
- [ ] `reimbursement_agent_decision_duration_seconds: Histogram` — no labels, buckets `(0.1, 0.5, 1, 2, 5, 10, 20, 30, 60, 120)` (sub-second to 2 minutes)
- [ ] `reimbursement_time_to_decision_seconds: Histogram` — no labels, buckets `(1, 5, 15, 30, 60, 300, 900, 1800, 3600, 21600)` (1 second to 6 hours, distinct from agent_decision)
- [ ] `reimbursement_agent_node_duration_seconds: Histogram` — labelnames `["node", "model"]`, buckets `(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)` (finer-grained than full graph)
- [ ] `reimbursement_agent_llm_calls_total: Counter` — labelnames `["model", "outcome"]`
- [ ] `reimbursement_policy_rule_triggered_total: Counter` — labelnames `["rule"]`
- [ ] `reimbursement_decision_failure_escalations_total: Counter` — no labels
- [ ] `reimbursement_messages_consumed_total: Counter` — labelnames `["topic"]`
- [ ] `reimbursement_messages_requeued_total: Counter` — labelnames `["topic"]`
- [ ] All counter names without `_total` suffix
- [ ] All exported at module root
- [ ] No import errors
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`

**Tests**: Unit — create `packages/reimbursement/tests/test_metrics.py`:
  - Each histogram has correct labelnames and bucket boundaries
  - Each counter has correct labelnames
  - Histograms can observe positive float values
  - Counters can increment with and without labels
  - Review-wait histogram buckets (1 min–1 week) are distinct from decision-duration histograms (sub-second–minutes)

**Gate**: Quick

**Commit**: `feat(reimbursement): create metrics module with decision-pipeline and message-lifecycle metrics`

---

### T13: Create `PolicyRule` enum in `packages/reimbursement/src/reimbursement/agent/nodes/apply_policies.py` and wire rule-triggering counters

**What**: Add a module-level `PolicyRule` enum with three values (`STALE_RECEIPT_REJECT`, `LOW_VALUE_AUTO_APPROVE`, `HIGH_VALUE_HUMAN_REVIEW`), and modify each of the three rule-matching branches to:
  1. Set a `rule: PolicyRule` variable
  2. Increment `reimbursement_policy_rule_triggered_total.labels(rule.value).inc()` immediately once the rule fires (before DB write)

The `else` branch (LLM-judgment required) remains unchanged — no rule increments there by definition.

**Where**: `packages/reimbursement/src/reimbursement/agent/nodes/apply_policies.py` (modify)

**Depends on**: T12 (reimbursement.metrics exists)

**Reuses**: Existing three-branch `if`/`elif` structure; design.md apply_policies specification.

**Requirement**: METRICS-16, METRICS-17 (AC1: enum created; AC2: rule counter incremented)

**Tools**:
- MCP: None
- Skill: None

**Done when**:
- [ ] `PolicyRule(str, Enum)` enum class defined with three string values: `"stale-receipt-reject"`, `"low-value-auto-approve"`, `"high-value-human-review"`
- [ ] Each of the three `if`/`elif` branches captures its corresponding enum value
- [ ] Each branch increments counter with `rule.value` before returning/yielding result
- [ ] The `else` branch (LLM judgment) is unchanged — no rule fires there
- [ ] Counter increments happen independently of DB write success
- [ ] No import errors
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`

**Tests**: Unit — update `packages/reimbursement/tests/test_apply_policies.py`:
  - When stale-receipt rule fires, `reimbursement_policy_rule_triggered_total` increments with label `stale-receipt-reject`
  - When low-value rule fires, counter increments with label `low-value-auto-approve`
  - When high-value rule fires, counter increments with label `high-value-human-review`
  - When no rule fires (LLM-judgment path), counter does not increment
  - Counter increments exactly once per rule firing (no double-counting on DB retry)

**Gate**: Quick

**Commit**: `feat(reimbursement): add PolicyRule enum and instrument rule-triggered metrics`

---

### T14: Instrument `packages/reimbursement/src/reimbursement/agent/nodes/extract_fields.py` and `analysis.py` to count LLM calls

**What**: In both `extract_fields.py` and `analysis.py`, wrap each node's `await self._model.ainvoke(messages)` call:
  ```python
  try:
      result = await self._model.ainvoke(messages)
  except Exception:
      reimbursement_agent_llm_calls_total.labels(self._model_name, "failure").inc()
      raise
  reimbursement_agent_llm_calls_total.labels(self._model_name, "success").inc()
  ```
  The exception still propagates; this metric just observes, doesn't intercept.

**Where**: `packages/reimbursement/src/reimbursement/agent/nodes/extract_fields.py` (modify), `packages/reimbursement/src/reimbursement/agent/nodes/analysis.py` (modify)

**Depends on**: T12 (reimbursement.metrics exists)

**Reuses**: Existing `await self._model.ainvoke()` call pattern; `self._model_name` attribute already available.

**Requirement**: METRICS-18 (AC3: LLM calls counter with model and outcome labels)

**Tools**:
- MCP: None
- Skill: None

**Done when**:
- [ ] `extract_fields.py`: wrap `ainvoke()` with try/except, increment counter on success and failure
- [ ] `analysis.py`: wrap `ainvoke()` with try/except, increment counter on success and failure
- [ ] Exception propagates unchanged after counter increment
- [ ] Counter labeled with `self._model_name` and `"success"` or `"failure"`
- [ ] No import errors
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`

**Tests**: Unit — update `packages/reimbursement/tests/test_extract_fields.py` and `test_analysis.py`:
  - On successful `ainvoke()`, counter increments with outcome `success`
  - On `ainvoke()` raising exception, counter increments with outcome `failure` and exception still propagates
  - Counter labels include correct model name from config
  - Counter increments exactly once per call (no double-counting on retry)

**Gate**: Quick

**Commit**: `feat(reimbursement): instrument LLM calls with model and outcome metrics`

---

### T15: Wire per-node timing into `packages/reimbursement/src/reimbursement/agent/agent.py`

**What**: In `agent.py`, create a `_timed_node()` helper that wraps a node callable to measure its duration via `time.monotonic()` in a try/finally, observing `reimbursement_agent_node_duration_seconds`. Then modify `_wire()` to accept an optional `node_models` dict parameter (default `{}`), and wrap each node via `_timed_node()` before adding to the graph. Finally, modify `build_graph()` to pass `{"extract_fields": config.models.extract_fields.model_name, "analysis": config.models.analysis.model_name}` as the `node_models` parameter. Also wrap `decide()`'s own `await graph.ainvoke(...)` call to observe `reimbursement_agent_decision_duration_seconds`.

**Where**: `packages/reimbursement/src/reimbursement/agent/agent.py` (modify)

**Depends on**: T12 (reimbursement.metrics exists)

**Reuses**: `time.monotonic()` pattern; existing `_wire()`, `build_graph()`, `decide()` functions; `Node` Protocol from types.py; design.md agent specifications.

**Requirement**: METRICS-13, METRICS-15 (AC1: agent_decision_duration_seconds; AC3: agent_node_duration_seconds with model label for 2 nodes)

**Tools**:
- MCP: None
- Skill: None

**Done when**:
- [ ] `_timed_node(name: str, node: Node, model_name: str = "") -> Node` helper created — wraps node in async closure, observes duration
- [ ] `_wire()` accepts optional `node_models: dict[str, str] | None = None` parameter (default `{}`)
- [ ] `_wire()` wraps each node via `_timed_node(node_name, node, node_models.get(node_name, ""))` before graph.add_node()
- [ ] `build_graph()` calls `_wire(..., node_models={"extract_fields": config.models.extract_fields.model_name, "analysis": config.models.analysis.model_name})`
- [ ] `decide()` wraps `await graph.ainvoke()` in try/finally with `time.monotonic()`, observing `reimbursement_agent_decision_duration_seconds.observe(duration)` in finally
- [ ] Model label is empty string `""` for non-LLM nodes (not truly absent)
- [ ] Duration measured unconditionally (success or failure)
- [ ] Existing routing tests still pass (they now call `_wire()` without `node_models`, which defaults to empty dict)
- [ ] No import errors
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`

**Tests**: Unit — update `packages/reimbursement/tests/test_agent.py`:
  - Per-node duration is recorded for each node in the graph
  - Only `extract_fields` and `analysis` nodes observe non-empty `model` label
  - Other three nodes observe empty string `""` as model label
  - Full-graph `agent_decision_duration_seconds` is measured independently from per-node durations
  - Duration is observed even if `agent.decide()` raises an exception
  - When `_wire()` is called without `node_models`, routing still works and node labels are empty strings

**Gate**: Quick

**Commit**: `feat(reimbursement): add per-node and full-graph decision-latency timing`

---

### T16: Instrument `packages/reimbursement/src/reimbursement/validation.py` to count status transitions, decision latency, and failure escalations

**What**: In `validation.py`:
  - `_decide()`: on success branch, before `return MessageOutcome.RESOLVED`, and only when `final_state.get("persisted")` is true, observe `reimbursement_time_to_decision_seconds` with `(datetime.now(UTC) - row["created_at"]).total_seconds()`
  - `_escalate_decision_failure()`: increment `reimbursement_decision_failure_escalations_total.inc()` unconditionally, before calling `_escalate_row()` — this is the ONE and only increment site for this metric
  - `_requeue()`: increment `reimbursement_messages_requeued_total.labels(REIMBURSEMENT_TOPIC).inc()` immediately before `return MessageOutcome.REQUEUED` (publish-succeeded branch only)

**Where**: `packages/reimbursement/src/reimbursement/validation.py` (modify)

**Depends on**: T12 (reimbursement.metrics exists)

**Reuses**: Existing `_decide()`, `_escalate_decision_failure()`, `_requeue()` functions; `row["created_at"]` already available as parameter to `_decide()`.

**Requirement**: METRICS-14, METRICS-19, METRICS-21 (AC2: end-to-end latency; AC4: failure escalations; AC2: message requeue)

**Tools**:
- MCP: None
- Skill: None

**Done when**:
- [ ] `_decide()`: observes `reimbursement_time_to_decision_seconds` on success with persisted=true, using row's created_at
- [ ] `_escalate_decision_failure()`: increments failure-escalation counter before `_escalate_row()`, no guard (unconditional)
- [ ] `_requeue()`: increments requeue counter before returning, only on successful publish branch
- [ ] Metric calls don't change control flow or error handling
- [ ] No import errors
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`

**Tests**: Unit (or integration) — update `packages/reimbursement/tests/test_validation.py`:
  - When a decision persists successfully, `reimbursement_time_to_decision_seconds` observes the latency
  - When a row never persists (ghost case), time-to-decision is not observed
  - When `_escalate_decision_failure()` is called, counter increments exactly once
  - When `_requeue()` returns successfully (message published), counter increments
  - When `_requeue()` fails to publish, counter does not increment
  - Metrics observe independently of control flow (no exceptions raised or suppressed by metrics code)

**Gate**: Quick

**Commit**: `feat(reimbursement): instrument status-transition latency and failure-escalation metrics`

---

### T17: Instrument `packages/reimbursement/src/reimbursement/consumer.py` to start metrics server and count consumed messages

**What**: In `reimbursement/consumer.py`, add instrumentation:
  - `main()`: after `configure_logging()`, before `asyncio.run(_serve())`, call `start_metrics_server(load_agent_config().metrics_port)`
  - `run()` (the message-loop function): after the `error is not None` guard (i.e., only for genuinely delivered messages), before `await handle_message(...)`, increment `reimbursement_messages_consumed_total.labels(REIMBURSEMENT_TOPIC).inc()`

**Where**: `packages/reimbursement/src/reimbursement/consumer.py` (modify)

**Depends on**: T2, T12, T16

**Reuses**: Existing `load_agent_config()`, `configure_logging()`, `handle_message()` flow; REIMBURSEMENT_TOPIC constant.

**Requirement**: METRICS-03 (AC3: reimbursement starts metrics HTTP server), METRICS-20 (AC1: consumed messages counter)

**Tools**:
- MCP: None
- Skill: None

**Done when**:
- [ ] `start_metrics_server(load_agent_config().metrics_port)` called in `main()` after `configure_logging()`
- [ ] Message consumed counter incremented in `run()` after error guard, before message processing
- [ ] Consumed counter labeled with `REIMBURSEMENT_TOPIC`
- [ ] No import errors
- [ ] Metrics server starts without errors
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`

**Tests**: Unit — update `packages/reimbursement/tests/test_consumer.py`:
  - `start_metrics_server()` is called once during startup with correct port
  - Each genuinely consumed message (past error guard) increments counter exactly once
  - Protocol-level errors do not increment counter

**Gate**: Quick

**Commit**: `feat(reimbursement): start metrics server at startup and count consumed messages`

---

### T18: Wire metrics into `packages/shared/src/shared/reimbursement/use_cases/apply_decision.py` and `packages/api/src/api/reimbursement/update/route.py` for status transitions and review wait

**What**: In `apply_decision.py`, after a successful `repository.update_decision()` call, increment `reimbursement_status_transitions_total.labels("pending", status).inc()` (from is hardcoded, per design.md Approach 3 and the invariant that reimbursement only writes to rows at pending). In `api/reimbursement/update/route.py`, after a successful `approve_reimbursement()` or `reject_reimbursement()` call (before connection release), read the CTE's extra columns off `decision_row` (not the later re-fetched `row`), then after the success gate (decision_write_error is None):
  - Increment `reimbursement_status_transitions_total.labels(from_status, to_status).inc()` with the CTE-captured prior status
  - If `from_status == "human-review"`, observe `reimbursement_review_wait_seconds` with elapsed time since `from_updated_at`

**Where**: `packages/shared/src/shared/reimbursement/use_cases/apply_decision.py` (modify), `packages/api/src/api/reimbursement/update/route.py` (modify)

**Depends on**: T8 (repository queries with CTE available), T5 (api.metrics with review_wait_seconds exists), T2 (shared.metrics with status_transitions_total exists)

**Reuses**: Existing `apply_decision()` and route functions; decision_row returned from approve/reject; existing CTE columns.

**Requirement**: METRICS-12 (AC3: status transitions in both services), METRICS-11 (AC2: review wait time)

**Tools**:
- MCP: None
- Skill: None

**Done when**:
- [ ] `apply_decision()` increments `reimbursement_status_transitions_total.labels("pending", status).inc()` after successful update
- [ ] `api` route increments counter with actual from/to statuses from CTE columns
- [ ] `api` route observes review-wait histogram only when from_status is "human-review"
- [ ] Review-wait observation uses datetime subtraction with row's created_at/updated_at as appropriate
- [ ] Both increments/observations happen independently, don't prevent DB writes or raise exceptions
- [ ] No import errors
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`

**Tests**: Unit + integration — update `packages/shared/tests/reimbursement/use_cases/test_apply_decision.py` and `packages/api/tests/reimbursement/update/test_route.py`:
  - When `apply_decision()` persists a transition, counter increments with correct labels
  - When `PUT` approve/reject is called, counter increments with correct from/to statuses from CTE
  - When an item exits human-review via PUT, review-wait histogram observes the duration
  - When an item is in a different status (auto-rejected, human-rejected) and then PUT again, review-wait is not observed (guard works)
  - Metric increments/observations happen exactly once per call

**Gate**: Full

**Commit**: `feat: instrument status-transition and review-wait metrics in shared and api`

---

### T19: Create `docs/METRICS.md` and update `docs/codebase/CONCERNS.md` with two new follow-up entries

**What**: Create `docs/METRICS.md` with:
  1. The full 16-metric catalog table (name, type, service, labels, description, endpoint/port) from spec.md, with added port/endpoint column
  2. The three rule-ID enum values and their thresholds (90 days / ≤200 / >2000)
  3. Explanation of the decision-latency histogram split (why three separate histograms, why they don't share buckets)
  4. Why 3 independent scrape targets instead of federated/centralized
  5. Counter naming rule (constructed without `_total`, appended by prometheus_client)
  6. Pointer to `docs/codebase/CONCERNS.md` for MVP gaps

Update `docs/codebase/CONCERNS.md`:
  - (a) Extend the existing "No authentication on the public API" entry to include `/metrics` on all three services and reference the new 9101/9102 ports
  - (b) Add a new entry under "Missing Critical Features": 3 new Prometheus scrape targets need k3s manifest updates (publisher 9101, reimbursement 9102)

**Where**: `docs/METRICS.md` (new file), `docs/codebase/CONCERNS.md` (modify)

**Depends on**: All previous tasks (feature is functionally complete, only documentation remains)

**Reuses**: Spec.md Metric Catalog (copy verbatim); design.md architecture notes; existing CONCERNS.md structure.

**Requirement**: METRICS-25, METRICS-26, METRICS-27 (documentation deliverables)

**Tools**:
- MCP: None
- Skill: None

**Done when**:
- [ ] `docs/METRICS.md` exists with all 16 metrics in a table, name/type/service/labels/description columns match spec.md exactly
- [ ] Enum values (`stale-receipt-reject`, `low-value-auto-approve`, `high-value-human-review`) listed with thresholds
- [ ] Histogram explanation covers why three separate histograms, why buckets don't overlap
- [ ] Scrape-target explanation covers why 3 endpoints instead of 1
- [ ] Counter naming rule explained (construct without `_total`, prometheus_client appends it)
- [ ] Pointer to CONCERNS.md for MVP gaps included
- [ ] `docs/codebase/CONCERNS.md` existing "No authentication" entry extended to mention `/metrics` + 9101/9102 ports
- [ ] New entry added under "Missing Critical Features" about k3s manifest updates, with problem/files/effort/blocker fields filled
- [ ] No markdown syntax errors
- [ ] Build/lint checks pass (if any)

**Tests**: None (documentation, verified by content review and markdown validation)

**Gate**: Build

**Commit**: `docs: add METRICS.md and update CONCERNS.md with RA-3 metrics MVP gaps`

---

## Phase Execution Map

Visual representation of task ordering. Phases run in sequence, and tasks within a phase run in order:

```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5

Phase 1: T1 → T2 → T3 → T4
Phase 2: T5 → T6 → T7 → T8
Phase 3: T9 → T10 → T11
Phase 4: T12 → T13 → T14 → T15 → T16 → T17
Phase 5: T18 → T19
```

**Total tasks:** 19 (fits 2 batches: Phase 1-2 [8 tasks] + Phases 3-4 [9 tasks] + Phase 5 [2 tasks] — or single batch at user discretion per `sub-agents.md` packing)

---

## Task Granularity Check

| Task | Scope | Status |
|------|-------|--------|
| T1: Add prometheus-client dependency | 1 file, 1 addition | ✅ Granular |
| T2: Create shared.metrics module | 1 file, 2 exports | ✅ Granular |
| T3: Add METRICS_PORT to publisher config | 1 file, 1 field | ✅ Granular |
| T4: Add METRICS_PORT to reimbursement config | 1 file, 1 field | ✅ Granular |
| T5: Create api.metrics module | 1 file, 5 metrics + 2 helpers | ✅ Granular |
| T6: Create MetricsMiddleware class | 1 file, 1 class addition | ✅ Granular |
| T7: Add /metrics route + wire middleware | 1 file, 1 route + 1 middleware registration | ✅ Granular |
| T8: Update repository with count_by_status + CTE | 1 file, 1 query + 2 enhanced statements | ✅ Granular |
| T9: Create publisher.metrics module | 1 file, 3 counters | ✅ Granular |
| T10: Instrument publisher processing | 1 file, 2 metric calls | ✅ Granular |
| T11: Instrument publisher consumer | 1 file, 2 metric calls + startup | ✅ Granular |
| T12: Create reimbursement.metrics module | 1 file, 8 metrics | ✅ Granular |
| T13: Add PolicyRule enum + instrument apply_policies | 1 file, 1 enum + 3 metric calls | ✅ Granular |
| T14: Instrument extract_fields + analysis LLM calls | 2 files, 2 metric wraps | ✅ Granular (closely related, same pattern) |
| T15: Wire per-node timing + full-graph timing | 1 file, 2 helpers + modifications | ✅ Granular |
| T16: Instrument validation decision latency + escalation + requeue | 1 file, 3 metric sites | ✅ Granular (same file, different branches) |
| T17: Instrument reimbursement consumer | 1 file, 2 metric calls + startup | ✅ Granular |
| T18: Wire metrics into apply_decision + api.update.route | 2 files, 2 cross-service metric sites | ✅ Granular (same pattern, closes cross-service loop) |
| T19: Create docs + update concerns | 2 files, documentation | ✅ Granular |

---

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
|------|------------------------|---------------|--------|
| T1 | None | T1 start | ✅ Match |
| T2 | T1 | T1 → T2 | ✅ Match |
| T3 | T1 | T1 → T3 | ✅ Match |
| T4 | T1 | T1 → T4 | ✅ Match |
| T5 | T2 | T2 → T5 | ✅ Match |
| T6 | T5 | T5 → T6 | ✅ Match |
| T7 | T5, T6 | T5,T6 → T7 | ✅ Match |
| T8 | T2 | T2 → T8 | ✅ Match |
| T9 | T2 | T2 → T9 | ✅ Match |
| T10 | T9 | T9 → T10 | ✅ Match |
| T11 | T2, T9, T10 | T2,T9,T10 → T11 | ✅ Match |
| T12 | T2 | T2 → T12 | ✅ Match |
| T13 | T12 | T12 → T13 | ✅ Match |
| T14 | T12 | T12 → T14 | ✅ Match |
| T15 | T12 | T12 → T15 | ✅ Match |
| T16 | T12 | T12 → T16 | ✅ Match |
| T17 | T2, T12, T16 | T2,T12,T16 → T17 | ✅ Match |
| T18 | T8, T5, T2 | T8,T5,T2 → T18 | ✅ Match |
| T19 | all previous (implicit final) | all → T19 | ✅ Match |

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
|------|---------------------------|-----------------|-----------|--------|
| T1 | Dependency (pyproject.toml) | none | None | ✅ OK |
| T2 | Unit (shared.metrics module) | Unit | Unit | ✅ OK |
| T3 | Unit (config) | Unit | Unit | ✅ OK |
| T4 | Unit (config) | Unit | Unit | ✅ OK |
| T5 | Unit (api.metrics module) | Unit | Unit | ✅ OK |
| T6 | Unit (middleware class) | Unit | Unit | ✅ OK |
| T7 | Route (GET /metrics) + middleware registration | Route-level | Route-level | ✅ OK |
| T8 | Integration (DB query + SQL statements) | Integration | Integration | ✅ OK |
| T9 | Unit (publisher.metrics module) | Unit | Unit | ✅ OK |
| T10 | Unit (processing instrumentation) | Unit | Unit | ✅ OK |
| T11 | Unit (consumer instrumentation) | Unit | Unit | ✅ OK |
| T12 | Unit (reimbursement.metrics module) | Unit | Unit | ✅ OK |
| T13 | Unit (apply_policies enum + instrumentation) | Unit | Unit | ✅ OK |
| T14 | Unit (extract_fields + analysis instrumentation) | Unit | Unit | ✅ OK |
| T15 | Unit (agent.py timing helpers + instrumentation) | Unit | Unit | ✅ OK |
| T16 | Unit (validation.py instrumentation) | Unit | Unit | ✅ OK |
| T17 | Unit (consumer.py instrumentation) | Unit | Unit | ✅ OK |
| T18 | Unit + Integration (apply_decision + route instrumentation) | Unit + Integration | Unit + Integration | ✅ OK |
| T19 | Documentation (.md files) | none | None | ✅ OK |

All tasks are granular, dependencies match the diagram, and test co-location matches the coverage matrix. No task deferring tests to another task; no missing tests for code layers with requirements.

---

## Tips

- **Phases are ordered** — Each phase completes before the next; tasks run in order within a phase
- **Reuses = Token saver** — Always reference existing code (middleware, config patterns, etc.)
- **Tools per task** — Most tasks need no MCP; design.md components drive the implementation
- **Dependencies are gates** — Clear dependency chains keep tasks unblocked and testable
- **Done when = Testable** — Every `Done when` entry is binary pass/fail and gates execution
- **Requirement ID = Traceable** — Every task maps to one or more spec requirements (METRICS-NN)
- **One commit per task** — Conventional commit format (`feat`, `fix`, etc.) with scope
- **Test Coverage Matrix** — All code layers in scope have required test types before Execute

---

## MCP and Skill Confirmation

**For Execute, confirm:**

- **Available MCPs**: None required; all tasks use standard Python/PostgreSQL/Kafka tooling already in the project
- **Available Skills**: `tlc-spec-driven` (for Execute flow and Verifier); no other skills needed

All tasks execute with standard file reads/writes and project test commands (pytest, uv).
