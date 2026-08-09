# api-put-reimbursement Validation

**Date**: 2026-08-08 (re-verification)
**Spec**: `.specs/features/api-put-reimbursement/spec.md`
**Diff range**: `c95118d..HEAD` (`5f5203c`) on `feature/7_reimbursement_get_put`; the PUT-relevant delta since the prior report is the single new commit `ba9a828` (`5f5203c` on top of it is the sibling GET feature's own fix commit — confirmed via `git show --stat 5f5203c`, touches only `list`/GET files, no overlap with PUT)
**Verifier**: independent sub-agent (author ≠ verifier), fresh re-run — no memory of the implementer's session

---

## Task Completion

| Task | Status  | Notes |
| ---- | ------- | ----- |
| T1   | ✅ Done | `shared.errors.{ReviewInvalid, ReimbursementNotFound, ReimbursementNotEligible}` — unchanged since prior report |
| T2   | ✅ Done | `approve`/`reject`/`find_reimbursement_state`/`record_human_review_decision` + repository tests — unchanged |
| T3   | ✅ Done | `use_cases.review_reimbursement` — unchanged; test file gains `DescribeTransactionAtomicity` (2 tests) and a strengthened `DescribeRejectReimbursement::it_raises_reimbursement_not_eligible_for_an_ineligible_status` |
| T4   | ✅ Done | `reimbursement/update/validation.py` — unchanged; test file gains `it_raises_when_reason_is_missing_on_reject` and a message-content assertion on the existing missing-field test |
| T5   | ✅ Done | `reimbursement/update/route.py` — unchanged; test file gains a DB-unchanged assertion on the malformed-currency (422) and reject-ineligible (400) route tests |

`ba9a828` is test-only — zero production-code lines changed (`git show --stat`: 3 files, all under `tests/`). The transaction wrapper in `review_reimbursement.py` (the thing under test) is byte-for-byte identical to the prior report's version.

---

## Spec-Anchored Acceptance Criteria

| Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| --------- | --------------------- | ------------------------ | ------ |
| REVIEW-01 Approve happy path | `200`, `reimbursement.status='human-approved'` + `receipts_*` from payload, exactly one `human_review` row | `src/shared/tests/reimbursement/test_repository.py:331-350`, `:409-420` + `test_review_reimbursement.py:44-61` + `src/api/tests/reimbursement/update/test_route.py:76-85` | ✅ PASS (unchanged) |
| REVIEW-02 Approve missing field → `422` | `422`, `{"msg"}` naming the missing field, no DB change | `src/api/tests/reimbursement/update/test_validation.py:40-46` — **now asserts `"receipts_value" in str(exc_info.value)`** (message-content gap closed) + `test_route.py:87-95` (`422`, `"msg"` present) | ✅ PASS — message-content clause closed. "No DB change" clause: not independently asserted by a new test, but structurally guaranteed — `route.py:31-32` calls `validate_review(raw)` before `pool.acquire()` (`route.py:36`) is ever reached, so a validation failure cannot reach any DB call. Noted as a trivial residual, not a gap (see Residual Notes). |
| REVIEW-03 Malformed `receipts_*` → `422` | `422`, no DB change | `test_validation.py:48-64` (date/currency/negative-value) + `test_route.py:97-106` — **now asserts `status == "human-review"` post-response** (no-DB-change clause closed) | ✅ PASS — fully precise, gap closed |
| REVIEW-04 Approve ineligible status → `400` | `400`, no DB change | `test_repository.py:352-367` + `test_review_reimbursement.py:87-106` + `test_route.py:108-114` | ✅ PASS (unchanged) |
| REVIEW-05 Reject happy path | `200`, `human-rejected`, one `human_review` row | `test_repository.py:371-379` + `test_review_reimbursement.py:110-119` + `test_route.py:116-124` | ✅ PASS (unchanged) |
| REVIEW-06 Reject missing `reason`/`approved_by` → `422` | `422` | `test_validation.py:66-70` (`approved_by` missing) + **`test_validation.py:72-78` — new: `reason` missing, plus message-content assertion `"reason" in str(exc_info.value)`** + `test_route.py:126-133` (`approved_by` missing, `422`) | ✅ PASS — the untested `reason`-missing half of the disjunction is now covered |
| REVIEW-07 Unknown uuid → `404` | `404` | `test_review_reimbursement.py:63-85`, `:121-123` + `test_route.py:135-139`, `:210-224` | ✅ PASS (unchanged) |
| REVIEW-08 Ineligible status (existing row) → `400` not `404` | `400`, no DB change | `test_route.py:108-114`, `:141-149` (route: 400, **reject route test now also asserts no DB change, bonus**) + **`test_review_reimbursement.py:125-136` — reject-ineligible use-case test now asserts `status == "human-approved"` and `human_review` count `== 0`** (the specifically-flagged gap) | ✅ PASS — use-case-level "no DB change" gap closed |
| REVIEW-09 Concurrent decisions — exactly one wins | Exactly one `200`, one `400`; exactly one `human_review` row | `test_review_reimbursement.py:206-252` + `test_route.py:179-207` | ✅ PASS (unchanged) |
| REVIEW-10 Reject blocked on incomplete receipts → `400` | `400`, no DB change | `test_repository.py:381-390` + `test_review_reimbursement.py:138-147` + `test_route.py:151-157` | ✅ PASS (unchanged) |

**Status**: 10/10 ACs pass with full spec-clause precision (up from 6/10 fully precise + 4 with flagged spec-precision gaps in the prior report). The one remaining sub-clause gap (REVIEW-02's "no DB change") is closed by code structure rather than by an explicit assertion — see Residual Notes below.

### Edge Cases (spec.md)

- [x] Body `uuid` present and differs from path `uuid` → `400` — `test_route.py:159-168`.
- [x] `status` not `approved`/`rejected` → `422` — `test_validation.py:80-84`.
- [x] **DB transaction fails after the `human_review` insert but before the `reimbursement` update (or vice versa) → roll back both — NOW TESTED.** `test_review_reimbursement.py:150-203` (`DescribeTransactionAtomicity`, 2 tests: approve and reject). Each monkeypatches `record_human_review_decision` (the second write in write-order) to raise `RuntimeError` after the first write (`approve`/`reject`, the `reimbursement.status` UPDATE) has executed but before the transaction commits, then asserts via the same connection that **both** the status change and the `human_review` row are absent. This is the "vice versa" ordering the spec explicitly names (failure after the `reimbursement` write, before the `human_review` write). Confirmed genuinely load-bearing by the discrimination sensor re-run below (mutation killed).
- [x] A `human-review` row with never-extracted receipts can be approved but never rejected — unchanged from prior report, implicitly covered by REVIEW-01 + REVIEW-10.

---

## Discrimination Sensor

Re-run in a fresh isolated `git worktree` under the scratchpad (`sensor-worktree-put`), created via `git worktree add --detach`, never touching the real tree. Reverted (`git checkout --`) after each mutation and removed (`git worktree remove --force`) at the end; real tree confirmed clean (`git status --short` empty, `git worktree list` no longer shows the scratch entry) at the end.

| # | File:line | Description | Killed? |
| - | --------- | ------------ | ------- |
| 1 (**re-verify target**) | `src/shared/src/shared/reimbursement/use_cases/review_reimbursement.py:51,70` (`approve_reimbursement`/`reject_reimbursement`) | Removed the `async with conn.transaction():` wrapper around both writes (the same mutation that survived in the prior report) | ✅ **Now killed** — `uv run pytest src/shared/tests/reimbursement/use_cases/test_review_reimbursement.py -v` → 2 failed, 8 passed. Both new `DescribeTransactionAtomicity` tests fail with the exact expected signature: `AssertionError: assert 'human-approved' == 'human-review'` (approve) and `assert 'human-rejected' == 'human-review'` (reject) — the partial write survives without the transaction boundary, precisely proving the wrapper's necessity. |
| 2 (sanity re-check) | `src/shared/src/shared/reimbursement/use_cases/review_reimbursement.py:28` | `ELIGIBLE_STATUSES` extended to also accept `"human-approved"` | ✅ Killed (6 tests failed across use_case + route, same as prior report) |

**Sensor depth**: targeted re-verification of the one previously-surviving mutation, plus one sanity re-check of a previously-killed mutation to confirm the harness itself still discriminates correctly.
**Result**: 2/2 killed — ✅ PASS (the previously-surviving mutant is now killed; no regression on the sanity-check mutation)

---

## Payload/Conjunction Rule (`human_review` row fields)

Unchanged from the prior report — `ba9a828` did not touch this area:

- **Repository layer** (`test_repository.py:409-420`): full conjunction (`status`, `reviewed_by`, `reason`) plus `len(rows)==1`. Strongest evidence.
- **Use-case layer**: approve's happy-path asserts `status`+`reviewed_by`; reject's happy-path asserts only `status`.
- **Route layer**: neither route test queries `human_review` directly.

**Assessment**: unchanged — satisfied at the repository layer (the layer that owns the SQL), thinner at use-case/route layers for the reject path specifically. Not a blocking gap.

---

## Code Quality

| Principle | Status |
| --------- | ------ |
| Minimum code | ✅ — `ba9a828` is 76 lines, entirely test additions, zero production code touched |
| Surgical changes | ✅ — only the 3 files the fix plan named |
| No scope creep | ✅ |
| Matches patterns | ✅ — `DescribeTransactionAtomicity` follows the same `Describe*`/`it_*` convention; monkeypatch-to-inject-failure is a standard, minimal technique for this kind of test |
| Spec-anchored outcome check (asserted values match spec) | ✅ — all 4 previously-flagged clauses now have direct or structurally-guaranteed coverage |
| Per-layer Coverage Expectation met | ✅ |
| Every test maps to a spec requirement — no unclaimed tests | ✅ — `DescribeTransactionAtomicity`'s docstring cites spec.md's Edge Cases section explicitly |
| Documented guidelines followed | `docs/codebase/TESTING.md` (Describe*/it_* convention) — followed |

---

## Residual Notes (non-blocking)

- **REVIEW-02's "no DB change" clause**: not given its own explicit post-response DB query (unlike REVIEW-03/REVIEW-08, which now have one). This is because `route.py:31-32` runs `validate_review(raw)` strictly before `pool.acquire()` (`route.py:36`) — a validation failure cannot reach any DB call, full stop. An explicit assertion here would be testing a structural guarantee the code already makes impossible to violate without also breaking REVIEW-03's now-covered sibling path (same route, same ordering). Flagged for completeness, not raised as a gap — a staff engineer would not block on this.
- **Reject-path conjunction thinness** (use-case/route layers only assert `status`, not the full `human_review` row): unchanged from prior report, not reintroduced or worsened by this fix, still non-blocking given repository-layer exhaustiveness.

---

## Gate Check

- **Gate command**: `uv run pytest` (full gate, from workspace root, real working tree — not the scratch worktree)
- **Result**: **372 passed, 0 failed, 0 skipped** (1 unrelated deprecation warning re: httpx/TestClient; `rdkafka` connect-refused log lines are expected local-Kafka-absent noise from unrelated publisher tests' teardown, not failures)
- **Test count at prior report** (`HEAD` = `7f5434a`): 363
- **Test count now** (`HEAD` = `5f5203c`): 372
- **Delta**: +9 — decomposes as PUT feature's `ba9a828` (+3: `DescribeTransactionAtomicity` ×2, `it_raises_when_reason_is_missing_on_reject` ×1; the other `ba9a828` changes strengthen existing tests rather than adding new ones) + sibling GET feature's `5f5203c` (+6, out of this feature's scope, verified separately)
- **PUT-scoped tests**: 74/74 passed (`src/shared/tests/reimbursement/use_cases/test_review_reimbursement.py` + `src/api/tests/reimbursement/update/`, 71 prior + 3 new)

---

## Requirement Traceability Update

| Requirement | Previous Status | New Status |
| ----------- | ---------------- | ----------- |
| REVIEW-01 | ✅ Verified | ✅ Verified |
| REVIEW-02 | ⚠️ Verified with spec-precision gap | ✅ Verified |
| REVIEW-03 | ⚠️ Verified with spec-precision gap | ✅ Verified |
| REVIEW-04 | ✅ Verified | ✅ Verified |
| REVIEW-05 | ✅ Verified | ✅ Verified |
| REVIEW-06 | ⚠️ Verified with spec-precision gap | ✅ Verified |
| REVIEW-07 | ✅ Verified | ✅ Verified |
| REVIEW-08 | ⚠️ Verified with spec-precision gap | ✅ Verified |
| REVIEW-09 | ✅ Verified | ✅ Verified |
| REVIEW-10 | ✅ Verified | ✅ Verified |

---

## Summary

**Overall**: ✅ **Ready** — the prior report's blocking issue (surviving mutant on the transaction-atomicity edge case) is resolved, and all 4 minor spec-precision gaps are closed.

**Spec-anchored check**: 10/10 ACs have `file:line` evidence matching the spec-defined status code and DB-state outcome exactly, with zero remaining flagged gaps (one trivial residual noted above, non-blocking).
**Sensor**: previously-surviving mutation (transaction-wrapper removal) now killed by the new `DescribeTransactionAtomicity` tests; sanity re-check of a previously-killed mutation still kills cleanly (no regression in the harness's own discrimination power).
**Gate**: 372 passed, 0 failed (full suite); PUT-scoped tests 74/74 passed; test count integrity confirmed (+9, decomposed and accounted for across both concurrently-verified features).

**What works**: All 10 REVIEW ACs are correctly implemented and tested end-to-end across repository/use-case/route layers. The transaction-atomicity guarantee — the one gap that mattered — now has direct, mutation-confirmed proof: a mid-transaction failure rolls back both writes, exactly as spec.md's Edge Cases section requires. The four minor precision gaps (message content, no-DB-change assertions, the `reason`-missing reject case) are all closed with targeted, minimal test additions — zero production code was touched, consistent with these being test-coverage gaps rather than behavior bugs.

**Issues found**: None blocking. One trivial residual noted (REVIEW-02's "no DB change" clause is structurally guaranteed rather than independently asserted) — not worth a fix task.

**Next steps**: None required for this feature. Ready to proceed (merge/ship) from a verification standpoint.
