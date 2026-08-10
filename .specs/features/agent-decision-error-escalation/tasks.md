# Agent Decision Error Escalation Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user — do not proceed without it.**

---

**Design**: `.specs/features/agent-decision-error-escalation/design.md`
**Status**: Draft

---

## Test Coverage Matrix

> Generated from codebase sampling. Guidelines found: `docs/codebase/TESTING.md` (project-wide test conventions), plus a direct precedent in `packages/shared/tests/test_models.py::DescribeAttemptErrorFromException::it_accepts_the_agent_resolve_stage` — every prior addition to `Stage`'s allowed value set shipped with its own dedicated acceptance test, used here as the floor for T1.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------- | --------------------- | ----------------- | ----------- |
| `shared.models.Stage` (new literal value) | Unit | One dedicated acceptance test for the new value, matching the existing per-value precedent (`it_accepts_the_agent_resolve_stage`) | `packages/shared/tests/test_models.py` | `uv run pytest -m "not integration"` |
| `shared.reimbursement.use_cases.send_human_review.render_history`/`escalate_existing` (header parameterization) | Unit | Existing tests continue to pass unchanged (default preserves current wording exactly); one new test asserts a non-default `header` renders instead of "Retry ceiling reached" | `packages/shared/tests/reimbursement/use_cases/test_send_human_review.py` | `uv run pytest -m "not integration"` |
| `reimbursement.validation._decide` (decision-stage escalation branch) | Unit (fakes) | 1:1 to spec ACs — every ADE-01..08 scenario gets its own test in `DescribeDecideIntegration`, using the existing `FakePool`/`_error`/`_row`/`_deps` doubles (no new fakes needed) | `packages/reimbursement/tests/test_validation.py` | `uv run pytest -m "not integration"` |
| `.specs/STATE.md` / `agent-decide-reimbursement/spec.md` (decision record) | none | Documentation-only change — build gate only | n/a | n/a |

> **Amended during Execute (T2 inserted):** implementing the escalation
> branch surfaced that `render_history` hardcodes its header as "Retry
> ceiling reached after N failed attempts:" — accurate for `_escalate`'s
> retry-ceiling path, factually wrong for an immediate decision-stage
> escalation (which typically has no retry ceiling involved at all). Fixed
> by parameterizing the header, user-approved before implementation. This
> is a correction to the plan, not new scope — flagged and confirmed before
> any code was touched.

## Gate Check Commands

> Reused verbatim from `docs/codebase/TESTING.md` — this feature needs no new gate tier (no DB/Kafka dependency; the existing `FakePool` already covers every scenario).

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | After T1, T2, and T3 | `uv run pytest -m "not integration and not e2e"` |
| Full | Before considering the feature done (after T4) | `uv run pytest` |

---

## Execution Plan

Phases are ordered and run sequentially — each phase completes before the next begins, and tasks within a phase execute in order.

### Phase 1: Foundation

```
T1 → T2
```

### Phase 2: Core Implementation

```
T3
```

### Phase 3: Decision Record

```
T4
```

---

## Task Breakdown

### T1: Add `"decide"` to the `Stage` literal

**What**: `shared/models.py`'s `Stage` gains a fourth value, `"decide"`, alongside the existing `"db-insert"`, `"publish"`, `"resolve"` — a type-only, additive change. Add one dedicated acceptance test mirroring the existing `it_accepts_the_agent_resolve_stage` precedent.
**Where**: `packages/shared/src/shared/models.py` (modify), `packages/shared/tests/test_models.py` (modify)
**Depends on**: None
**Reuses**: `AttemptError.from_exception` (already generic over `Stage`, needs no change) — same pattern `it_accepts_the_agent_resolve_stage` already established when `"resolve"` was added
**Requirement**: ADE-02

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `Stage = Literal["db-insert", "publish", "resolve", "decide"]`
- [ ] `DescribeAttemptErrorFromException::it_accepts_the_agent_decide_stage` added, asserting `AttemptError.from_exception(1, "decide", RuntimeError("boom")).stage == "decide"`
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`
- [ ] Test count: existing `packages/shared/tests/test_models.py` count + 1 passes

**Tests**: unit
**Gate**: quick

**Commit**: `feat(shared): add "decide" stage to AttemptError`

---

### T2: Parameterize `render_history`'s header

**What**: `render_history(errors, max_message_chars, *, header: str = "Retry ceiling reached")` — the hardcoded "Retry ceiling reached" phrase becomes a keyword-only parameter defaulting to its current text, so `f"{header} after {len(errors)} failed attempts:"` replaces the literal string. `escalate_existing` and `send_human_review` gain the same keyword-only `header` passthrough (both default-preserving). Discovered during T3's implementation: reusing the retry-ceiling wording verbatim for a decision-stage escalation (which typically has no retry ceiling involved) would write a factually wrong `decision_reason` — user-approved fix, inserted as its own task rather than silently expanding T3's scope.
**Where**: `packages/shared/src/shared/reimbursement/use_cases/send_human_review.py` (modify), `packages/shared/tests/reimbursement/use_cases/test_send_human_review.py` (modify)
**Depends on**: None
**Reuses**: nothing new — existing `_one_line`/truncation logic inside `render_history` is untouched, only the header line's construction changes
**Requirement**: ADE-02 (prerequisite)

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `render_history`'s default behavior is byte-identical to today's for every existing caller (`_escalate`, `send_human_review`) — all existing tests in `DescribeRenderHistory`/`DescribeSendHumanReview`/`DescribeEscalateExisting` pass unchanged, with no assertion rewritten
- [ ] `it_renders_a_custom_header_instead_of_the_retry_ceiling_wording` — `render_history(errors, _LIMIT, header="Decision-stage failure")` produces text containing `"Decision-stage failure after"` and NOT containing `"Retry ceiling"`
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`
- [ ] Test count: existing `packages/shared/tests/reimbursement/use_cases/test_send_human_review.py` count + 1 passes

**Tests**: unit
**Gate**: quick

**Commit**: `refactor(shared): parameterize render_history's header`

---

### T3: Escalate decision-stage failures to human-review in `_decide`

**What**: `_decide`'s `except Exception` branch, after its existing `failure_log.write(DECISION_FAILED_EVENT, ...)` call, additionally escalates the row to `human-review` via `escalate_existing`, combining `envelope.errors` with a new `AttemptError.from_exception(len(envelope.errors) + 1, "decide", exc)` and passing `header="Decision-stage failure"` (T2). Mirrors `_escalate`'s exact ghost/write-failure fallback shape (its own `failure_log.write(ESCALATION_FAILED_EVENT, ...)` on a ghost or a raised exception) and returns `MessageOutcome.ESCALATED` on success, `MessageOutcome.LOGGED` otherwise. `logger.error(...)`'s `sanitize(exc)` call is untouched.
**Where**: `packages/reimbursement/src/reimbursement/validation.py` (modify `_decide`, add `_escalate_decision_failure`), `packages/reimbursement/tests/test_validation.py` (modify `DescribeDecideIntegration`)
**Depends on**: T1 (constructing the new `AttemptError` with `stage="decide"` is rejected by pydantic validation until `Stage` includes it), T2 (`header` kwarg)
**Reuses**: `escalate_existing` (`shared/reimbursement/use_cases/send_human_review.py`), `render_history`, `_failure_record`, `deps.pool.acquire(timeout=...)` — all called exactly as `_escalate` (same file, `retry > 3` path) already calls them; `FakePool`'s existing `update_errors`/ghost-via-absent-uuid support (`packages/reimbursement/tests/agent_fakes.py`) needs no changes
**Requirement**: ADE-01, ADE-02, ADE-03, ADE-04, ADE-05, ADE-06, ADE-07, ADE-08

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `it_escalates_to_human_review_with_the_error_in_the_reason_and_returns_escalated` — `_decide` raises, row's `status` becomes `human-review`, `decision_reason` contains the exception message and `"decide"`-tagged formatting, outcome is `MessageOutcome.ESCALATED` (ADE-01, ADE-02, ADE-07)
- [ ] `it_still_writes_the_failure_log_when_escalation_succeeds` — `DECISION_FAILED_EVENT` is present in `caplog` even though escalation also succeeded (ADE-03)
- [ ] `it_combines_prior_resolve_stage_errors_with_the_new_decide_failure_in_the_reason` — envelope carries pre-existing `errors=[_error(1)]`; the rendered `decision_reason` contains both the earlier `"resolve"`-stage line and the new `"decide"`-stage line, in order (ADE-02)
- [ ] `it_writes_to_the_failure_log_and_returns_logged_when_the_uuid_is_a_ghost` — the row is absent from `pool.rows` when `_decide` raises; escalation affects zero rows, `ESCALATION_FAILED_EVENT` is logged, outcome is `MessageOutcome.LOGGED`, no exception propagates (ADE-04)
- [ ] `it_writes_to_the_failure_log_and_returns_logged_when_the_escalation_write_itself_fails` — `FakePool(update_errors={uuid: RuntimeError(...)})`; `ESCALATION_FAILED_EVENT` is logged, outcome is `MessageOutcome.LOGGED` (ADE-05)
- [ ] `it_escalates_even_when_a_decision_was_already_computed_but_the_persist_call_raised` — the stubbed `agent.decide()` raises after having "computed" a decision in a way that mirrors a persist-step failure (e.g. the fake raises the same exception the real `apply_agent_decision` write would); asserts the row still lands on `human-review` with the error-based reason, not any partially-computed status (ADE-06)
- [ ] `it_keeps_the_stdout_log_sanitized_never_the_raw_decide_error` — the stdout `logger.error` line uses `sanitize(exc)` output only, and the raw `str(exc)` text does NOT appear anywhere in the stdout-captured log record (only in `pool.rows[uuid]["decision_reason"]`) (ADE-08)
- [ ] The 2 pre-existing `DescribeDecideIntegration` tests that pinned the pre-feature behavior (`it_catches_a_decide_failure_writes_the_failure_log_and_returns_logged_without_propagating`, `it_catches_a_malformed_original_payload_as_a_decision_failure`) are updated to assert the new spec-mandated outcome (`ESCALATED`, row `status == "human-review"`) — user-approved during Execute (see Post-Gate note), same triggering setup retained
- [ ] Gate check passes: `uv run pytest -m "not integration and not e2e"`
- [ ] Test count: existing `packages/reimbursement/tests/test_validation.py` count + 6 passes (no silent deletions of the 2 existing `DescribeDecideIntegration` failure-path tests — updated, not removed)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(reimbursement): escalate decision-stage failures to human-review`

---

### T4: Record R-011's resolution as AD-039

**What**: Append `AD-039` to `.specs/STATE.md`'s `## Decisions` section, recording "immediate escalation, not retry-then-escalate or a cause-differentiated policy" as R-011's resolution — same format/depth as the existing `AD-NNN` entries. Update `agent-decide-reimbursement/spec.md`'s Assumptions row for "Decision-stage error-handling mechanism" (currently "Not selected in this spec... deferred to a later session — see R-011") to note it is now resolved by this feature, pointing at `AD-039`, mirroring the AD-020/AD-027/AD-030 precedent of amending prior docs in place rather than leaving them silently stale.
**Where**: `.specs/STATE.md` (append), `.specs/features/agent-decide-reimbursement/spec.md` (modify one row)
**Depends on**: T3 (the decision is recorded as shipped, not merely designed)
**Reuses**: AD-020/AD-027/AD-030's own "amend the referencing doc in place with an inline note" pattern
**Requirement**: N/A (project memory, not a spec.md requirement)

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `AD-039` appended to `.specs/STATE.md`'s `## Decisions` section (append-only — no existing entry rewritten), stating the decision, reason, trade-off, scope (`reimbursement/validation.py`'s decision-stage failure path), and date
- [ ] `agent-decide-reimbursement/spec.md`'s Assumptions row for "Decision-stage error-handling mechanism" amended in place with a pointer to `AD-039` and this feature
- [ ] No other content in either file changed

**Tests**: none
**Gate**: none (documentation only — verified by review, not by the test suite)

**Commit**: `docs(specs): record AD-039 — R-011 resolved as immediate escalation`

---

## Phase Execution Map

```
Phase 1 → Phase 2 → Phase 3

Phase 1:  T1 ──→ T2
Phase 2:  T3
Phase 3:  T4
```

Execution is strictly sequential. This entire feature is 4 tasks — well under the
~7-task sub-agent batch threshold, so Execute runs inline with no sub-agents
spawned.

---

## Task Granularity Check

| Task | Scope | Status |
| ---- | ----- | ------ |
| T1: Add `"decide"` to `Stage` | 1 type change + 1 test | ✅ Granular |
| T2: Parameterize `render_history`'s header | 1 function signature + 2 passthrough call sites + 1 test | ✅ Granular |
| T3: Escalate decision-stage failures in `_decide` | 1 function's except branch + 1 new helper | ✅ Granular |
| T4: Record AD-039 | 1 memory append + 1 row amendment | ✅ Granular |

---

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| ---- | ----------------------- | -------------- | ------ |
| T1 | None | No incoming arrow | ✅ Match |
| T2 | None | Phase 1 internal, no arrow from T1 | ✅ Match |
| T3 | T1, T2 | Phase 1 → Phase 2 | ✅ Match |
| T4 | T3 | Phase 2 → Phase 3 | ✅ Match |

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| ---- | ---------------------------- | ---------------- | --------- | ------ |
| T1: Add `"decide"` to `Stage` | `shared.models.Stage` | Unit | unit | ✅ OK |
| T2: Parameterize `render_history`'s header | `shared.reimbursement.use_cases.send_human_review` | Unit | unit | ✅ OK |
| T3: Escalate decision-stage failures | `reimbursement.validation._decide` | Unit (fakes) | unit | ✅ OK |
| T4: Record AD-039 | Documentation | none | none | ✅ OK |

---

## Task Verification Standards

Every task's `Done when` entries are specific and binary pass/fail, each naming
the exact test function it maps to and the requirement ID(s) it covers.
Expected test-count deltas are stated per task to catch silent deletions.
`Gate` commands are copied verbatim from `docs/codebase/TESTING.md` — no new
gate tier introduced.
