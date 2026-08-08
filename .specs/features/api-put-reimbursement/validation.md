# api-put-reimbursement Validation

**Date**: 2026-08-08
**Spec**: `.specs/features/api-put-reimbursement/spec.md`
**Diff range**: `c95118d..HEAD` (7f5434a) on `feature/7_reimbursement_get_put`
**Verifier**: independent sub-agent (author ≠ verifier)

---

## Task Completion

| Task | Status  | Notes |
| ---- | ------- | ----- |
| T1   | ✅ Done | `shared.errors.{ReviewInvalid, ReimbursementNotFound, ReimbursementNotEligible}` added |
| T2   | ✅ Done | `approve`/`reject`/`find_reimbursement_state`/`record_human_review_decision` + 8 new repository tests |
| T3   | ✅ Done | `use_cases.review_reimbursement` + 8 new tests, incl. concurrency |
| T4   | ✅ Done | `reimbursement/update/validation.py` + 7 new tests |
| T5   | ✅ Done | `reimbursement/update/route.py`, 3 handlers registered, + 13 new tests |

---

## Spec-Anchored Acceptance Criteria

| Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| --------- | --------------------- | ------------------------ | ------ |
| REVIEW-01 Approve happy path | `200`, `reimbursement.status='human-approved'` + `receipts_*` from payload, exactly one `human_review` row (`approved`, `reviewed_by`, `reason`) | `src/shared/tests/reimbursement/test_repository.py:331-350` (`DescribeApprove::it_succeeds_on_an_eligible_row_and_returns_the_updated_row` — asserts status, receipts_value, receipts_date, currency, decision_reason) + `test_repository.py:409-420` (`DescribeRecordHumanReviewDecision` — len==1, status/reviewed_by/reason) + `src/shared/tests/reimbursement/use_cases/test_review_reimbursement.py:44-61` + `src/api/tests/reimbursement/update/test_route.py:76-85` (`response.status_code==200`) | ✅ PASS |
| REVIEW-02 Approve missing field → `422` | `422`, `{"msg"}` naming the missing field, no DB change | `src/api/tests/reimbursement/update/test_validation.py:40-44` (`pytest.raises(ReviewInvalid)`) + `test_route.py:87-95` (`status_code==422`, `"msg" in response.json()`) | ⚠️ Spec-precision gap — message content ("naming the missing field") and "no DB change" are never asserted, only that a `422`/`ReviewInvalid` occurs |
| REVIEW-03 Malformed `receipts_*` → `422` | `422`, no DB change | `test_validation.py:46-62` (date/currency/negative-value, 3 tests) + `test_route.py:97-104` (bad currency → `422`) | ⚠️ Spec-precision gap — status code precise and 3/3 fields covered, but "no DB change" clause untested at route level |
| REVIEW-04 Approve ineligible status → `400` | `400`, no DB change | `test_repository.py:352-367` (row is `None`, status unchanged via explicit query) + `test_review_reimbursement.py:87-106` (raises `ReimbursementNotEligible`, status unchanged, hr count 0) + `test_route.py:106-112` (`400`) | ✅ PASS — full precision incl. no-DB-change; confirmed by sensor mutation 1 |
| REVIEW-05 Reject happy path | `200`, `human-rejected`, one `human_review` row, no `receipts_*` in payload required | `test_repository.py:371-379` + `test_review_reimbursement.py:110-119` + `test_route.py:114-122` (`200`) | ✅ PASS |
| REVIEW-06 Reject missing `reason`/`approved_by` → `422` | `422` | `test_validation.py:64-68` (missing `approved_by` only) + `test_route.py:124-131` (missing `approved_by` only, `422`) | ⚠️ Spec-precision gap — only the `approved_by`-missing half of the "reason **or** approved_by" disjunction is tested; missing `reason` on reject has no test |
| REVIEW-07 Unknown uuid → `404` | `404` | `test_review_reimbursement.py:63-85` (approve) + `:121-123` (reject) — `ReimbursementNotFound` raised, hr count 0 + `test_route.py:133-137` (`404`, via approve payload) + `test_route.py:206-220` (`DescribeTheRealApp`, real app wiring, `404`) | ✅ PASS — confirmed by sensor mutation 3 |
| REVIEW-08 Ineligible status (existing row) → `400` not `404` | `400` | `test_route.py:106-112` (approve) + `:139-145` (reject) — both `400` | ✅ PASS on status-code precision; reject-ineligible path's "no DB change" is not independently re-asserted at use-case level (`test_review_reimbursement.py:125-131` has no post-raise DB query), unlike the approve-ineligible sibling test |
| REVIEW-09 Concurrent decisions — exactly one wins | Exactly one `200`, one `400`; exactly one `human_review` row | `test_review_reimbursement.py:145-191` (`successes` len 1, `failures` len 1 `isinstance ReimbursementNotEligible`, hr count 1, real two-connection concurrency) + `test_route.py:175-203` (`sorted(statuses)==[200,400]`, hr count==1, real 2-conn pool) | ✅ PASS — precise on both layers |
| REVIEW-10 Reject blocked on incomplete receipts → `400` | `400`, no DB change | `test_repository.py:381-390` (row `None`, status unchanged) + `test_review_reimbursement.py:133-142` (raises `ReimbursementNotEligible`, hr count 0) + `test_route.py:147-153` (`400`) | ✅ PASS — confirmed by sensor mutation 2, the single strongest kill of the sensor run |

**Status**: ⚠️ Spec-precision gaps flagged on REVIEW-02, REVIEW-03, REVIEW-06, REVIEW-08 (all around the unasserted "no DB change" / "naming the field" clauses); 6/10 ACs fully precise with zero gaps.

### Edge Cases (spec.md)

- [x] Body `uuid` present and differs from path `uuid` → `400` — `test_route.py:155-164`. Confirmed by sensor mutation 5.
- [x] `status` not `approved`/`rejected` → `422` — `test_validation.py:70-74`.
- [ ] **DB transaction fails after the `human_review` insert but before the `reimbursement` update (or vice versa) → roll back both** — **NOT TESTED.** No test injects a mid-transaction failure. Confirmed genuinely uncovered by sensor mutation 4 (see below) — the mutant that deletes the `async with conn.transaction():` wrapper survived the full suite.
- [x] A `human-review` row with never-extracted receipts can be approved but never rejected — implicitly covered by REVIEW-01 (approve doesn't require pre-existing receipts) + REVIEW-10 (reject blocked when they're missing); no combined single test, acceptable as the union of the two existing ACs.

---

## Discrimination Sensor

Run in an isolated `git worktree` under the scratchpad (`/private/.../scratchpad/sensor-worktree`), never in the real tree. Reverted and removed after each mutation; real tree confirmed clean (`git status --short` empty) at the end.

| # | File:line | Description | Killed? |
| - | --------- | ------------ | ------- |
| 1 | `src/shared/src/shared/reimbursement/use_cases/review_reimbursement.py:28` | `ELIGIBLE_STATUSES` extended to also accept `"human-approved"` | ✅ Killed (6 tests failed: REVIEW-04/08/09 across use_case + route) |
| 2 | `src/shared/src/shared/reimbursement/repository.py:71-81` (`_REJECT`) | Dropped the `receipts_value/date/currency IS NOT NULL` completeness gate from the reject `WHERE` clause | ✅ Killed (3 tests failed: REVIEW-10 across repository, use_case, route) |
| 3 | `src/shared/src/shared/reimbursement/use_cases/review_reimbursement.py:36-37` (`_disambiguate`) | Unknown-uuid branch raises `ReimbursementNotEligible` instead of `ReimbursementNotFound` | ✅ Killed (4 tests failed: REVIEW-07 across use_case + route, incl. `DescribeTheRealApp`) |
| 4 | `src/shared/src/shared/reimbursement/use_cases/review_reimbursement.py:51,70` (`approve_reimbursement`/`reject_reimbursement`) | Removed the `async with conn.transaction():` wrapper around both writes (atomicity boundary) | ❌ **Survived** — all 71 feature-scoped tests still passed. No test exercises the mid-transaction-failure/rollback edge case explicitly listed in spec.md's Edge Cases section. |
| 5 | `src/api/src/reimbursement/update/route.py:33` | Flipped body/path uuid consistency check `!=` → `==` | ✅ Killed (1 test failed: uuid-mismatch test) |

**Sensor depth**: lightweight (5 targeted behavior-level mutations)
**Result**: 4/5 killed, 1 survived — ❌ FAIL

---

## Payload/Conjunction Rule (`human_review` row fields)

- **Repository layer** (`DescribeRecordHumanReviewDecision`, `test_repository.py:409-420`): calls `record_human_review_decision` directly and asserts `status`, `reviewed_by`, `reason` together on the one resulting row, plus `len(rows)==1`. Strongest evidence — full conjunction of fields, not just "an insert happened."
- **Use-case layer**: approve's happy-path test (`test_review_reimbursement.py:44-61`) asserts `hr["status"]` and `hr["reviewed_by"]` together, but not `hr["reason"]`. Reject's happy-path test (`:110-119`) asserts only `hr["status"]`, neither `reviewed_by` nor `reason`.
- **Route layer**: neither the approve nor the reject route test queries the `human_review` table at all — both only re-read `reimbursement.status`.

**Assessment**: the conjunction rule is satisfied at the repository layer (the layer that owns the actual SQL), and partially at the use-case layer for approve. Reject's use-case test and both route tests fall back to "an insert happened" implicitly (via the repository test's separate proof) rather than re-asserting field values at their own layer. Not a blocking gap given the repository-layer coverage is exhaustive, but noted as thinner than the approve path.

---

## Code Quality

| Principle | Status |
| --------- | ------ |
| Minimum code | ✅ |
| Surgical changes | ✅ — only the files the task list named |
| No scope creep | ✅ |
| Matches patterns | ✅ — mirrors `create/route.py`/`create/validation.py`/`send_human_review.py` shapes per tasks.md's "Reuses" notes |
| Spec-anchored outcome check (asserted values match spec) | ⚠️ — see spec-precision gaps above |
| Per-layer Coverage Expectation met (domain 1:1 ACs; routes happy+edge+error) | ✅ — domain logic (repository/use_case) covers every AC field-precisely; routes cover every status code |
| Every test maps to a spec requirement — no unclaimed tests | ✅ |
| Documented guidelines followed | `docs/codebase/TESTING.md` (Describe*/it_* convention, `pythonpath` layout, gate commands) — followed |

---

## Gate Check

- **Gate command**: `uv run pytest` (Full gate, per tasks.md)
- **Result**: 363 passed, 0 failed, 0 skipped
- **Test count before feature** (commit `0002ff5`, measured via isolated scratch worktree): 327
- **Test count after feature** (`HEAD`): 363
- **Delta**: +36 — matches tasks.md's declared per-task deltas exactly (T2 +8, T3 +8, T4 +7, T5 +13)
- **Skipped tests**: none
- **Failures**: none

---

## Fix Plans

### Fix 1: Untested transaction-atomicity edge case (sensor mutation 4 survived)

- **Root cause**: `approve_reimbursement`/`reject_reimbursement` wrap both writes in `async with conn.transaction():`, which is the correct mechanism, but no test proves it — every existing test only exercises the two-writes-both-succeed or zero-writes-happen paths. The spec's Edge Cases section explicitly requires: "WHEN the DB transaction fails after the `human_review` insert but before the `reimbursement` update (or vice versa) THEN the system SHALL roll back both."
- **Fix task**: Add a test (likely in `test_review_reimbursement.py`) that injects a failure between the two writes — e.g. monkeypatch/wrap `record_human_review_decision` (or `approve`/`reject`) to raise after the first statement executes but before commit, then assert via a second connection that neither the `reimbursement.status` change nor the `human_review` row is visible (both rolled back).
- **Priority**: Major — this is an explicit, named spec requirement with zero test coverage, empirically confirmed via mutation testing (not merely inferred).

### Fix 2 (minor): Spec-precision gaps on REVIEW-02/03/06/08

- **Root cause**: several ACs specify clauses ("naming the missing field", "no DB change", the `reason`-missing half of REVIEW-06's disjunction) that the tests don't independently assert — they assert the status code but not every clause of the AC's THEN.
- **Fix task**: add the missing assertions — DB-row-unchanged queries after 422/400 responses where the AC requires "no DB change," a message-content check for at least one 422 case, and a `reason`-missing reject-validation test alongside the existing `approved_by`-missing one.
- **Priority**: Minor — the underlying mechanisms are proven correct by other tests (e.g., the atomic `UPDATE...WHERE...RETURNING` pattern was mutation-tested for REVIEW-04/10), this is about closing the assertion-to-spec-clause distance, not fixing broken behavior.

---

## Requirement Traceability Update

| Requirement | Previous Status | New Status |
| ----------- | ---------------- | ----------- |
| REVIEW-01 | Implementing | ✅ Verified |
| REVIEW-02 | Implementing | ⚠️ Verified with spec-precision gap |
| REVIEW-03 | Implementing | ⚠️ Verified with spec-precision gap |
| REVIEW-04 | Implementing | ✅ Verified |
| REVIEW-05 | Implementing | ✅ Verified |
| REVIEW-06 | Implementing | ⚠️ Verified with spec-precision gap |
| REVIEW-07 | Implementing | ✅ Verified |
| REVIEW-08 | Implementing | ⚠️ Verified with spec-precision gap |
| REVIEW-09 | Implementing | ✅ Verified |
| REVIEW-10 | Implementing | ✅ Verified |

---

## Summary

**Overall**: ❌ Not Ready (one surviving mutant on a named spec edge case blocks "done")

**Spec-anchored check**: 10/10 ACs have `file:line` evidence matching the spec-defined status code exactly; 4 flagged with spec-precision gaps on secondary clauses
**Sensor**: 4/5 mutations killed, 1 survived
**Gate**: 363 passed, 0 failed (full suite); scoped feature tests 71/71 passed; test count integrity confirmed (+36, matches declared deltas)

**What works**: All 10 REVIEW ACs' primary status-code outcomes are correctly implemented and tested across repository/use-case/route layers; the concurrency guarantee (REVIEW-09) and the reject-completeness gate (REVIEW-10) — the two ACs this task explicitly flagged as easy to under-test — are both solidly covered with real multi-connection concurrency tests and confirmed by mutation testing.

**Issues found**:
1. Transaction-atomicity edge case has zero test coverage (Fix 1 above) — Major.
2. Four ACs have secondary spec clauses ("no DB change", message content, `reason`-missing on reject) that aren't independently asserted (Fix 2 above) — Minor.

**Next steps**: Route Fix 1 to an implementer as a fix task; re-verify with the sensor re-run on that mutation. Fix 2 items can be batched into the same pass.
