# Traceability Correlation IDs Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user — do not proceed without it.**

---

**Design**: `.specs/features/traceability-correlation-ids/design.md`
**Status**: Done — all 9 tasks (T1-T9) implemented, gated, and committed. See `validation.md` for the Verifier's independent report.

---

## Pre-Execute Note (not a task here, do not fix as part of this feature)

`main` at this feature's base commit already carries baseline breakage in
`packages/reimbursement/tests/` that is **not** this feature's to fix:

- `test_config.py::DescribeAgentConfig::it_defaults_the_ollama_settings_when_unset`
  fails (`config.py`'s `_DEFAULT_OLLAMA_MODEL` was changed to `"qwen3.5:4b"`
  without updating the test's expected `"llama3.2"`).
- `test_agent.py`, `test_analysis.py`, `test_extract_fields.py`,
  `test_integration.py` fail to **collect** (`ImportError: cannot import
  name 'PLACEHOLDER_PROMPT'` — the prompt modules were refactored to
  `get_analysis_prompt()`/`get_extract_fields_prompt()` without updating
  these test files' imports or the now-removed `prompt=` constructor kwarg).

Both are squarely the scope of the concurrently in-flight sibling feature
`agent-model-config` — confirmed via `.specs/features/agent-model-config/tasks.md`,
whose T1/T4/T5/T6 explicitly own the Ollama→Groq config rename and the
`PLACEHOLDER_PROMPT` import fixes in exactly these files. Per this session's
operating instructions ("do not try to coordinate; just implement your spec
correctly against current `main`"), these tasks below do not touch
`config.py`, `prompts/*.py`, or fix these four test files. Every task's gate
command below either scopes around `packages/reimbursement/tests/` entirely
(the `api`/`shared` gates) or, where it must touch `packages/reimbursement`
(T8/T9), uses `--continue-on-collection-errors` and asserts the *rest* of
that package's suite (which does collect) stays green, noting the
pre-existing 1 failure / 4 collection errors as unrelated baseline noise.

---

## Test Coverage Matrix

> Generated from `docs/codebase/TESTING.md` (existing, documented project
> guidelines — cited directly) plus this feature's own spec.md. spec.md's
> Out of Scope table explicitly excludes "Dedicated automated tests for the
> logging/correlation-id behavior this feature adds (**TRC-01..10**)" —
> verification for those is by code inspection plus keeping the existing
> full suite green. **TRC-11 is deliberately not in that excluded range**
> (spec.md's own Why: uvicorn's default logging setup means the other ten
> requirements' new `.info()` calls would silently no-op without it) — it
> gets one dedicated unit test below.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| --- | --- | --- | --- | --- |
| `shared.logging.log_event()` (TRC-10) | None | Per spec.md Out of Scope — exercised indirectly by every call site this feature adds; not unit-tested itself | `packages/shared/src/shared/logging.py` | build gate only |
| `api` create route (TRC-01) | None (existing suite must stay green) | Per spec.md Out of Scope; `packages/api/tests/reimbursement/create/test_route.py` continues passing unmodified | `packages/api/src/api/reimbursement/create/route.py` | `uv run pytest packages/api -m "not integration"` |
| `api` update route (TRC-02, TRC-03) | None (existing suite must stay green) | Per spec.md Out of Scope; `packages/api/tests/reimbursement/update/test_route.py` continues passing unmodified | `packages/api/src/api/reimbursement/update/route.py` | same |
| `api` list route (TRC-05) | None (existing suite must stay green) | Per spec.md Out of Scope; `packages/api/tests/reimbursement/list/test_route.py` continues passing unmodified | `packages/api/src/api/reimbursement/list/route.py` | same |
| `api` get route (TRC-06) | None (existing suite must stay green) | Per spec.md Out of Scope; `packages/api/tests/reimbursement/get/test_route.py` continues passing unmodified | `packages/api/src/api/reimbursement/get/route.py` | same |
| `api` catch-all handler (TRC-04) | None (existing suite must stay green) | Per spec.md Out of Scope; `packages/api/tests/test_errors.py` continues passing unmodified | `packages/api/src/api/errors.py` | same |
| `api` startup logging config (TRC-11) | Unit | **Not excluded by spec.md** — one dedicated test asserting `logging.basicConfig(level=logging.INFO)` is invoked on module import | `packages/api/tests/test_main.py` | same |
| `reimbursement` agent nodes uuid logging (TRC-07) | None (existing suite must stay green where collectible) | Per spec.md Out of Scope; verified by code inspection — the 5 node test files are part of the pre-existing, unrelated collection failure noted above and cannot currently prove this either way | `packages/reimbursement/src/reimbursement/agent/nodes/*.py` | `uv run pytest packages/reimbursement -m "not integration" --continue-on-collection-errors` (informational only, see Pre-Execute Note) |
| `reimbursement` agent LangFuse correlation (TRC-08, TRC-09) | None (existing suite must stay green where collectible) | Per spec.md Out of Scope; verified by code inspection — `test_agent.py::DescribeDecide` is part of the same pre-existing collection failure; change is additive-only (one new `config` dict key) and preserves the two keys (`callbacks`, `configurable`) that test already asserts | `packages/reimbursement/src/reimbursement/agent/agent.py` | same |

**Coverage Expectation values** — set directly from spec.md's own explicit
Out of Scope decision (a documented project guideline for this feature, not
the skill's strong default), except TRC-11 which spec.md deliberately left
outside that exclusion.

## Gate Check Commands

> Sourced from `docs/codebase/TESTING.md`'s own Gate Check Commands table,
> scoped per package where a task doesn't touch every package.

| Gate Level | When to Use | Command |
| --- | --- | --- |
| Quick (api/shared) | After tasks touching only `api`/`shared` | `uv run pytest packages/api packages/shared -m "not integration"` |
| Quick (reimbursement, informational) | After tasks touching `packages/reimbursement` | `uv run pytest packages/reimbursement -m "not integration" --continue-on-collection-errors` — pre-existing 1 failure + 4 collection errors expected (see Pre-Execute Note); zero *new* failures is the actual bar |
| Full | Before considering the feature done | `uv run pytest -m "not integration" --continue-on-collection-errors` (whole-workspace gate, same pre-existing exceptions carried over) |

---

## Execution Plan

Phases are ordered and run sequentially — each phase completes before the next begins, and tasks within a phase execute in order.

### Phase 1: Foundation

```
T1 → T2
```

### Phase 2: API Route Logging

```
T3 → T4 → T5 → T6
```

### Phase 3: Catch-All + Agent Layer

```
T7 → T8 → T9
```

---

## Task Breakdown

### T1: `shared.logging.log_event()` helper

**What**: New module exposing `log_event(logger, level, event, **fields)` — builds `{"event": event, **fields}`, serializes via `json.dumps(..., default=str)`, emits via `logger.log(level, "%s", payload)`, never raises (internal `try/except Exception` falling back to a named fallback logger, mirroring `failure_log.write`'s own defensive shape).
**Where**: `packages/shared/src/shared/logging.py`
**Depends on**: None
**Reuses**: `failure_log.write`'s `default=str` + defensive try/except shape (`packages/shared/src/shared/failure_log.py:41-63`), without its `_truncated` file-specific logic
**Requirement**: TRC-10

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None` defined, docstring states the `from shared.logging import log_event` (never `from shared import logging`) calling convention
- [x] Emits one `json.dumps({"event": event, **fields}, default=str)` payload via `logger.log(level, "%s", payload)`
- [x] Internal failure (e.g. a genuinely non-serializable field even with `default=str`) is caught and falls back to a named fallback logger — never propagates to the caller
- [x] Gate check passes: `uv sync --all-packages` (build only — no dedicated test per matrix)

**Tests**: none (per Test Coverage Matrix)
**Gate**: build

---

### T2: `api/main.py` — `logging.basicConfig(level=logging.INFO)` at startup

**What**: Add `logging.basicConfig(level=logging.INFO)` near the top of the module (after imports, alongside the existing `load_dotenv()` call, not inside `lifespan()`), mirroring `publisher/consumer.py:142`/`reimbursement/consumer.py:100`'s own entrypoint pattern.
**Where**: `packages/api/src/api/main.py`
**Depends on**: None
**Reuses**: `logging.basicConfig(level=logging.INFO)` pattern already used verbatim by `publisher`/`reimbursement`'s entrypoints
**Requirement**: TRC-11

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `logging.basicConfig(level=logging.INFO)` called at module scope, before any request-handling code runs
- [x] New test in `packages/api/tests/test_main.py` (`DescribeLoggingSetup`): monkeypatches `logging.basicConfig`, `importlib.reload`s `api.main`, asserts it was called with `level=logging.INFO`
- [x] Gate check passes: `uv run pytest packages/api packages/shared -m "not integration"`
- [x] Test count recorded (no silent deletions vs. pre-task count)

**Tests**: unit
**Gate**: quick (api/shared)

**Commit**: `feat(api): call logging.basicConfig at startup so new INFO logs emit`

---

### T3: `create/route.py` — log the accepted batch

**What**: Add `logger = logging.getLogger(__name__)`; after `await publish(...)` succeeds, before `return`, call `log_event(logger, logging.INFO, BATCH_ACCEPTED_EVENT, request_ids=request_ids, accepted_count=accepted_count)`.
**Where**: `packages/api/src/api/reimbursement/create/route.py`
**Depends on**: T1
**Reuses**: `request_ids`/`accepted_count`, already computed in the existing code for the response message
**Requirement**: TRC-01

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] Module-level `BATCH_ACCEPTED_EVENT = "reimbursement.batch_accepted"` constant added (naming convention matches `publisher.processing`'s `*_EVENT` constants)
- [x] `log_event(...)` called with `request_ids`/`accepted_count`, never the raw request body
- [x] Existing `packages/api/tests/reimbursement/create/test_route.py` and `test_integration.py` pass unmodified
- [x] Gate check passes: `uv run pytest packages/api packages/shared -m "not integration"`
- [x] Test count recorded (no silent deletions vs. pre-task count)

**Tests**: none (per Test Coverage Matrix)
**Gate**: quick (api/shared)

---

### T4: `update/route.py` — log the recorded decision, durably log a decision-write failure

**What**: Add `logger = logging.getLogger(__name__)`; wrap the existing `approve_reimbursement(...)`/`reject_reimbursement(...)` call only (not the post-commit re-fetch, which keeps its own existing `RuntimeError` handling untouched) in `try/except Exception`, writing `failure_log.write(load_config().failure_log, {"event": DECISION_WRITE_FAILED_EVENT, "uuid": str(uuid), "approved_by": review.approved_by, "error": sanitize(exc)})` then re-raising. On success, call `log_event(logger, logging.INFO, DECISION_RECORDED_EVENT, uuid=str(uuid), status=row["status"], approved_by=review.approved_by)`.
**Where**: `packages/api/src/api/reimbursement/update/route.py`
**Depends on**: T1
**Reuses**: `row["status"]` from the already-returned `asyncpg.Record`; `failure_log.write`/`sanitize` as-is
**Requirement**: TRC-02, TRC-03

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] Module-level `DECISION_RECORDED_EVENT`/`DECISION_WRITE_FAILED_EVENT` constants added
- [x] Success path logs `uuid`, `status`, `approved_by` via `log_event`
- [x] Failure path (any exception from `approve_reimbursement`/`reject_reimbursement`) writes a CRITICAL `failure_log` record with `uuid`+`approved_by`+sanitized error, then re-raises — response contract (still 500 via the existing catch-all) unchanged
- [x] The existing post-commit re-fetch `RuntimeError` path is untouched — the new `try/except` wraps only the decision-write call
- [x] Existing `packages/api/tests/reimbursement/update/test_route.py` and `test_validation.py` pass unmodified
- [x] Gate check passes: `uv run pytest packages/api packages/shared -m "not integration"`
- [x] Test count recorded (no silent deletions vs. pre-task count)

**Tests**: none (per Test Coverage Matrix)
**Gate**: quick (api/shared)

---

### T5: `list/route.py` — log the applied filter and result count

**What**: Add `logger = logging.getLogger(__name__)`; before `return`, call `log_event(logger, logging.INFO, LIST_QUERIED_EVENT, status_filter=status or "none", result_count=len(rows))`.
**Where**: `packages/api/src/api/reimbursement/list/route.py`
**Depends on**: T1
**Reuses**: `status`/`rows`, already in scope
**Requirement**: TRC-05

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] Module-level `LIST_QUERIED_EVENT = "reimbursement.list_queried"` constant added
- [x] `status_filter` logs the literal string `"none"` when `status` is unset (edge case from spec.md), never `""`/`null`
- [x] Existing `packages/api/tests/reimbursement/list/*.py` pass unmodified
- [x] Gate check passes: `uv run pytest packages/api packages/shared -m "not integration"`
- [x] Test count recorded (no silent deletions vs. pre-task count)

**Tests**: none (per Test Coverage Matrix)
**Gate**: quick (api/shared)

---

### T6: `get/route.py` — log the query outcome

**What**: Add `logger = logging.getLogger(__name__)`; wrap `await get_reimbursement(conn, uuid)` in `try/except ReimbursementNotFound`, logging `log_event(logger, logging.INFO, DETAIL_QUERIED_EVENT, uuid=str(uuid), found=False)` then re-raising; on success, `log_event(logger, logging.INFO, DETAIL_QUERIED_EVENT, uuid=str(uuid), found=True)`.
**Where**: `packages/api/src/api/reimbursement/get/route.py`
**Depends on**: T1
**Reuses**: N/A — first logging call site in this route
**Requirement**: TRC-06

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] Module-level `DETAIL_QUERIED_EVENT = "reimbursement.detail_queried"` constant added
- [x] Both the 404 (`found=False`) and 200 (`found=True`) paths log, response contract unchanged in both cases
- [x] Existing `packages/api/tests/reimbursement/get/test_route.py` passes unmodified
- [x] Gate check passes: `uv run pytest packages/api packages/shared -m "not integration"`
- [x] Test count recorded (no silent deletions vs. pre-task count)

**Tests**: none (per Test Coverage Matrix)
**Gate**: quick (api/shared)

**Commit**: `feat(api): log correlation id and outcome on every reimbursement route`

---

### T7: `api/errors.py` — catch-all includes the path uuid

**What**: `_unhandled_exception_handler` becomes `logger.exception("unhandled exception on %s %s uuid=%s", request.method, request.url.path, request.path_params.get("uuid"))`.
**Where**: `packages/api/src/api/errors.py`
**Depends on**: None
**Reuses**: The handler's existing `logger` (already module-level) and `Request` argument — zero new imports
**Requirement**: TRC-04

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] Log line includes `uuid=%s` sourced from `request.path_params.get("uuid")` (`None` on routes with no `uuid` path param, e.g. `POST`/`GET` list — harmless)
- [x] Existing `packages/api/tests/test_errors.py::it_returns_500_with_a_generic_message_for_unhandled_exceptions` passes unmodified (response contract untouched — only the log line changes)
- [x] Gate check passes: `uv run pytest packages/api packages/shared -m "not integration"`
- [x] Test count recorded (no silent deletions vs. pre-task count)

**Tests**: none (per Test Coverage Matrix)
**Gate**: quick (api/shared)

**Commit**: `feat(api): include path uuid in the catch-all exception log line`

---

### T8: Agent decision nodes — carry the reimbursement uuid on every log line

**What**: In each of the 5 node files, extend every existing `logger.info("FLOW: ...", ...)` call with `uuid=%s` in the format string and `state["reimbursement"].uuid` as an added argument. No new log calls, no new imports, no change to log content otherwise. One cohesive concern (the same one-line pattern repeated identically) across 5 files with no inter-file logic.
**Where**: `packages/reimbursement/src/reimbursement/agent/nodes/{validate,extract_fields,apply_policies,analysis,apply_agent_decision}.py`
**Depends on**: None
**Reuses**: `state["reimbursement"].uuid`, already threaded into every node's `State`
**Requirement**: TRC-07

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] Every existing `logger.info("FLOW: ...")` call in all 5 files carries `uuid=%s` + `state["reimbursement"].uuid`
- [x] No new log calls added, no log content otherwise changed, no new imports
- [x] Code-inspection check: `grep -c "uuid=%s" packages/reimbursement/src/reimbursement/agent/nodes/*.py` shows at least one match per file
- [x] Gate check passes (informational, pre-existing exceptions apply): `uv run pytest packages/reimbursement -m "not integration" --continue-on-collection-errors` — zero *new* failures vs. the documented pre-existing 1 failure / 4 collection errors
- [x] Test count recorded for whatever does collect (no silent deletions vs. pre-task count)

**Tests**: none (per Test Coverage Matrix — verified by code inspection)
**Gate**: quick (reimbursement, informational)

---

### T9: `agent.py::decide()` — attach the uuid to the LangFuse trace

**What**: The `config` dict passed to `graph.ainvoke` gains one key: `"metadata": {"langfuse_session_id": str(reimbursement.uuid)}`, alongside the untouched `"configurable"` and `"callbacks"` keys. `_langfuse_handlers()` itself — including its fallback branches — is not modified.
**Where**: `packages/reimbursement/src/reimbursement/agent/agent.py`
**Depends on**: None
**Reuses**: `_langfuse_handlers()`, `get_graph()` — unchanged
**Requirement**: TRC-08, TRC-09

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `decide()`'s `config` dict includes `metadata={"langfuse_session_id": str(reimbursement.uuid)}` alongside `configurable`/`callbacks`
- [x] `_langfuse_handlers()` and its fallback branches (`ImportError`, construction failure) are byte-identical to before this feature — diff confined to `decide()`'s `config` dict literal
- [x] Code-inspection check against `test_agent.py::DescribeDecide::it_threads_the_langfuse_callback_handlers_into_graph_ainvoke`'s existing assertions (`calls[0]["callbacks"]`, `calls[0]["configurable"]`) confirms the new `metadata` key is additive and does not invalidate either — that test file itself cannot currently run (pre-existing collection failure, see Pre-Execute Note)
- [x] Gate check passes (informational, pre-existing exceptions apply): `uv run pytest packages/reimbursement -m "not integration" --continue-on-collection-errors` — zero *new* failures vs. the documented pre-existing 1 failure / 4 collection errors
- [x] Test count recorded for whatever does collect (no silent deletions vs. pre-task count)

**Tests**: none (per Test Coverage Matrix — verified by code inspection)
**Gate**: quick (reimbursement, informational)

**Commit**: `feat(reimbursement): attach reimbursement uuid to every LangFuse trace`

---

## Phase Execution Map

```
Phase 1 → Phase 2 → Phase 3

Phase 1:  T1 ──→ T2
Phase 2:  T3 ──→ T4 ──→ T5 ──→ T6
Phase 3:  T7 ──→ T8 ──→ T9
```

Execution is strictly sequential — 9 tasks total, marginally over the ~8-task
single-batch threshold. This session runs fully autonomously (no user
available for the offer-then-confirm sub-agent gate) — every file involved
is already read and each task is a small, low-risk, mostly-mechanical edit
(1-4 line additions to existing call sites), so the reasonable autonomous
call is to execute all 9 inline in the main window rather than pay
sub-agent-dispatch coordination overhead for work this size. Recorded as a
judgment call, not a skipped step.

---

## Task Granularity Check

| Task | Scope | Status |
| --- | --- | --- |
| T1: `shared.logging` helper | 1 file | ✅ Granular |
| T2: `api/main.py` basicConfig + test | 1 file (+ 1 test file, cohesive: the config change and its own verification) | ✅ Granular |
| T3: `create/route.py` logging | 1 file | ✅ Granular |
| T4: `update/route.py` logging + failure log | 1 file (2 cohesive changes: success log + failure-path durable record) | ✅ Granular |
| T5: `list/route.py` logging | 1 file | ✅ Granular |
| T6: `get/route.py` logging | 1 file | ✅ Granular |
| T7: `errors.py` catch-all uuid | 1 file | ✅ Granular |
| T8: 5 agent node files, uuid logging | 5 files (one cohesive concern: the identical one-line `uuid=%s` addition, no per-file logic variation) | ✅ Granular |
| T9: `agent.py` LangFuse metadata | 1 file | ✅ Granular |

---

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| --- | --- | --- | --- |
| T1 | None | None | ✅ Match |
| T2 | None | T1 → T2 (phase-order arrow only) | ✅ Match (task body: "Depends on: None") |
| T3 | T1 | T2 → T3 (diagram shows sequential order; body's real dependency, T1, is already satisfied earlier) | ✅ Match |
| T4 | T1 | T3 → T4 (phase-order arrow; body's real dependency, T1, already satisfied) | ✅ Match |
| T5 | T1 | T4 → T5 (phase-order arrow; body's real dependency, T1, already satisfied) | ✅ Match |
| T6 | T1 | T5 → T6 (phase-order arrow; body's real dependency, T1, already satisfied) | ✅ Match |
| T7 | None | T6 → T7 (phase-order arrow only) | ✅ Match (task body: "Depends on: None") |
| T8 | None | T7 → T8 (phase-order arrow only) | ✅ Match (task body: "Depends on: None") |
| T9 | None | T8 → T9 (phase-order arrow only) | ✅ Match (task body: "Depends on: None") |

**Note on phase-order-only arrows**: T2, T7, T8, T9's diagram arrows reflect execution order within their phase, not a data dependency — each touches files disjoint from its phase neighbor. This is intentional sequencing, not a cross-check violation.

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| --- | --- | --- | --- | --- |
| T1: `shared/logging.py` | `shared.logging.log_event()` | none | none | ✅ OK |
| T2: `api/main.py` | `api` startup logging config | unit | unit | ✅ OK |
| T3: `create/route.py` | `api` create route | none | none | ✅ OK |
| T4: `update/route.py` | `api` update route | none | none | ✅ OK |
| T5: `list/route.py` | `api` list route | none | none | ✅ OK |
| T6: `get/route.py` | `api` get route | none | none | ✅ OK |
| T7: `errors.py` | `api` catch-all handler | none | none | ✅ OK |
| T8: agent nodes ×5 | `reimbursement` agent nodes uuid logging | none | none | ✅ OK |
| T9: `agent.py` | `reimbursement` agent LangFuse correlation | none | none | ✅ OK |

All ✅ — no restructuring needed. Every `none` row is directly traceable to
spec.md's own Out of Scope decision (TRC-01..10), not a gap in matrix
generation.

---

## Tools for Execution

Direct code editing throughout — no MCP, no skill beyond `tlc-spec-driven`
itself for the Execute flow. No UI/API-design surface, no library-version
uncertainty to resolve via `context7` (this feature adds no new dependency).
