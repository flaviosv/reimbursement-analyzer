# Agent — Decide `Reimbursement` Validation

**Date**: 2026-08-09 (round 1); updated 2026-08-09 (round 2 — re-check of 3 fix commits)
**Spec**: `.specs/features/agent-decide-reimbursement/spec.md`
**Diff range**: `main...HEAD` (branch `feature/9-reimbursement_agent`; merge-base `5969f21`)
**Verifier**: independent sub-agent (author ≠ verifier); round 2 is a different Verifier instance than round 1

---

## Round 2 — Re-verification of 3 Fix Commits

Round 1 FAILed with 3 gaps (AGD-13/16 self-referential assertions, AGD-24 fallback
not mirroring `failure_log`, AGD-26 unfiltered PII in the extraction prompt).
Round 2 re-checks **only** those 3 gaps plus a general regression sanity check —
the other 23 ACs were not re-audited (round 1 already covered them and no code
in their paths changed).

Fix commits reviewed: `6193185` (AGD-24), `b81ddfc` (AGD-26), `559bf75` (AGD-13/16).

**Verdict: PASS ✅** — all 3 gaps are closed with direct evidence; no regressions.

| Gap (round 1) | Fix commit | Evidence (round 2) | Result |
| --- | --- | --- | --- |
| AGD-24 fallback didn't mirror `failure_log`; unasserted | `6193185` | `packages/reimbursement/src/reimbursement/agent/agent.py:110-118,120-132` now calls `failure_log.write(load_config().failure_log, {...})` in both the `ImportError` and handler-construction-failure branches; `packages/reimbursement/tests/test_langfuse.py:20-36` — `it_writes_a_failure_log_record_through_the_durable_fallback_when_langfuse_is_not_installed` monkeypatches `agent.failure_log.write`, asserts it was called exactly once with `config == load_config().failure_log` and `record["event"] == agent.LANGFUSE_FALLBACK_EVENT`, `record["reason"] == "langfuse not installed"` | ✅ Closed |
| AGD-26 full raw payload (incl. `submitted_by`) reached the extraction prompt | `b81ddfc` | `packages/reimbursement/src/reimbursement/agent/nodes/extract_fields.py:23,44-47` allow-lists the prompt payload to `("claimed_amount_brl", "claimed_category", "raw_ocr_text")` before `json.dumps`; `packages/reimbursement/tests/test_extract_fields.py:77-89` — `it_never_includes_submitted_by_in_the_rendered_prompt` renders the actual `model.calls[0]` message content and asserts `_PAYLOAD["submitted_by"] not in rendered` — proves the LLM-facing payload excludes it, not just that a dict was filtered in isolation. `analysis.py:33-34` (the feature's other LLM step) reads only `state["extracted"]` (never the raw payload), so it was already PII-clean pre-fix (per AGD-02, round 1) | ✅ Closed |
| AGD-13/16 self-referential `decision_reason` assertions | `559bf75` | `packages/reimbursement/tests/test_apply_policies.py:81-83` — ceiling case now asserts `"200" in result["decision_reason"]` in addition to the existing echo-check; `:95-97` — floor case asserts `"2000.01" in result["decision_reason"]`. Cross-checked against `apply_policies.py:48-51` (`f"auto-approve rule: resolved value {value} is within the {AUTO_APPROVE_CEILING} ceiling"`) and `:53-56` (`f"mandatory human-review rule: resolved value {value} exceeds the {HUMAN_REVIEW_FLOOR} floor"`) — the asserted substrings are the actual value/rule text spec.md's P1 stories (lines 217-218, 243-244) require ("decision_reason SHALL state the rule and the resolved value"), not incidental matches | ✅ Closed |

**AGD-23 (LangFuse tracing itself, not just the fallback)**: remains intentionally
deferred, as scoped. `agent.py:97-108`'s `# SPEC_DEVIATION` comment still honestly
states `langfuse` is not an installed dependency and that adding it is "a follow-up
pending an explicit decision, not a silent omission" — round 2 confirms this
deferral is still documented, not silently implied as resolved by the AGD-24 fix.
AGD-23's row below is unchanged from round 1 (still ❌ GAP, by explicit scope
decision, not a new finding).

---

## Task Completion

| Task | Status  | Notes |
| ---- | ------- | ----- |
| T1   | ✅ Done | `Reimbursement` model + `from_record`, `packages/shared/src/shared/models.py:104-118` |
| T2   | ✅ Done | `update_decision` replaces `update_human_review`, `packages/shared/src/shared/reimbursement/repository.py:146-152` |
| T3   | ✅ Done | `apply_decision` use case, `packages/shared/src/shared/reimbursement/use_cases/apply_decision.py` |
| T4   | ✅ Done | `escalate_existing` delegates to `apply_decision`, `send_human_review.py:54-61`; regression suite green |
| T5   | ✅ Done | `AgentConfig.ollama_model`/`ollama_base_url`, `packages/reimbursement/src/reimbursement/config.py:17-23,42-44` |
| T6   | ✅ Done | `langchain-ollama>=1.1.0` in `packages/reimbursement/pyproject.toml` |
| T7   | ✅ Done | `State`/`ExtractedFields`/`Node` Protocol, `packages/reimbursement/src/reimbursement/schema.py` |
| T8   | ✅ Done | `ExtractFields` node + placeholder prompt |
| T9   | ✅ Done | `Validate` node + `route_after_validate` |
| T10  | ✅ Done | `ApplyPolicies` node, constructor-injected `apply_decision` |
| T11  | ✅ Done | `Analysis` node + placeholder prompt |
| T12  | ✅ Done | `ApplyAgentDecision` node |
| T13  | ✅ Done | `agent.py` wiring, `get_graph()`/`build_graph()` singleton, 6 routing tests + 2 singleton tests |
| T14  | ✅ Done | `_resolve`/`_decide` invoke `agent.decide()`, R-011 interim floor (`failure_log` + `MessageOutcome.LOGGED`, no propagation) |
| T15  | ✅ Done | Real-Postgres integration test, fake models only, `apply_decision` real |

All 15 tasks show `[x]` on every "Done when" line in `tasks.md`; none partial or blocked.

---

## Spec-Anchored Acceptance Criteria

| Criterion (WHEN X THEN Y) | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --- | --- | --- |
| AGD-01 extraction runs first, unconditionally | one LLM call, before any deterministic check | `agent.py:47-48` (`START→extract_fields→validate`); `test_extract_fields.py:31-49` | ✅ PASS |
| AGD-02 later steps use extraction output as-is | no re-derivation from raw payload | `apply_policies.py:32-33` reads only `state["extracted"]`; `test_apply_policies.py:18-24` injects `extracted` directly, output matches input on every case | ✅ PASS |
| AGD-03 exactly one extraction call, never batched | `len(model.calls) == 1` | `test_extract_fields.py:48` — `assert len(model.calls) == 1` | ✅ PASS |
| AGD-04 unconditional even with `claimed_amount_brl` present | call still happens | `test_extract_fields.py:51-66` — `it_invokes_the_model_unconditionally_even_when_claimed_amount_brl_is_present` | ✅ PASS |
| AGD-05 reject fires at >90 days, overrides other rules | `auto-rejected` | `apply_policies.py:39-45`; `test_apply_policies.py:32-49` (91 days, value 5000) | ✅ PASS |
| AGD-06 reject reason states both dates | reason names `submitted_at` and `receipts_date` | `test_apply_policies.py:45-46` — `assert "2026-01-09" in ...; assert "2026-04-10" in ...` | ✅ PASS |
| AGD-07 reject checked first, ahead of ≤200/>2000 | reject wins when old AND >2000 | `apply_policies.py:39-58` (if/elif order); `test_apply_policies.py:61-70` — `it_rejects_ahead_of_the_mandatory_over_2000_human_review_rule` | ✅ PASS |
| AGD-08 ≤90 days or unresolved date → continue | reject does not fire; evaluation continues | `test_apply_policies.py:51-59` (exactly 90 days → `auto-approved`, not rejected); unresolved-date sub-case is Validate's own routing (AGD-10) — apply_policies never receives an unresolved date | ✅ PASS |
| AGD-09 value unresolved → human-review, never reject/approve | `human-review`, reason names `value` | `test_validate.py:16-27` | ✅ PASS |
| AGD-10 date unresolved → human-review | `human-review`, reason names `receipts_date` | `test_validate.py:29-36` | ✅ PASS |
| AGD-11 both resolved → proceed to policy application | `missing_fields=[]`, routes to `apply_policies` | `test_validate.py:49-55`, `:59-60` (`route_after_validate`) | ✅ PASS |
| AGD-12 ≤200 auto-approves, no guardrail call | `auto-approved`, `analysis` never invoked | `test_apply_policies.py:71-81`; `test_agent.py:125-153` — `assert len(fakes.analysis_model.calls) == 0` | ✅ PASS |
| AGD-13 ≤200 reason states rule + value | reason names the ceiling rule and the resolved value | `apply_policies.py:48-51`; `test_apply_policies.py:81-83` — `assert "200" in result["decision_reason"]` (round 2, commit `559bf75`) | ✅ PASS |
| AGD-14 >2000 mandatory human-review, no guardrail call | `human-review`, `analysis` never invoked | `test_apply_policies.py:83-93`; `test_agent.py:155-185` — `assert len(fakes.analysis_model.calls) == 0` | ✅ PASS |
| AGD-15 >2000 overrides any guardrail outcome | decided deterministically, guardrail never runs | Same as AGD-14 — `apply_policies.py`'s `elif` chain returns before `analysis` node is ever reached on this path | ✅ PASS |
| AGD-16 >2000 reason states rule + value | reason names the floor rule and the resolved value | `apply_policies.py:53-56`; `test_apply_policies.py:95-97` — `assert "2000.01" in result["decision_reason"]` (round 2, commit `559bf75`) | ✅ PASS |
| AGD-17 guardrail invoked only for 200<value≤2000, second call | `analysis` called exactly once, only in ambiguous zone | `test_agent.py:187-215`; `test_analysis.py:53-59` — `assert len(model.calls) == 1` | ✅ PASS |
| AGD-18 guardrail consistent → auto-approved | `status="auto-approved"` | `test_analysis.py:17-35` — `assert result["status"] == "auto-approved"` | ✅ PASS |
| AGD-19 guardrail contradictory/unsure → human-review | `status="human-review"`, reason = guardrail's own text | `test_analysis.py:37-51` — `assert result["decision_reason"] == "claimed amount contradicts the OCR total"` | ✅ PASS |
| AGD-20 guardrail checks intentionally unspecified | no fixed check logic required by spec | N/A by design — spec explicitly leaves this open | ⚠️ Spec-precision gap (by design, not an implementation defect) |
| AGD-21 `decision_reason` non-null on every outcome | non-null on reject/≤200/>2000/ambiguous×2/missing-field | `test_agent.py` — every `DescribeGraphRouting` case asserts `result["decision_reason"] is not None` | ✅ PASS |
| AGD-22 persist is the outcome's own last action, every path | every one of the 6 routing paths calls `apply_decision` before `END` | `test_agent.py` — each case asserts `fakes.apply_policies_decision.calls[...]` or `fakes.apply_agent_decision_decision.calls[...]` fired with the final `status`/`decision_reason` | ✅ PASS |
| AGD-23 LangFuse trace on every LLM invocation | invocation recorded via LangFuse | `agent.py:88-113` (`_langfuse_handlers`) — **no dependency installed** (`langfuse` absent from `pyproject.toml`, self-flagged via `# SPEC_DEVIATION` at `agent.py:92-101`), **no test asserts this function's behavior directly** (only incidentally executed, unasserted, inside `test_integration.py`'s `DescribeTheDecisionGraph` case) | ❌ GAP — no evidence |
| AGD-24 file-log fallback when LangFuse unreachable, mirrors `failure_log` pattern | fallback recorded the same way `failure_log.write` records a failure | `agent.py:110-118,120-132` now calls `failure_log.write(load_config().failure_log, {"event": LANGFUSE_FALLBACK_EVENT, ...})` in both fallback branches; `test_langfuse.py:20-36` — `it_writes_a_failure_log_record_through_the_durable_fallback_when_langfuse_is_not_installed` monkeypatches `agent.failure_log.write` and asserts the call count, config, and record shape directly (round 2, commit `6193185`) | ✅ PASS |
| AGD-25 no-loss invariant on decision-stage failure | `decide()` failure caught, `failure_log` write, `MessageOutcome.LOGGED`, never propagates | `validation.py:163-170`; `test_validation.py:368-391` — asserts `LOGGED`, `reimbursement.decision_failed` in the log, row untouched, no exception propagates | ✅ PASS |
| AGD-26 (P2) prompts avoid PII beyond operational need | `submitted_by` (and similar) excluded from LLM prompts unless needed | `extract_fields.py:23,44-47` allow-lists the prompt payload to `("claimed_amount_brl", "claimed_category", "raw_ocr_text")` before `json.dumps`; `test_extract_fields.py:77-89` — `it_never_includes_submitted_by_in_the_rendered_prompt` asserts `_PAYLOAD["submitted_by"] not in rendered` against the actual rendered message content (round 2, commit `b81ddfc`); `analysis.py:33-34` (the other LLM step) reads only `state["extracted"]`, already PII-clean per AGD-02 | ✅ PASS |

**Status (round 2)**: ✅ All 3 round-1 gaps closed — 24/26 clean PASS. AGD-20 remains an intentional, spec-sanctioned open item (not a defect). AGD-23 remains an intentional, explicitly-scoped-out deferral (real `langfuse` dependency pending a separate user decision) — honestly documented via the `# SPEC_DEVIATION` comment in `agent.py`, not silently implied as resolved.
**Status (round 1, historical)**: ❌ Gaps present — 21/26 clean PASS, 2 spec-precision gaps (AGD-13, AGD-16), 3 hard GAPs (AGD-23, AGD-24, AGD-26).

---

## Discrimination Sensor

All three mutations were applied directly to the tracked working-tree files (no uncommitted changes existed beforehand), each proven to fail the targeted tests, then restored byte-for-byte from a pre-mutation backup in the session scratchpad; `git status --porcelain` was empty before, and empty again after, every mutation/restore cycle.

| Mutation | File:line | Description | Killed? |
| --- | --- | --- | --- |
| 1 | `packages/reimbursement/src/reimbursement/agent/nodes/apply_policies.py:46` | Flipped the ≤200 fast-path boundary: `value <= AUTO_APPROVE_CEILING` → `value < AUTO_APPROVE_CEILING` | ✅ Killed — `test_apply_policies.py::it_auto_approves_at_exactly_the_200_ceiling_via_apply_decision`, `test_agent.py::it_auto_approves_at_or_below_the_200_ceiling` both failed (2 failed) |
| 2 | `packages/reimbursement/src/reimbursement/validation.py:167-169` | R-011 interim floor: removed the `failure_log.write(...)` call from `_decide`'s `except` branch, leaving the exception caught-but-unlogged | ✅ Killed — `test_validation.py::DescribeDecideIntegration::it_catches_a_decide_failure_writes_the_failure_log_and_returns_logged_without_propagating` failed (1 failed) |
| 3 | `packages/reimbursement/src/reimbursement/agent/nodes/apply_policies.py:39-58` | Flipped reject-vs-`>2000` precedence: moved the `>2000` check ahead of the reject (>90-day) check | ✅ Killed — `test_apply_policies.py::it_rejects_a_receipt_91_days_old_regardless_of_value`, `::it_rejects_ahead_of_the_mandatory_over_2000_human_review_rule`, `test_agent.py::it_rejects_a_stale_receipt_regardless_of_value` all failed (3 failed) |

**Sensor depth**: lightweight (default tier)
**Result (round 1)**: 3/3 killed — ✅ PASS

Post-sensor confirmation: `uv run pytest -q -m "not integration"` → `478 passed, 11 deselected` on the fully-restored tree (identical to the pre-sensor run).

### Round 2 sensor (1 mutation, targeting the AGD-24 fix)

File backed up via temp copy (not `git stash`, since the working tree already
carried unrelated pre-existing modifications to `.specs/LESSONS.md`,
`.specs/lessons.json`, `docs/SCOPE.md` outside this feature's scope — stashing
would have swept those up too).

| Mutation | File:line | Description | Killed? |
| --- | --- | --- | --- |
| 4 | `packages/reimbursement/src/reimbursement/agent/agent.py:113-117` | Removed the `failure_log.write(...)` call from `_langfuse_handlers()`'s `ImportError` branch (the AGD-24 fix), leaving only the `logger.info` line | ✅ Killed — `test_langfuse.py::DescribeLangfuseHandlers::it_writes_a_failure_log_record_through_the_durable_fallback_when_langfuse_is_not_installed` failed (`assert 0 == 1`); `it_returns_no_handlers_when_langfuse_is_not_installed` still passed, confirming the mutation was scoped to the new assertion only |

File restored byte-for-byte from the temp copy immediately after; `git status --porcelain` confirmed identical (only the same pre-existing unrelated modifications, none from this session) before and after.

**Result (round 2)**: 1/1 killed — ✅ PASS

---

## Code Quality

| Principle | Status |
| --- | --- |
| Minimum code | ✅ — every new file/class maps 1:1 to a task; no speculative abstractions |
| Surgical changes | ✅ — `update_human_review`→`update_decision` fully removed, not left alongside; `escalate_existing` refactor kept its exact signature |
| No scope creep | ✅ — `Stage` Literal `"decide"` widening explicitly deferred per `tasks.md`'s own scope note, not added |
| Matches patterns | ✅ — `@lru_cache(maxsize=1)` singleton mirrors `load_config()` (AD-023); `Node` Protocol mirrors the dataclass-first, non-ABC style elsewhere in the codebase |
| Spec-anchored outcome check (asserted values match spec) | ✅ (round 2) — 24/26 solid; AGD-13/AGD-16 fixed in `559bf75` (round 1 finding: self-referential `fake.calls == [(..., result["decision_reason"])]` assertion, now pins `"200"`/`"2000.01"`) |
| Per-layer Coverage Expectation met (domain 1:1 ACs; routes happy+edge+error) | ✅ (round 2) — all 5 nodes and the graph wiring have 1:1 branch coverage; AGD-24/26 fixed in `6193185`/`b81ddfc`; AGD-23 remains an intentional, documented deferral, not a coverage gap |
| Every test maps to a spec requirement — no unclaimed tests | ✅ — every new `it_*` traces to an AGD-xx or a named regression concern (T4's fake-shape adaptation) |
| Documented guidelines followed | `docs/codebase/CONVENTIONS.md` not present in this repo at time of review; root `CLAUDE.md` (traceability is a hard requirement — `FLOW:` log-line discipline followed on all 5 nodes) and `.specs/STATE.md` (AD-023 singleton, AD-025 use-case placement) — both followed |

**Spot-check (non-shallow, one story in depth):** P1 "A value ≤ 200 auto-approves deterministically" — `ApplyPolicies.__call__` (`apply_policies.py:27-74`) correctly short-circuits before ever constructing/invoking the `Analysis` node's dependencies; verified both at the unit level (`test_apply_policies.py`) and the full-graph level (`test_agent.py`), with an explicit `assert len(fakes.analysis_model.calls) == 0` proving no wasted LLM billing — this is the story's own MVP-critical claim (`spec.md`'s Goals: "costs exactly one LLM call") and it holds.

---

## Edge Cases

- [x] Exactly `2000` stays in the ambiguous zone, not forced to `human-review` — `test_apply_policies.py:95-102`
- [x] Exactly `90` days does not reject — `test_apply_policies.py:51-59`
- [x] Exactly `200` auto-approves (inclusive boundary) — `test_apply_policies.py:71-81`
- [x] Reject + `>2000` both apply → reject wins — `test_apply_policies.py:61-70`
- [x] Ghost `apply_decision` write (0 rows affected) → `persisted=False`, no exception, distinct log line — `test_apply_agent_decision.py:44-51`, `test_validation.py:342-366`
- [x] Multiple date-like values in `raw_ocr_text` — explicitly deferred by spec to prompt engineering, no business rule to test; N/A by design
- [x] `claimed_amount_brl=0` — no distinct floor exists in the boundary logic (`value <= 200` handles 0 identically to any small value); no dedicated test, but none required since spec explicitly states no special floor applies
- [x] AGD-26's PII-minimization edge (prompt should exclude `submitted_by`) — **handled (round 2, commit `b81ddfc`)**: `extract_fields.py:23,44-47` allow-lists the prompt payload; `test_extract_fields.py:77-89` asserts `submitted_by` absent from the rendered prompt

---

## Gate Check

### Round 1
- **Gate command**: `uv run pytest -q` (full, Docker running)
- **Result**: 489 passed, 0 failed, 0 skipped
- **Quick gate** (`uv run pytest -q -m "not integration"`): 478 passed, 11 deselected, 0 failed
- **Test count before feature**: 437 passed, 10 deselected (stated pre-feature baseline, old `src/` layout, unit-only scope — not directly comparable post-merge)
- **Test count after feature**: 478 passed / 11 deselected (unit-only), 489 passed total (full suite, post the unrelated package-namespacing merge)
- **Delta attributable to this feature** (measured directly off `git diff main...HEAD`, which — being a three-dot diff — already excludes the unrelated merge's own changes): **+42 new `it_*` test functions, 0 removed** (41 unit + 1 integration, matching the unit-deselected delta of 10→11)
- **Skipped tests**: none
- **Failures**: none

### Round 2 (post fix commits `6193185`, `b81ddfc`, `559bf75`)
- **Gate command**: `uv run pytest -q` (full, Docker running)
- **Result**: **492 passed, 0 failed, 0 skipped**
- **Delta vs round 1**: **+3 passed** (489 → 492) — matches the 3 fix commits' new test functions exactly: `test_langfuse.py` added 2 (`it_returns_no_handlers_when_langfuse_is_not_installed`, `it_writes_a_failure_log_record_through_the_durable_fallback...`), `test_extract_fields.py` added 1 (`it_never_includes_submitted_by_in_the_rendered_prompt`); `test_apply_policies.py`'s fix strengthened 2 existing assertions in place (no new test functions). Count only went up — no regressions, no silent deletions.
- **Skipped tests**: none
- **Failures**: none

---

## Fix Plans

### Fix 1: AGD-23/AGD-24 — LangFuse tracing has no dependency, no test, and its fallback doesn't mirror `failure_log`

- **Root cause**: `design.md`'s Tech Decisions table assumed `langfuse` would be an installed dependency; no task in `tasks.md` added it (T6 added only `langchain-ollama`), and no task's Done-when checklist asked for a direct test of `_langfuse_handlers()`. The implementer self-flagged this via a `# SPEC_DEVIATION` comment in `agent.py:92-101` rather than silently omitting it — but the AC itself remains unmet.
- **Fix task**: (a) Add `langfuse` as a real dependency (or explicitly re-scope AGD-23/24 to "fallback-only, until a follow-up adds the dependency" in a spec amendment); (b) add a direct unit test for `_langfuse_handlers()` covering the ImportError branch (already the only reachable branch today) and asserting its return value/log line, not just incidental execution; (c) route the fallback log through `shared.failure_log.write` (or an equally durable, dedicated channel) instead of a plain `logger.info` on the module logger, to actually match "mirrors the project's existing `failure_log` fallback pattern" as literally stated in `spec.md`.
- **Priority**: Major (P1 story, "mission-critical... audit" per spec.md Goals, but no runtime data is lost or corrupted — only under-verified/under-implemented tracing).
- **Status (round 2)**: ✅ **(b) and (c) applied**, commit `6193185`. `_langfuse_handlers()` now calls `failure_log.write` in both fallback branches (ImportError and handler-construction-failure), and `test_langfuse.py` directly asserts the call, its config, and its record shape. **(a) remains intentionally NOT applied** — `langfuse` is still not a real dependency; AGD-23 itself (the actual tracing, not the fallback) stays deferred pending a separate user decision, per the updated `# SPEC_DEVIATION` comment (`agent.py:97-108`), which explicitly still says so. This was the scoped intent per the round-1 fix task's own option (a) wording ("or explicitly re-scope") — confirmed honestly documented, not silently dropped.

### Fix 2: AGD-26 — PII minimization never implemented

- **Root cause**: AGD-26 (P2) is present in `spec.md`'s Requirement Traceability table but is never referenced by `design.md` or any task in `tasks.md` — it silently dropped out of scope between Specify and Design/Tasks. `extract_fields.py` passes `json.dumps(state["reimbursement"].original_payload)` — the full raw payload, including `submitted_by` (an email address) — into the extraction LLM prompt with no filtering.
- **Fix task**: Strip (or explicitly allow-list) the fields `ExtractFields`/`Analysis` actually need (`claimed_amount_brl`, `claimed_category`, `raw_ocr_text`, dates) before constructing the prompt payload; add a unit test asserting `submitted_by` (and any other PII field) never appears in the rendered prompt text.
- **Priority**: Minor (spec.md explicitly marks this P2, "not a hard-blocking MVP gate" — but it is currently 0% implemented, not partially implemented).
- **Status (round 2)**: ✅ Applied, commit `b81ddfc`. `extract_fields.py` now allow-lists `("claimed_amount_brl", "claimed_category", "raw_ocr_text")` before `json.dumps`; the new test renders the actual prompt messages and asserts `submitted_by`'s value is absent from that rendered text — not just that a dict was filtered in isolation. `analysis.py` (the feature's other LLM step) was already PII-clean (reads only `state["extracted"]`), confirmed by round-2 re-inspection.

### Fix 3: AGD-13/AGD-16 — self-referential decision_reason assertions

- **Root cause**: `test_apply_policies.py`'s ≤200 and >2000 cases assert `fake.calls == [(..., result["decision_reason"])]`, which echoes back whatever the code produced rather than pinning an expected substring (contrast with the reject-rule test, which does assert `"2026-01-09" in result["decision_reason"]`). The underlying implementation is correct (`apply_policies.py:48-51,53-56` do state the rule and value) — only the test is under-asserting.
- **Fix task**: Add `assert "200" in result["decision_reason"]` (ceiling case) and `assert "2000.01" in result["decision_reason"]` (floor case), matching the reject test's own stronger pattern.
- **Priority**: Minor.
- **Status (round 2)**: ✅ Applied exactly as specified, commit `559bf75`. Both substrings verified to actually match `apply_policies.py`'s real f-string output (`"...within the {AUTO_APPROVE_CEILING} ceiling"` / `"...exceeds the {HUMAN_REVIEW_FLOOR} floor"`), and to match spec.md's stated outcome (P1 stories, lines 217-218 and 243-244: "decision_reason SHALL state the rule and the resolved value"). No implementation change was needed or made.

---

## Requirement Traceability Update

| Requirement | Previous Status | New Status |
| --- | --- | --- |
| AGD-01 | Design | ✅ Verified |
| AGD-02 | Design | ✅ Verified |
| AGD-03 | Design | ✅ Verified |
| AGD-04 | Design | ✅ Verified |
| AGD-05 | Design | ✅ Verified |
| AGD-06 | Design | ✅ Verified |
| AGD-07 | Design | ✅ Verified |
| AGD-08 | Design | ✅ Verified |
| AGD-09 | Design | ✅ Verified |
| AGD-10 | Design | ✅ Verified |
| AGD-11 | Design | ✅ Verified |
| AGD-12 | Design | ✅ Verified |
| AGD-13 | Design | ✅ Verified (round 2, was: ⚠️ Spec-precision gap) |
| AGD-14 | Design | ✅ Verified |
| AGD-15 | Design | ✅ Verified |
| AGD-16 | Design | ✅ Verified (round 2, was: ⚠️ Spec-precision gap) |
| AGD-17 | Design | ✅ Verified |
| AGD-18 | Design | ✅ Verified |
| AGD-19 | Design | ✅ Verified |
| AGD-20 | Design | ⚠️ Spec-precision gap (by design) |
| AGD-21 | Design | ✅ Verified |
| AGD-22 | Design | ✅ Verified |
| AGD-23 | Design | ❌ Intentionally deferred (unchanged — real `langfuse` dependency pending separate user decision) |
| AGD-24 | Design | ✅ Verified (round 2, was: ❌ Needs Fix) |
| AGD-25 | Design | ✅ Verified |
| AGD-26 | Design | ✅ Verified (round 2, was: ❌ Needs Fix) |

---

## Summary

**Overall (round 2)**: ✅ Ready

**Spec-anchored check (round 2)**: 24/26 ACs matched spec outcome; 2 spec-precision gaps remain, both intentional/by-design — AGD-20 (guardrail's specific checks explicitly left open by spec) and AGD-23 (real `langfuse` dependency explicitly deferred pending a separate user decision, honestly documented via `# SPEC_DEVIATION`, not silently implied as resolved). Zero unintentional hard GAPs remain.

**Sensor (round 2)**: 1/1 new mutation killed (targeting the AGD-24 fix); round 1's 3/3 also still stand on unchanged code.

**Gate (round 2)**: 492 passed, 0 failed (full suite, Docker) — up from round 1's 489, +3 new tests from the fix commits, 0 removed, 0 regressions.

**What works**: All 3 round-1 gaps are closed with direct, evidence-or-zero test coverage:
- AGD-24: the LangFuse-unreachable fallback now durably records via `shared.failure_log.write` with the same config/record shape every other fallback in the codebase uses, and a direct test asserts the call, not just its execution.
- AGD-26: the extraction prompt is now allow-listed to the 3 fields the node actually reads, excluding `submitted_by`; a test proves this against the actual rendered prompt content, not an isolated dict-filter check. The guardrail/judge step was already PII-clean (operates on `extracted`, never the raw payload).
- AGD-13/AGD-16: the ceiling/floor `decision_reason` assertions now pin the actual expected value substrings (`"200"`, `"2000.01"`), matching both the real f-string output and spec.md's literal "SHALL state the rule and the resolved value" requirement — no longer a self-referential echo.

The entire deterministic decision core validated in round 1 (reject, ≤200, >2000, missing-field routing, the ambiguous-zone guardrail gate, the two persistence points, the R-011 interim failure floor, and the singleton graph-compilation proxy) is unchanged and remains precisely tested.

**Issues found**: None remaining that block the feature. AGD-23 (the actual LangFuse `CallbackHandler` wiring, as opposed to its fallback) stays an open, explicitly-scoped-out item pending a separate user decision on adding the `langfuse` dependency — not a defect, and not silently glossed over.

**Next steps**: None required to close this validation round. If/when the user decides to add `langfuse` as a real dependency, AGD-23 becomes a small follow-up task (wire `CallbackHandler()`, add a direct test for the success-path branch) — tracked as a known, intentional gap, not re-opened as a fix-plan item here.

**Lessons distilled (round 2)**: None new. Round 1 already recorded the 3 relevant lessons in `.specs/LESSONS.md` (L-020 — tracing dependency assumed by design.md but never added by tasks.md, SPEC_DEVIATION isn't a substitute for test coverage; L-021 — cross-check every spec.md requirement ID against design.md/tasks.md, a requirement absent from both drops out of scope silently; L-022 — assert a substring of expected reason content, not a self-referential echo). All 3 fixes in this round applied exactly the corrective pattern those lessons already prescribe, with no new failure mode surfacing — confirming the lessons were sufficient, not that they were absent.
