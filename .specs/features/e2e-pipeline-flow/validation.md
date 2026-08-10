# E2E Pipeline Flow & Dotenv-First Runtime Configuration Validation

**Date**: 2026-08-10
**Spec**: `.specs/features/e2e-pipeline-flow/spec.md`
**Diff range**: `04236cd4af4b3dc6d4df77f938585d77ef5bbb37..HEAD` (18 commits)
**Verifier**: independent sub-agent (author ≠ verifier)

---

## Task Completion

| Task | Status  | Notes |
| ---- | ------- | ----- |
| T1   | ✅ Done | `.env.sample` defines `LANGFUSE_SECRET_KEY=sk-lf-local-dev` alongside `LANGFUSE_INIT_PROJECT_SECRET_KEY` |
| T2   | ✅ Done | All 4 services mounted `./.env:/app/.env:ro`; `environment:` blocks pruned to topology allowlists; manual boot verification recorded in commit `ac94e6c` |
| T3   | ✅ Done | `test_dotenv_config_parity.py` — 4 tests, all passing |
| T4   | ✅ Done | `e2e` marker registered, `addopts` gains `-m "not e2e"`, `testpaths` gains `tests/e2e` — KNOWN GAP (CLI `-m` replaces `addopts`) flagged in the same commit (`ce33ecd`) and later corrected at the documentation/Gate-Command level |
| T5   | ✅ Done | `conftest.py` — `e2e_env`, `stack_ready`, `api_client`, `kafka_producer_config` all present |
| T6   | ✅ Done | `polling.py::wait_for_status` — bounded deadline, `AssertionError` naming last-seen status |
| T7   | ✅ Done | `payload_builders.py` — 3 builder functions, round-number/far-past-date steering |
| T8   | ✅ Done | `langfuse_helper.py::trace_exists_for_session` — SPEC_DEVIATION from design.md's planned accessor (`api.trace.list`/`sessions.get` 404 against this project's LangFuse v4 "events_only" deployment); re-pointed to `api.observations.get_many(filter=...)`, documented in-file with the verbatim deprecation response |
| T8b  | ✅ Done | `Analysis.__call__` builds `found_data`/`request_data`, PII-strips `submitted_by`; `get_analysis_prompt` renders both via `.replace()` |
| T9   | ✅ Done | `test_happy_path.py` — implementer-reported real-Groq pass (trusted per task instructions, not re-run); also required an undocumented-until-then `LANGFUSE_PUBLIC_KEY` fix, retroactively added to `.env.sample` |
| T10  | ✅ Done | `test_auto_reject.py` |
| T11  | ✅ Done | `test_human_review.py` — 3 cases (landing + both PUT resolutions) |
| T12  | ✅ Done | `test_retry_ghost_stale.py` — 3 cases, E2E-09 implemented as a documented race-free equivalent (see Spec-Anchored table) |
| T13  | ✅ Done | `README.md` — dotenv-mount + `e2e` marker sections added |
| T14  | ✅ Done | `docs/codebase/TESTING.md`/`INTEGRATIONS.md` updated |

**Undocumented-in-tasks.md but present in the diff** (both discovered during real-Groq validation, both retroactively explained in commit messages, not routed through a new task):
- `92d834a` — `GuardrailVerdict` schema (`consistent: bool` → `status: Literal[...]`) realigned to match the prompt's actual `{"status": ..., "reason": ...}` output contract; Groq was 400-ing on the mismatch. Directly enables AGT-01/E2E-04..06 to function at all — a real bug, correctly fixed, but never given its own requirement ID.
- `82e4286` — `extract_fields` prompt nudged to require ISO-8601 `receipts_date`, mitigating an observed flake (Groq occasionally returning `DD/MM/YYYY`, failing tool-call schema validation). No test asserts this prompt text — see Gap 1 below.

---

## Spec-Anchored Acceptance Criteria

| Criterion (WHEN X THEN Y) | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --- | --- | --- |
| ENV-01.1: 4 services get a `.env` runtime mount | `./.env:/app/.env:ro` on `api`/`publisher`/`reimbursement`/`migrate` | `docker-compose.yml:280,305,328,350` — `volumes: - ./.env:/app/.env:ro` present on all 4 | ✅ PASS |
| ENV-01.2: built image contains no `.env` | `.dockerignore` excludes `.env`/`.env.*` from build context | `.dockerignore:6-7` — `.env` / `.env.*` (with `!.env.sample` carve-out) | ✅ PASS |
| ENV-01.3: `reimbursement` boots from `.env` alone, vars absent from compose | Container `Up`, staying up ≥10s | No automated test (manual-only per tasks.md); evidence = commit `ac94e6c`'s message ("all three stayed Up >30s... contains no .env file") | ⚠️ Spec-precision gap (evidence-or-zero: no `file:line`; commit-message-only claim, not independently reproducible by the Verifier without a live Docker run) |
| ENV-02.1/.2/.3: `environment:` blocks pruned to exact topology allowlist per service | `api`/`publisher`: `{KAFKA_BOOTSTRAP_SERVERS, DATABASE_URL}`; `reimbursement`: `+ LANGFUSE_HOST` | `docker-compose.yml:301-303,323-326,346-348` — exact match, no extra keys (visually confirmed + parity-test-enforced) | ✅ PASS |
| ENV-03.1: `.env.sample` defines `LANGFUSE_SECRET_KEY=` | Same placeholder value as `LANGFUSE_INIT_PROJECT_SECRET_KEY` | `.env.sample:20,27` — both `sk-lf-local-dev` | ✅ PASS |
| ENV-03.2: no compose-side `LANGFUSE_SECRET_KEY: ${LANGFUSE_INIT_PROJECT_SECRET_KEY}` rename | Absent from `reimbursement`'s block | `docker-compose.yml:323-326` — not present | ✅ PASS |
| ENV-04.1: parity test fails naming any unexpected env var | Assertion message includes the var name | `packages/api/tests/test_dotenv_config_parity.py:46-63` — `assert not unexpected, f"...{unexpected}"` | ✅ PASS (mutation-confirmed — see Sensor #3) |
| ENV-04.2: parity test asserts `.env.sample` covers every required var | Missing var named in assertion | `test_dotenv_config_parity.py:65-67` — `assert not missing, f"...{missing}"` | ✅ PASS |
| ENV-04.3: collected under `-m "not integration"`, no Docker | Passes standalone | Ran `uv run pytest -m "not integration and not e2e"` — 4/4 passed, no container spun up | ✅ PASS |
| AGT-01.1: `found_data` uses prompt's documented names, `ExtractedFields` names unchanged elsewhere | `currency`/`receipt_date`/`receipt_value` populated; `validate.py`/`apply_policies.py` untouched | `nodes/analysis.py:39-43` — dict built with mapped keys; `test_analysis.py:92-105` — `assert "1000" in rendered; assert "BRL" in rendered` | ✅ PASS |
| AGT-01.2: `request_data` = PII-stripped original payload | `submitted_by` absent, other payload fields present | `nodes/analysis.py:45-48`; `test_analysis.py:107-131` — asserts `raw_ocr_text`/`claimed_amount_brl` present, `submitted_by`'s value absent | ✅ PASS (mutation-confirmed — Sensor #2) |
| AGT-01.3: unit test asserts both found_data/request_data values present, `submitted_by` absent | Precise value-level assertions | `test_analysis.py:92-131` — 3 dedicated tests | ✅ PASS |
| E2E-01.1: fresh low-value item → `status="auto-approved"` | Exact string `"auto-approved"` on the row | `tests/e2e/test_happy_path.py:36-41` — `wait_for_status(..., {"auto-approved"}, ...)`; `assert item["decision_reason"] is not None` | ✅ PASS (per implementer-reported real run; not re-executed — see Gate) |
| E2E-02: LangFuse trace queryable by `uuid` as `session_id` | `trace_exists_for_session(...)` returns `True` | `test_happy_path.py:43-48` — `assert trace_exists_for_session(langfuse_client, str(uuid), ...)` | ✅ PASS (same caveat) |
| E2E-01/02.3: `@pytest.mark.e2e`, fails fast on missing `GROQ_API_KEY`/unreachable stack | Marked; `e2e_env` `pytest.fail`s before any live call | `test_happy_path.py:18` (`pytestmark`); `conftest.py:42-48` (`pytest.fail` before `Langfuse`/API calls) | ✅ PASS |
| E2E-03.1: stale-date item → `"auto-rejected"` | Exact string | `test_auto_reject.py:25-29` — `wait_for_status(..., {"auto-rejected"}, ...)` | ✅ PASS (real-run caveat) |
| E2E-03.2: `decision_reason` non-null | `is not None` | `test_auto_reject.py:30` | ✅ PASS |
| E2E-04.1: ambiguous+inconsistent item → `"human-review"` | Exact string | `test_human_review.py:41-49` (`_post_and_reach_human_review`) | ✅ PASS (real-run caveat) |
| E2E-05: `PUT` approval → `"human-approved"` | Exact string, via `GET`-equivalent `PUT` response | `test_human_review.py:58-63` — `assert response.json()["data"]["status"] == "human-approved"` | ✅ PASS (real-run caveat) |
| E2E-06: `PUT` rejection → `"human-rejected"` | Exact string | `test_human_review.py:65-70` — `== "human-rejected"` | ✅ PASS (real-run caveat) |
| E2E-07: `retry=4` → `"human-review"`, non-null `decision_reason`, no Groq call | Exact status + non-null reason; "no Groq call" proven structurally (short-circuit before `agent.decide()`) not by mock-call-count | `test_retry_ghost_stale.py:57-74` — `wait_for_status(..., {"human-review"}, ...)`; `assert item["decision_reason"] is not None` | ✅ PASS — ⚠️ spec-precision gap on "no Groq call": asserted only via code-path reasoning in the docstring/comment, not an executable assertion (no fake/spy to catch a call, consistent with "this suite makes no fakes") |
| E2E-08: ghost `uuid` → no row created, no crash | 404 on `GET`; "no crash" observed via next test | `test_retry_ghost_stale.py:76-93` — `assert response.status_code == 404`; "no crash" proxy documented inline | ✅ PASS — ⚠️ spec-precision gap on "no crash": proxy-only (next test passing), matches design.md's own accepted approach, not independently verifiable per-test |
| E2E-09: stale message → row unaffected | **SPEC_DEVIATION** (documented, reasoned): literal "stays pending" is untestable via a real-POST-created row (a natural decision always races it); reimplemented as "message older than settled `updated_at` is a no-op" | `test_retry_ghost_stale.py:95-130` — `assert item["status"] == settled["status"]; assert item["decision_reason"] == settled["decision_reason"]` | ⚠️ Spec-precision gap — asserted outcome is a reasoned substitute for the AC's literal wording, not the AC's literal wording itself. The substitution is well-argued and exercises the same guard condition, but the spec's own text is not what's being checked. |
| E2E-10.1: `uv run pytest` (default) never collects `e2e` | 0 e2e tests collected | Verified live: `uv run pytest --collect-only -q` → `572/580 collected (8 deselected)`; `uv run pytest -m e2e --collect-only -q` → those same 8 | ✅ PASS |
| E2E-10.2/README/TESTING/INTEGRATIONS document marker+prereqs+command, `.env`-mount model | Documented | `README.md` (new "End-to-end suite" section); `docs/codebase/TESTING.md` (new e2e rows); `docs/codebase/INTEGRATIONS.md` (mount-model language) | ✅ PASS |
| E2E-11 (dup of E2E-10.2 in spec's traceability table) | same | same | ✅ PASS |

**Status**: ❌ Not all criteria fully evidence-matched — 4 spec-precision gaps flagged (ENV-01.3, E2E-07's "no Groq call" clause, E2E-08's "no crash" clause, E2E-09's literal wording), all pre-existing, reasoned, and disclosed by the implementer rather than silently passed. No criterion is uncovered (0 "evidence-or-zero" failures).

---

## Discrimination Sensor

| # | File:line | Description | Killed? |
| - | --------- | ------------ | ------- |
| 1 | `packages/reimbursement/src/reimbursement/agent/nodes/analysis.py:53` | `guardrail_verdict = verdict.status == "auto-approved"` → hardcoded `True` | ✅ Killed — `test_analysis.py::it_routes_to_human_review_on_a_contradictory_verdict...` fails (`assert True is False`) |
| 2 | `packages/reimbursement/src/reimbursement/agent/nodes/analysis.py:48` | Removed the `submitted_by`-stripping comprehension, passed `payload` raw | ✅ Killed — `test_analysis.py::it_never_includes_submitted_by_in_the_rendered_prompt` fails (email found in rendered prompt) |
| 3 | `docker-compose.yml` (`reimbursement.environment`) | Added `GROQ_API_KEY: ${GROQ_API_KEY}` back into the block | ✅ Killed — `test_dotenv_config_parity.py::it_keeps_reimbursements_environment_block_to_only_its_topology_allowlist` fails, naming `{'GROQ_API_KEY'}` |

**Sensor depth**: lightweight (3 mutations, per the default tier — none of this feature's new code is itself a P0 payment/auth path; the *decision* it feeds is P0-adjacent but the mutated lines are guardrail-routing/config-hygiene, not the money-threshold logic itself, which is pre-existing and out of this feature's diff)
**Result**: 3/3 killed — ✅ PASS

All mutations applied via direct file edits (not `git stash`, since a prior stash cycle was already in flight for an unrelated pre-existing uncommitted change — see Anomalies below), one at a time, `git checkout -- <file>` immediately after confirming each kill. `git diff` on each mutated file confirmed clean before starting the next.

---

## Code Quality

| Principle | Status |
| --- | --- |
| Minimum code | ✅ — every file in the diff maps to a task or a disclosed mid-implementation fix |
| Surgical changes | ✅ — `docker-compose.yml`/`.env.sample` changes are exactly the allowlist/mount deltas the spec calls for |
| No scope creep | ✅ — the two undocumented-in-tasks fixes (`92d834a`, `82e4286`) are both real bugs blocking the feature's own real-Groq validation, not unrelated improvements |
| Matches patterns | ✅ — `test_dotenv_config_parity.py` mirrors `test_compose_parity.py`; e2e helpers mirror `test_integration.py`'s poll-with-deadline style |
| Spec-anchored outcome check | ⚠️ — 4 spec-precision gaps (see table above), all disclosed, none silently passed |
| Per-layer Coverage Expectation met | ✅ — parity test is 1:1 with ENV-04's 3 ACs; every E2E-0X AC has a dedicated assertion |
| Every test maps to a spec requirement | ✅ — no unclaimed tests found |
| Documented guidelines followed | ✅ — `docs/codebase/TESTING.md`'s marker/gate conventions, `CLAUDE.md`'s traceability requirement (LangFuse session-correlated trace check) |

---

## Edge Cases

- [x] `GROQ_API_KEY` unset → fail-fast, no hang: `conftest.py:42-48`
- [x] Compose stack not up → named diagnostic, no hang: `conftest.py:89-109` (`stack_ready`)
- [x] Strict-bucket assertion failure → no retry/suppress: confirmed by code inspection (no retry decorator/loop wraps any `wait_for_status`/assertion call in `tests/e2e/`)
- [x] `.env` missing at `docker compose up` → traceable to `_require_env`'s `ValueError`: not independently re-verified (would require tearing down a real `.env`); consistent with existing `_require_env` behavior pre-dating this feature

---

## Gate Check

- **Gate command**: `uv run pytest -m "not integration and not e2e"`
- **Result**: 560 passed, 0 failed, 20 deselected (integration-marked)
- **Collection sanity**: `uv run pytest --collect-only -q` → 572/580 collected, 8 deselected (all 8 are the new `tests/e2e/*` files) — confirms E2E-10's default-exclusion AC live, not just by reading `addopts`
- **SPEC_DEVIATION cross-check**: `uv run pytest -m "not integration" --collect-only -q` → 568/580 (12 deselected) — confirms the documented leak (568 = 560 quick-gate baseline + 8 e2e) is real, not a stale claim
- **E2E gate**: NOT re-run by the Verifier per task instructions (Groq rate-limit cost) — trusted on the implementer's reported pass, cross-checked instead by reading every e2e test file's assertions against the spec ACs (table above)
- **Test count before feature**: not independently measured (no pre-feature baseline commit was run) — the diff added exactly 4 new test methods to `test_dotenv_config_parity.py`, 8 new e2e test methods (excluded from this gate), and 3 new test methods to `test_analysis.py` (found_data/request_data/no-submitted_by) — no test deletions found in the diff
- **Delta**: no assertions weakened; `test_agent.py`/`test_integration.py`'s changes are `GuardrailVerdict(consistent=...)` → `GuardrailVerdict(status=...)` field renames tracking the schema fix, not weakened checks
- **Skipped tests**: none
- **Failures**: none

---

## Anomalies (not part of this feature's diff, disclosed for transparency)

Three uncommitted, out-of-band working-tree modifications were present/appeared during this validation session, none introduced by the Verifier and none part of the `04236cd..HEAD` diff range under review:
- `docs/SCOPE.md` — present before validation began (pre-existing per the task brief)
- `packages/reimbursement/src/reimbursement/agent/prompts/extract_fields.py` — a further prompt-wording tweak appeared mid-session (adds a `submmited_at field is not valid as Receipt Date` line)
- `packages/reimbursement/src/reimbursement/agent/nodes/apply_policies.py` — appeared mid-session, not read/reviewed by the Verifier (outside the diff range and outside this report's scope)

Per this session's own instructions these are concurrent, intentional, out-of-band edits (not the Verifier's) and were left untouched. They are **not** evaluated against the spec's ACs in this report and are called out here only so `git status --porcelain` deviating from "validation.md + docs/SCOPE.md only" is explained rather than silently unexplained.

---

## Fix Plans

None required for a PASS-with-caveats verdict — the 4 spec-precision gaps are pre-existing, disclosed-by-the-implementer reasoning calls (E2E-07/08/09's non-observability constraints, ENV-01.3's manual-only verification tier), not implementation defects. See Ranked Gaps below for optional hardening, not blocking.

---

## Requirement Traceability Update

| Requirement | Previous Status | New Status |
| --- | --- | --- |
| ENV-01 | Pending | ✅ Verified (⚠️ AC3 manual-only, no automated file:line) |
| ENV-02 | Pending | ✅ Verified |
| ENV-03 | Pending | ✅ Verified |
| ENV-04 | Pending | ✅ Verified |
| AGT-01 | Pending | ✅ Verified |
| E2E-01 | Pending | ✅ Verified (trusted real-run report) |
| E2E-02 | Pending | ✅ Verified (trusted real-run report) |
| E2E-03 | Pending | ✅ Verified (trusted real-run report) |
| E2E-04 | Pending | ✅ Verified (trusted real-run report) |
| E2E-05 | Pending | ✅ Verified (trusted real-run report) |
| E2E-06 | Pending | ✅ Verified (trusted real-run report) |
| E2E-07 | Pending | ✅ Verified (⚠️ "no Groq call" clause structural, not asserted) |
| E2E-08 | Pending | ✅ Verified (⚠️ "no crash" clause proxy-only) |
| E2E-09 | Pending | ✅ Verified (⚠️ SPEC_DEVIATION — reasoned substitute assertion, not the AC's literal wording) |
| E2E-10 | Pending | ✅ Verified |
| E2E-11 | Pending | ✅ Verified |

---

## Summary

**Overall**: ✅ Ready (with disclosed caveats — none blocking)

**Spec-anchored check**: 22/26 AC rows fully precision-matched; 4 flagged as spec-precision gaps (all pre-existing, reasoned, and disclosed by the implementer in the diff itself — not newly discovered defects)
**Sensor**: 3/3 mutations killed
**Gate**: 560 passed, 0 failed (quick gate); e2e suite not re-run, cross-checked by assertion reading

**What works**: Dotenv-first config delivery (mount + pruned compose + parity guard) is fully evidenced and mutation-confirmed. The analysis-guardrail prompt-wiring fix (AGT-01) is precisely tested at the value level and mutation-confirmed for both the routing logic and the PII-stripping guarantee. All 8 e2e scenario tests have exact-value assertions on `status`/`decision_reason`/PUT-response fields, not just "a call happened." Documentation (README/TESTING.md/INTEGRATIONS.md) accurately reflects the shipped mechanism, verified by live collection counts, not just prose review.

**Issues found**: None requiring a fix task. The 4 spec-precision gaps are each a deliberate, disclosed tradeoff (unobservable "no crash"/"no Groq call" clauses via HTTP-only assertions per design.md's own "no direct DB access" constraint; one AC literally untestable against the suite's own architecture, with a reasoned equivalent substituted and documented in the test file itself) — consistent with this project's own "no silent fallback" ethos applied to test design, not silently passed.

**Next steps**: None blocking. Optional hardening for a future pass: (1) an automated (not just manual/commit-message) proof for ENV-01 AC3's boot behavior, e.g. a lightweight CI job; (2) a unit test asserting `extract_fields.py`'s ISO-date-normalization prompt text, to guard the `82e4286` flake-mitigation fix from silent prompt drift.
