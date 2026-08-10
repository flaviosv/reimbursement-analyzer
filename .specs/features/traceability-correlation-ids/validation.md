# Traceability Correlation IDs Validation

**Date**: 2026-08-09
**Spec**: `.specs/features/traceability-correlation-ids/spec.md`
**Diff range**: `main` (adc4309) .. `feature/11-traceability-correlation-ids` (c416ab5) — commits `8bbecc9`..`c416ab5`
**Verifier**: independent sub-agent (author ≠ verifier)

**Scope note**: Per spec.md's Out of Scope table ("Dedicated automated tests for the logging/correlation-id behavior this feature adds (TRC-01..10)"), verification for TRC-01..10 is by **code inspection** (cited `file:line`) plus keeping the existing full suite green — not new test assertions. This is an explicit, twice-confirmed user decision, not a gap. TRC-11 is the one exception and is tested (`packages/api/tests/test_main.py::DescribeLoggingSetup`).

---

## Task Completion

| Task | Status  | Notes |
| ---- | ------- | ----- |
| T1   | ✅ Done | `shared.logging.log_event()` — `packages/shared/src/shared/logging.py` |
| T2   | ✅ Done | `api/main.py` `logging.basicConfig(level=logging.INFO)` + `test_main.py::DescribeLoggingSetup` |
| T3   | ✅ Done | `create/route.py` — TRC-01 |
| T4   | ✅ Done | `update/route.py` — TRC-02, TRC-03 |
| T5   | ✅ Done | `list/route.py` — TRC-05 |
| T6   | ✅ Done | `get/route.py` — TRC-06 |
| T7   | ✅ Done | `errors.py` — TRC-04 |
| T8   | ✅ Done | 5 agent node files — TRC-07 (co-located `test_validate.py` fixture update reviewed, coherent — see Code Quality) |
| T9   | ✅ Done | `agent.py::decide()` — TRC-08, TRC-09 |

All 9 tasks committed, one commit per task, in dependency order (`8bbecc9` T1 → `9bcd741` T2 → `b300a96` T3 → `151788e` T4 → `e2e986c` T5 → `e30e4ad` T6 → `d482f21` T7 → `ea898f5` T8 → `4a7cdfd` T9), plus a closing `docs(specs)` commit (`c416ab5`).

---

## Spec-Anchored Acceptance Criteria

### P1: Write-path api logging

| Criterion (WHEN X THEN Y) | Spec-defined outcome | Evidence | Result |
| --- | --- | --- | --- |
| TRC-01: WHEN `POST` successfully publishes an accepted batch THEN api logs at INFO via `log_event`, event with `request_ids`+`accepted_count`, never the raw body | INFO-level `log_event` call, fields exactly `request_ids`, `accepted_count`; no `raw`/body field | `packages/api/src/api/reimbursement/create/route.py:56-62` — `log_event(logger, logging.INFO, BATCH_ACCEPTED_EVENT, request_ids=request_ids, accepted_count=accepted_count)`, called after `publish(...)` succeeds, before `return`; `raw` is never passed | ✅ PASS |
| TRC-02: WHEN `PUT` commits an approve/reject decision THEN api logs at INFO via `log_event`, event with `uuid`+resulting `status`+`approved_by` | INFO-level `log_event`, fields `uuid`, `status` (from the `RETURNING *` row), `approved_by` | `packages/api/src/api/reimbursement/update/route.py:85-92` — `log_event(logger, logging.INFO, DECISION_RECORDED_EVENT, uuid=str(uuid), status=row["status"], approved_by=review.approved_by)` | ✅ PASS |
| TRC-03: WHEN `PUT`'s `approve_reimbursement`/`reject_reimbursement` raises THEN api writes a durable CRITICAL `failure_log` record (`uuid`+`approved_by`+sanitized error) before re-raising | `failure_log.write` (CRITICAL-tier per `failure_log.py:41-63`) called with exactly those 3 fields plus `event`, then `raise` (no swallow) | `packages/api/src/api/reimbursement/update/route.py:53-76` — `try:` wraps only the `approve_reimbursement`/`reject_reimbursement` call; `except Exception as exc:` calls `failure_log.write(load_config().failure_log, {"event": DECISION_WRITE_FAILED_EVENT, "uuid": str(uuid), "approved_by": review.approved_by, "error": sanitize(exc)})` then bare `raise` at line 76 | ✅ PASS |

### P2: Read-path api logging

| Criterion | Spec-defined outcome | Evidence | Result |
| --- | --- | --- | --- |
| TRC-05: WHEN `GET` list returns THEN api logs at INFO, applied status filter (or literal `"none"`) + result count | `status_filter` = literal string `"none"` when unset, never `""`/`null`; `result_count` = `len(rows)` | `packages/api/src/api/reimbursement/list/route.py:47-53` — `log_event(logger, logging.INFO, LIST_QUERIED_EVENT, status_filter=status or "none", result_count=len(rows))`. `status or "none"` yields the literal string for both `None` and `""` inputs, matching the edge case exactly | ✅ PASS |
| TRC-06: WHEN `GET /{uuid}` returns (200 or 404) THEN api logs `uuid` + whether found | Both `found=True` (200) and `found=False` (404, before re-raise) logged with `uuid=str(uuid)` | `packages/api/src/api/reimbursement/get/route.py:37-42` — `except ReimbursementNotFound:` logs `found=False` then `raise`; success path logs `found=True` at line 42, outside the `async with` | ✅ PASS |

### P1: Catch-all exception logging includes the uuid

| Criterion | Spec-defined outcome | Evidence | Result |
| --- | --- | --- | --- |
| TRC-04: WHEN any exception reaches `_unhandled_exception_handler` on a uuid-scoped route THEN the log line includes that `uuid` | `uuid=%s` sourced from `request.path_params.get("uuid")`, alongside existing method+path | `packages/api/src/api/errors.py:87-92` — `logger.exception("unhandled exception on %s %s uuid=%s", request.method, request.url.path, request.path_params.get("uuid"))` | ✅ PASS |

### P1: Agent node uuid logging

| Criterion | Spec-defined outcome | Evidence | Result |
| --- | --- | --- | --- |
| TRC-07: WHEN any of the 5 nodes emits an existing log line THEN it includes `state["reimbursement"].uuid` | Every existing `logger.info("FLOW: ...")` call in all 5 files carries `uuid=%s` + the value | `validate.py:17-24`, `extract_fields.py:35-51`, `apply_policies.py:28,58-61,64,87`, `analysis.py:35-46`, `apply_agent_decision.py:22,45` — every one of the 7 total `logger.info("FLOW: ...")` call sites across the 5 files now carries `uuid=%s`/`uuid` sourced from `state["reimbursement"].uuid` (verified line-by-line against the diff; `grep -c "uuid=%s"` per file ≥1) | ✅ PASS |

### P1: LangFuse trace correlation

| Criterion | Spec-defined outcome | Evidence | Result |
| --- | --- | --- | --- |
| TRC-08: WHEN `agent.decide()` invokes the graph THEN `config` includes `metadata={"langfuse_session_id": str(reimbursement.uuid)}` alongside `callbacks` | `config` dict literal gains exactly this key, `callbacks`/`configurable` untouched | `packages/reimbursement/src/reimbursement/agent/agent.py:149` — `"metadata": {"langfuse_session_id": str(reimbursement.uuid)}` added as a third key in the same `config={...}` literal that still has `"configurable"` and `"callbacks"` | ✅ PASS |
| TRC-09: WHEN the LangFuse handler cannot be constructed THEN the existing `failure_log` fallback stays unchanged | `_langfuse_handlers()` byte-identical; diff confined to `decide()`'s `config` dict | `git diff main..HEAD -- packages/reimbursement/src/reimbursement/agent/agent.py` shows exactly one added line (the `metadata` key); `_langfuse_handlers()` function body has zero diff lines | ✅ PASS |

### P1: api's own INFO-level logs must actually emit

| Criterion | Spec-defined outcome | Evidence | Result |
| --- | --- | --- | --- |
| TRC-11: WHEN api's process starts THEN `logging.basicConfig(level=logging.INFO)` has been called | Exact call `logging.basicConfig(level=logging.INFO)`, module scope, before request-handling code | `packages/api/src/api/main.py:25` (before `app = FastAPI(...)` at line 41) — **and** `packages/api/tests/test_main.py:70-85` `DescribeLoggingSetup::it_calls_basic_config_at_info_level_on_module_import` — `assert calls == [{"level": logging.INFO}]` after `importlib.reload(main_module)` with `logging.basicConfig` monkeypatched to record calls. Assertion targets the exact value (`level=logging.INFO`), not just "was called" | ✅ PASS |

### P2: Shared JSON log-event helper

| Criterion | Spec-defined outcome | Evidence | Result |
| --- | --- | --- | --- |
| TRC-10: WHEN any new api log line (P1/P2) is emitted THEN it's built via `shared.logging.log_event(logger, level, event, **fields)`, one JSON object per call | Helper defined per contract; all 4 new route call sites use it, none hand-roll `json.dumps` | `packages/shared/src/shared/logging.py:19-32` defines `log_event`; `create/route.py:56`, `update/route.py:85` (+ `failure_log.write` for the failure path, which is a separate, already-existing/tested mechanism per design.md, not a `log_event` call site), `list/route.py:47`, `get/route.py:40,42` all import `from shared.logging import log_event` and call it — `grep -rn "json.dumps" packages/api/src/api/reimbursement/` returns zero hits in the 4 touched route files | ✅ PASS |

**Status**: ✅ All 11 ACs covered, 0 spec-precision gaps.

---

## Edge Cases

- [x] `PUT`'s 422 (body shape validation failure) fires no `approved_by`/`uuid` decision log — `validate_review(raw)` (the source of a 422) runs at `update/route.py:48`, before the `async with`/`try` block that contains every new log/failure-log call; a raised exception there skips all of them. Confirmed by code path, not reachable otherwise.
- [x] `GET` list with no `status` filter logs the literal string `"none"` — `status or "none"` (`list/route.py:51`) yields `"none"` for both `None` and `""`.
- [x] Retried decision (same uuid, graph re-invoked) — `langfuse_session_id` is `str(reimbursement.uuid)`, deterministic per-uuid and stable across invocations by construction (the uuid is read from the already-persisted row, not generated per-call).

---

## Discrimination Sensor

| # | File:line | Description | Killed? |
| - | --- | --- | --- |
| 1 | `packages/api/src/api/main.py:25` | `logging.basicConfig(level=logging.INFO)` → `logging.basicConfig(level=logging.WARNING)` | ✅ Killed — `test_main.py::DescribeLoggingSetup::it_calls_basic_config_at_info_level_on_module_import` fails: `AssertionError: assert [{'level': 30}] == [{'level': 20}]` |
| 2 | `packages/api/src/api/reimbursement/get/route.py:40,42` | Swapped `found=True`/`found=False` on the two `log_event` calls | ❌ Survived (expected/accepted) — `packages/api/tests/reimbursement/get/` suite (8 tests) still 8 passed; no test asserts on the `found` field's value. Per spec.md's Out of Scope decision for TRC-01..10, this is an accepted gap, not a fix task. |
| 3 | `packages/reimbursement/src/reimbursement/agent/nodes/apply_policies.py:64` | Removed `uuid=%s`/`uuid` arg from the "rule fired" log line | ❌ Survived (expected/accepted) — `test_apply_policies.py` (18 tests) still 18 passed; existing node tests assert on log content unrelated to `uuid`. Same accepted-gap treatment, consistent with T8's task notes. |

All 3 mutations applied and reverted via direct file edit + `git checkout --`, confirmed via `git status --short` (clean) after each. Real working tree never left mutated.

**Sensor depth**: lightweight (3 targeted mutations)
**Result**: 1/3 killed by design (the one TRC-11-covered mutation); 2/3 survived by design (TRC-01..10 have no dedicated tests per spec.md's explicit, twice-confirmed decision) — **PASS** for this feature's gate, since spec.md's own scope decision is what predicts this exact outcome, not a defect discovered by surprise.

---

## Code Quality

| Principle | Status | Notes |
| --- | --- | --- |
| No features beyond what was asked | ✅ | 16 files changed, all map 1:1 to design.md's Integration Points / Components list |
| No abstractions for single-use code | ✅ | `log_event` is used at 4+ call sites; free-form `**fields` is a documented, deliberate choice (design.md Tech Decisions), not speculative flexibility |
| No unnecessary "flexibility" added | ✅ | — |
| Only touched files required for task | ✅ | Every touched file is named in design.md's Components/Integration Points sections |
| Didn't "improve" unrelated code | ⚠️ minor | `extract_fields.py` diff includes one incidental trailing-whitespace removal on an already-blank line (`packages/reimbursement/src/reimbursement/agent/nodes/extract_fields.py`, the blank line between the two existing statements) — zero behavioral effect, not worth a fix task |
| Matches existing patterns/style | ✅ | `*_EVENT` naming matches `publisher/processing.py`/`reimbursement/validation.py` exactly; `log_event`'s defensive try/except mirrors `failure_log.write` line-for-line (both fall back to a differently-named logger, never raise) |
| Would senior engineer approve? | ✅ | Yes — minimal, surgical, matches the codebase's existing hand-rolled-call convention |
| Tests map to acceptance criteria and are non-shallow (spot-check TRC-11) | ✅ | `DescribeLoggingSetup` reloads the real module and asserts the exact `level=logging.INFO` value via a genuine mutation-killing assertion (confirmed by sensor mutation #1) |
| Spec-anchored outcome check (asserted values match spec) | ✅ | TRC-11's assertion targets the exact spec-defined value; TRC-01..10 verified by code inspection per spec.md's own decision |
| Per-layer Coverage Expectation met | ✅ | Matches Test Coverage Matrix exactly — `none` for TRC-01..10 (by spec decision), unit for TRC-11 |
| Every test in scope maps to a spec AC — no unclaimed tests | ✅ | Only new test is `DescribeLoggingSetup` (TRC-11); `test_validate.py`'s fixture change is not a new test, just an update to satisfy T8's new required `state["reimbursement"]` field — reviewed below |
| Documented guidelines followed | ✅ | `docs/codebase/TESTING.md` (cited directly by tasks.md's Test Coverage Matrix and Gate Check Commands) |

**T8/test_validate.py coherence check**: `validate.py`'s node now reads `state["reimbursement"].uuid` unconditionally (`packages/reimbursement/src/reimbursement/agent/nodes/validate.py:17-18`), making `state["reimbursement"]` a hard requirement where it was previously absent from the node's own logic. `test_validate.py`'s `_state()` fixture (`packages/reimbursement/tests/test_validate.py:15-19`) was updated to supply `"reimbursement": Reimbursement(uuid=_UUID, original_payload={})`. All 4 original `DescribeValidate` behaviors are still asserted unchanged (missing-value routing, missing-date routing, both-missing routing, no-missing-fields passthrough) — confirmed by diff: only the fixture gained a key, no assertion was weakened, removed, or added. This is a required co-located fix for T8, not scope creep.

---

## Gate Check

- **Gate 1** (`uv run pytest packages/api packages/shared -m "not integration" -q`): **350 passed, 0 failed, 2 deselected**
- **Gate 2** (`uv run pytest packages/reimbursement -m "not integration" --continue-on-collection-errors -q`): **79 passed, 1 failed, 4 collection errors** — verified identical on `main` (adc4309) via detached-checkout comparison (same 1 failed / 79 passed / 4 errors, byte-identical failure/error set: `test_config.py::DescribeAgentConfig::it_defaults_the_ollama_settings_when_unset` + `test_agent.py`/`test_analysis.py`/`test_extract_fields.py`/`test_integration.py` collection errors, all owned by the concurrently in-flight `agent-model-config` feature). **Zero new failures introduced by this feature.**
- **Gate 3** (`uv run pytest -m "not integration" --continue-on-collection-errors -q`, whole workspace): **508 passed, 1 failed, 6 deselected, 4 collection errors** — same pre-existing exceptions, zero new failures.
- **Test count before feature (main, packages/reimbursement)**: 79 passed + 1 failed + 4 errors (baseline)
- **Test count after feature**: same collected count for `packages/reimbursement` (no tests added there — `test_validate.py`'s change is a fixture update, not a new test); `packages/api` gained 1 new test (`DescribeLoggingSetup`)
- **Delta**: +1 test (`packages/api/tests/test_main.py::DescribeLoggingSetup::it_calls_basic_config_at_info_level_on_module_import`)
- **Skipped tests**: none
- **Failures**: none introduced; 1 pre-existing (`test_config.py`) + 4 pre-existing collection errors, both confirmed byte-identical to `main`, both owned by sibling feature `agent-model-config`

Working tree confirmed clean (`git status --short` empty) both before and after all gate runs and sensor mutations — no tracked file left modified.

---

## Fix Plans

None. No gaps found requiring a fix task. The 2 survived sensor mutations (#2, #3) are the explicit, already-confirmed consequence of spec.md's Out of Scope decision for TRC-01..10 — not defects in this feature's implementation.

---

## Requirement Traceability Update

| Requirement | Previous Status | New Status |
| --- | --- | --- |
| TRC-01 | Design → Pending | ✅ Verified |
| TRC-02 | Design → Pending | ✅ Verified |
| TRC-03 | Design → Pending | ✅ Verified |
| TRC-04 | Design → Pending | ✅ Verified |
| TRC-05 | Design → Pending | ✅ Verified |
| TRC-06 | Design → Pending | ✅ Verified |
| TRC-07 | Design → Pending | ✅ Verified |
| TRC-08 | Design → Pending | ✅ Verified |
| TRC-09 | Design → Pending | ✅ Verified |
| TRC-10 | Design → Pending | ✅ Verified |
| TRC-11 | Design → Pending | ✅ Verified |

---

## Summary

**Overall**: ✅ Ready

**Spec-anchored check**: 11/11 ACs matched spec outcome, 0 spec-precision gaps
**Sensor**: 1/1 expected-kill mutation killed; 2/2 expected-survive mutations survived (both accepted per spec.md's explicit TRC-01..10 no-test decision)
**Gate**: 3/3 gate commands pass with zero new failures (pre-existing baseline noise — 1 failure + 4 collection errors in `packages/reimbursement`, owned by sibling feature `agent-model-config` — confirmed byte-identical on `main` and on this branch)

**What works**: All 11 TRC requirements implemented exactly as designed — write-path/read-path api logging, catch-all uuid enrichment, agent-node uuid propagation, LangFuse session correlation, and the `api` startup logging fix that makes all of it actually emit. `shared.logging.log_event()` is adopted consistently at every new call site with no hand-rolled `json.dumps` left behind. The one dedicated test (TRC-11) is non-shallow and mutation-kill-confirmed.

**Issues found**: None blocking. One cosmetic, zero-risk incidental whitespace removal in `extract_fields.py` (not worth a fix task).

**Next steps**: None — feature is ready to ship as-is.
