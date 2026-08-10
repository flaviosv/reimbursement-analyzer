# Agent Decision Error Escalation Validation

**Date**: 2026-08-10
**Spec**: `.specs/features/agent-decision-error-escalation/spec.md`
**Diff range**: `356bf955cd50b27be3b6fa2485270ae8b329d493..HEAD` (branch `feature/13_agent-error-handling`, commits `1d149a1`, `f0c2b7a`, `75907ca`, `d035e85`)
**Verifier**: independent sub-agent (author ≠ verifier)

---

## Task Completion

| Task | Status  | Notes |
| ---- | ------- | ----- |
| T1: Add `"decide"` to `Stage` | ✅ Done | `packages/shared/src/shared/models.py:17`; test at `packages/shared/tests/test_models.py:101-104` |
| T2: Parameterize `render_history`'s header | ✅ Done | `packages/shared/src/shared/reimbursement/use_cases/send_human_review.py:26-77`; test at `packages/shared/tests/reimbursement/use_cases/test_send_human_review.py:84-92` |
| T3: Escalate decision-stage failures in `_decide` | ✅ Done | `packages/reimbursement/src/reimbursement/validation.py:176,192-232`; 6 new + 2 updated tests in `DescribeDecideIntegration` |
| T4: Record AD-039 | ✅ Done | `.specs/STATE.md` (AD-039 appended, append-only); `.specs/features/agent-decide-reimbursement/spec.md` (one row amended in place) |

---

## Spec-Anchored Acceptance Criteria

| Criterion (WHEN X THEN Y) | Spec-defined outcome | `file:line` + assertion | Result |
| -------------------------- | --------------------- | ------------------------ | ------ |
| ADE-01 (AC1): `agent.decide()` raises → escalate via `escalate_existing` (same path `_escalate` uses) | Row's `status` becomes `human-review` via `apply_decision`/`escalate_existing` | `packages/reimbursement/src/reimbursement/validation.py:176,203-209` calls `escalate_existing`; `packages/reimbursement/tests/test_validation.py:404-405` — `assert outcome == MessageOutcome.ESCALATED` / `assert pool.rows[uuid]["status"] == "human-review"` | ✅ PASS |
| ADE-02 (AC2): `decision_reason` = `render_history([*envelope.errors, new AttemptError(stage="decide", message=str(exc))])`, unsanitized, "attempt N ... [stage] type: message" format | Full attempt-history text containing the raw exception message, tagged `[decide]`, never a bare string/placeholder | `test_validation.py:406-408` — `assert "groq unreachable" in pool.updated[uuid]` / `assert "[decide]" in pool.updated[uuid]` / `assert "Decision-stage failure" in pool.updated[uuid]`; ordering + prior-stage combination at `test_validation.py:461-463` — `"attempt 1" ... "[resolve]"` before `"attempt 2" ... "[decide]"` | ✅ PASS |
| ADE-03 (AC3): `failure_log` `DECISION_FAILED_EVENT` write still occurs, independent of escalation success | Both records exist; escalation success does not suppress the CRITICAL log | `validation.py:172-176` (log/write precedes the escalate call, unconditional); `test_validation.py:434-435` — `assert outcome == MessageOutcome.ESCALATED` / `assert any("reimbursement.decision_failed" in r.message for r in caplog.records)` | ✅ PASS |
| ADE-04 (AC4): ghost row (deleted between resolve and decide) → zero rows affected, no retry, fallback to `failure_log`-only | `escalate_existing` returns `None`; one `ESCALATION_FAILED_EVENT` write; outcome `LOGGED` | `validation.py:222-227`; `test_validation.py:488-489` — `assert outcome == MessageOutcome.LOGGED` / `assert any("reimbursement.escalation_failed" in r.message for r in caplog.records)` | ✅ PASS |
| ADE-05 (AC5): escalation write itself fails (e.g. DB unreachable) → `failure_log` write, outcome `LOGGED`, no exception propagates | Caught by `_escalate_decision_failure`'s own `try/except Exception`; `ESCALATION_FAILED_EVENT` written; outcome `LOGGED` | `validation.py:201,210-220`; `test_validation.py:513-514` — `assert outcome == MessageOutcome.LOGGED` / `assert any("reimbursement.escalation_failed" in r.message for r in caplog.records)` (test itself completing without an unhandled exception is the "no exception propagates" evidence) | ✅ PASS |
| ADE-06 (AC6): a decision computed in-memory but the persist call raised → escalation still overwrites with `human-review` + error reason, discarding the computed status | Row lands on `human-review` with the write-error text; the never-durably-written computed status/decision never appears | `test_validation.py:542-547` — `assert pool.rows[uuid]["status"] == "human-review"` / `assert "could not persist decision" in pool.updated[uuid]` / `assert "auto-approved" not in pool.updated[uuid]` | ✅ PASS |
| ADE-07 (AC7): `MessageOutcome.ESCALATED` on success; `LOGGED` on AC4/AC5 | Exact enum values, no new member | `validation.py:220,227,232`; success asserted at `test_validation.py:404,434,457,542,595`; `LOGGED` asserted at `test_validation.py:488,513` | ✅ PASS |
| ADE-08 (AC8): stdout logging keeps `sanitize(exc)`, never raw `str(exc)` | Raw exception text never appears in the stdout-captured log record; only in the DB row | `validation.py:172` (unchanged `logger.error(..., sanitize(exc))`); `test_validation.py:568-572` — `assert not any("groq unreachable" in line for line in stdout_lines)` / `assert "groq unreachable" in pool.updated[uuid]` | ✅ PASS |

**Status**: ✅ All 8 ACs covered, spec-defined outcomes matched exactly. 0 spec-precision gaps.

---

## Discrimination Sensor

| Mutation | File:line | Description | Killed? |
| -------- | --------- | ------------ | ------- |
| 1 | `packages/reimbursement/src/reimbursement/validation.py:222` (`_escalate_decision_failure`) | Flipped ghost check `if result is None:` → `if result is not None:` | ✅ Killed — 6 of 10 `DescribeDecideIntegration` tests failed (`it_escalates_...`, `it_still_writes_...`, `it_combines_...`, `it_writes_...ghost`, `it_escalates_even_when_a_decision_was_already_computed...`, `it_catches_a_malformed_original_payload_and_escalates_it`) |
| 2 | `packages/reimbursement/src/reimbursement/validation.py:232` (`_escalate_decision_failure`, success return) | Changed the success-path return `MessageOutcome.ESCALATED` → `MessageOutcome.LOGGED` | ✅ Killed — 5 of 10 tests failed on the wrong outcome enum |
| 3 | `packages/reimbursement/src/reimbursement/validation.py:208` (`_escalate_decision_failure`'s `escalate_existing` call) | Dropped the `header="Decision-stage failure"` kwarg, silently reverting to the misleading "Retry ceiling reached" default | ✅ Killed — `it_escalates_to_human_review_with_the_error_in_the_reason_and_returns_escalated` failed: `assert "Decision-stage failure" in pool.updated[uuid]` → actual text was `"Retry ceiling reached after 1 failed attempts:..."` |

**Sensor depth**: lightweight (3 mutations — this is a Medium-scope, non-P0 feature per spec.md's Coverage note)
**Result**: 3/3 killed — PASS ✅

All mutations were applied directly to the tracked file and reverted via `git checkout --` immediately after each run; the real tree carried no mutation before or after this pass (confirmed via `git diff --stat` showing only the pre-existing, unrelated `README.md` working-tree change untouched throughout).

---

## Code Quality

| Principle | Status |
| --------- | ------ |
| Minimum code | ✅ — `_escalate_decision_failure` is additive; `_decide`'s except branch changed by exactly one line (`return await _escalate_decision_failure(...)` replacing `return MessageOutcome.LOGGED`) |
| Surgical changes | ✅ |
| No scope creep | ✅ — diff touches only the 9 files named across T1-T4's "Where" fields (`validation.py`, `test_validation.py`, `models.py`, `send_human_review.py`, `test_send_human_review.py`, `test_models.py`, `.specs/STATE.md`, `agent-decide-reimbursement/spec.md`, plus this feature's own `.specs/` docs). Unrelated `README.md` working-tree change predates the feature's base commit (`git log -1 -- README.md` → `356bf95`, the diff's own base) and is outside the diff range. |
| Matches patterns | ✅ — `_escalate_decision_failure` is a structural line-for-line mirror of `_escalate` (same acquire/try/except/ghost-check/logger/return shape); `Stage` extension follows the existing literal-value convention |
| Spec-anchored outcome check (asserted values match spec) | ✅ — see AC table above, no vague assertions |
| Per-layer Coverage Expectation met (domain 1:1 ACs; routes happy+edge+error) | ✅ — every ADE-01..08 has its own dedicated test; no route/API layer in scope for this feature |
| Every test maps to a spec requirement — no unclaimed tests | ✅ — all 8 new/updated tests in `DescribeDecideIntegration` map 1:1 to tasks.md's `Done when` list; T1/T2's single new tests map to their own Requirement rows |
| Documented guidelines followed | `docs/codebase/TESTING.md` (Describe*/it_* pytest convention, gate commands) — followed; `test_models.py::it_accepts_the_agent_resolve_stage` precedent followed by T1's `it_accepts_the_agent_decide_stage` |

**Additional note (not a gap):** `render_history`'s `_NO_HISTORY` placeholder text (`send_human_review.py:12`) remains hardcoded to "Retry ceiling reached..." and is not parameterized by `header`. This is safe and matches the spec's edge case exactly — `_escalate_decision_failure` always passes a list of at least one entry (`[*envelope.errors, <new AttemptError>]`), so the `if not errors: return _NO_HISTORY` branch inside `render_history` is structurally unreachable from the decision-stage escalation path. Confirmed by reading `validation.py:200` and `send_human_review.py:40-41` together, not assumed.

---

## Edge Cases

- [x] `envelope.errors` empty → rendered `decision_reason` still has exactly one attempt entry, `_NO_HISTORY` never appears: confirmed structurally (`_escalate_decision_failure` always appends ≥1 entry — see Code Quality note above) and exercised by `test_validation.py:381-411` (default envelope has `errors=[]`, assertions at lines 406-408 target the entry-format text, not the placeholder)
- [x] Embedded newlines in `str(exc)` collapsed via `_one_line` — unchanged `render_history` internals (only the header line changed in T2), inherited via the same call path `_escalate` already uses; existing coverage at `packages/shared/tests/reimbursement/use_cases/test_send_human_review.py:94-106` (`it_collapses_embedded_newlines_in_a_driver_supplied_message`) exercises this at the shared-component level `_escalate_decision_failure` calls unchanged
- [x] `str(exc)` truncated to `max_message_chars` — same inheritance; existing coverage at `test_send_human_review.py:76-82` (`it_truncates_each_message_to_the_configured_cap`); `_escalate_decision_failure` passes `deps.config.failure_log.max_message_chars` through identically to `_escalate` (`validation.py:207` vs. `validation.py:100`)

All three edge cases verified as genuinely "inherited for free," per design's claim — by reading the code path, not assuming it.

---

## Gate Check

- **Gate command**: `uv run pytest` (Full gate, per tasks.md's Gate Check Commands table)
- **Result**: 582 passed, 0 failed, 8 deselected (e2e tests, excluded by default marker config — consistent with the Verifier's e2e exclusion instruction)
- **Test count before feature** (derived from per-file `git show <base>:<file> | grep -c` deltas): 574
- **Test count after feature**: 582
- **Delta**: +8 (T1: `test_models.py` +1; T2: `test_send_human_review.py` +1; T3: `test_validation.py` +6 net-new, plus 2 pre-existing tests updated in place — not deleted, not double-counted)
- **Skipped tests**: none
- **Failures**: none

---

## Requirement Traceability Update

| Requirement | Previous Status | New Status  |
| ----------- | ---------------- | ----------- |
| ADE-01      | Pending           | ✅ Verified |
| ADE-02      | Pending           | ✅ Verified |
| ADE-03      | Pending           | ✅ Verified |
| ADE-04      | Pending           | ✅ Verified |
| ADE-05      | Pending           | ✅ Verified |
| ADE-06      | Pending           | ✅ Verified |
| ADE-07      | Pending           | ✅ Verified |
| ADE-08      | Pending           | ✅ Verified |

---

## Summary

**Overall**: ✅ Ready

**Spec-anchored check**: 8/8 ACs matched spec outcome, 0 spec-precision gaps
**Sensor**: 3/3 mutations killed
**Gate**: 582 passed, 0 failed

**What works**: Every ADE-01..08 acceptance criterion is traced to a passing test with an assertion targeting the exact spec-defined outcome (enum value, DB status, `decision_reason` content/format, stdout sanitization). `_escalate_decision_failure` is a faithful structural mirror of `_escalate`. All 3 edge cases (empty history, embedded newlines, truncation) are verified — by reading the code, not assumed — to be genuinely inherited through the unchanged `render_history` internals. The discrimination sensor's 3 targeted mutations (ghost-check inversion, wrong success enum, dropped header kwarg) were all killed by the existing test suite. T4's documentation-only changes (`AD-039`, the amended Assumptions row) match their `Done when` criteria exactly, append-only with no other content disturbed. No scope creep — the diff touches exactly the files named in T1-T4.

**Issues found**: none

**Next steps**: none — feature ready to ship as-is.
