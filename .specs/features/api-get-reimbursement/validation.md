# GET /api/v1/reimbursement Validation

**Date**: 2026-08-08
**Spec**: `.specs/features/api-get-reimbursement/spec.md`
**Diff range**: `eeb47c0..0002ff5` (T1–T9; scoped out `api-put-reimbursement`'s later commits `c95118d..7f5434a` on the same branch, which touch overlapping files but different symbols)
**Verifier**: independent sub-agent (author ≠ verifier)

---

## Task Completion

| Task | Status  | Notes |
| ---- | ------- | ----- |
| T1   | ✅ Done | `managed_pool` relocated to `shared/db.py`; `DescribeManagedPool` moved to `test_db.py` unmodified; repository's `DescribeManagedPool` removed. Verified via diff. |
| T2   | ✅ Done | `MAX_LIST_LIMIT = 500` in `shared/config.py:14`; `test_config.py:85` asserts it. |
| T3   | ✅ Done | `ReimbursementFilterInvalid` in `shared/errors.py:18-21`. |
| T4   | ✅ Done | `dependencies.get_pool` + `main.lifespan`'s `replace(config.database, pool_min_size=0)`; proof merge-forwarded into T9 as documented. |
| T5   | ✅ Done | `fetch_reimbursement_page` in `repository.py:130-137`; 7 tests in `DescribeFetchReimbursementPage`. |
| T6   | ✅ Done | `list_reimbursements` use case; 5 tests (4 unit + 1 integration). |
| T7   | ✅ Done | `params.py`; 4 tests. |
| T8   | ✅ Done | `response.py`; 2 tests. |
| T9   | ✅ Done | `route.py` + handler registration; **13 tests actually present** (12 in `DescribeGetReimbursement` + 1 `DescribeTheRealApp`), vs. the task's own stated "+12 (11 route cases + 1 real-app case)" — a harmless off-by-one in the task doc's own count, not a functional gap (all 12 `DescribeGetReimbursement` cases map to a real LIST-* AC). |

All tasks' `Done when` checkboxes are marked `[x]`; no partial/blocked tasks found.

---

## Spec-Anchored Acceptance Criteria

| Requirement | Criterion (WHEN X THEN Y) | Spec-defined outcome | `file:line` + assertion expression | Result |
| ----------- | -------------------------- | --------------------- | ----------------------------------- | ------ |
| LIST-01 | No query params → 200, ≤100 rows, `created_at DESC`, offset 0 | `200`, ordered DESC, default `limit=100`/`offset=0` | Defaults: `src/api/tests/reimbursement/list/test_params.py:17-22` — `assert response.json() == {"limit": 100, "offset": 0}`. Ordering: `src/api/tests/reimbursement/list/test_route.py:80-93` — `assert [...] == [str(newer), str(older)]`. No-params 200: `test_route.py:153-161`, `test_route.py:220-224`. | ⚠️ Spec-precision/composition gap — no single test exercises "zero query params" **and** asserts ordering together; the one test that proves DESC ordering (`test_route.py:80-93`) supplies `status=auto-approved`, and the two literal no-params tests don't assert ordering. Each sub-behavior is proven individually, not the exact conjunction. |
| LIST-02 | `limit`>500, `limit`/`offset`<0, or non-integer → `400`, no query run | `400 {"msg"}`, DB untouched | Limit ceiling: `test_route.py:111-116` — `assert response.status_code == 400`. Offset negative: `test_route.py:118-123`. Negative limit + "no query run" proof (via `conn=None`): `src/shared/tests/reimbursement/use_cases/test_list_reimbursements.py:19-29`. | ⚠️ Partial — bounds violations (ceiling, both negatives) are precisely covered, including the "no query run" guarantee at the use-case layer. **Non-integer** `limit`/`offset` (e.g. `limit=abc`) has **zero test evidence** anywhere in scope. |
| LIST-03 | No matching rows → `200 {data: []}` | `200`, `data == []` | `test_route.py:125-130` — `assert response.json()["data"] == []`. Repository level: `src/shared/tests/reimbursement/test_repository.py:287-292` — `assert page == []`. | ✅ PASS |
| LIST-04 | Single status filter | Only matching-status rows returned | `test_route.py:132-139` — `assert [...] == ["REQ-SINGLE-A"]`. `test_repository.py:252-258`. | ✅ PASS |
| LIST-05 | Comma-separated multi-status filter | Rows matching any listed status, no others | `test_route.py:141-151` — `assert {...} == {"REQ-MULTI-A", "REQ-MULTI-B"}`. `test_repository.py:260-269`. | ✅ PASS |
| LIST-06 | Status omitted → all statuses incl. `pending` | Both `pending` and non-pending rows returned | `test_route.py:153-161` — `assert {"REQ-ALL-PENDING", "REQ-ALL-APPROVED"} == request_ids`. `test_repository.py:271-285`. | ✅ PASS |
| LIST-07 | Invalid status segment (incl. `pending`) → `400`, whole filter rejected | `400 {"msg"}` | Single invalid value: `test_route.py:163-168` (`status=pending`) — `assert response.status_code == 400`. Use-case unit: `test_list_reimbursements.py:13-17`. | ⚠️ Partial — single-invalid-value case is precisely covered. Spec's **own Independent Test** for this story explicitly calls out `status=human-review,pending → 400` (one invalid segment among valid ones invalidating the whole filter) — **zero test evidence** for that mixed-segment case anywhere in scope. |
| LIST-08 | Repeated `status` param → `400` | `400 {"msg"}` | `test_route.py:170-177` — `assert response.status_code == 400`. | ✅ PASS |
| LIST-09 | `human_review` row exists → most recent one as `last_human_review` | Newest by `created_at DESC` returned, older suppressed | `test_route.py:179-196` — `assert by_uuid[...]["last_human_review"]["reason"] == "second look"`. `test_repository.py:294-313`. `src/api/tests/reimbursement/list/test_response.py:24-53`. | ✅ PASS |
| LIST-10 | No `human_review` row → `last_human_review: null` | `null` | `test_route.py:196` — `assert by_uuid[...]["last_human_review"] is None`. `test_repository.py:312-313`. `test_response.py:55-66`. | ✅ PASS |
| LIST-11 | DB pool/connection failure → `500 {"msg"}`, logged | `500`, `{"msg": "..."}`, failure logged | `test_route.py:198-203` — `assert response.status_code == 500` and `assert response.json() == {"msg": "internal error"}`. | ⚠️ Partial — status/body precisely covered. The "**and log the failure**" clause has **zero test evidence** (no `caplog` assertion), despite the project having an established caplog-based pattern for exactly this kind of check (`src/api/tests/reimbursement/create/test_producer.py:112-125`). |

**Status**: ❌ Gaps present — 7/11 fully matched spec outcome, 4/11 have a real sub-clause with zero test evidence (LIST-01, LIST-02, LIST-07, LIST-11). No AC is entirely uncovered.

---

## Discrimination Sensor

Run in an isolated scratch `git worktree` at commit `0002ff5` (GET feature complete, before `api-put-reimbursement`'s commits) — the real working tree was never touched. Each mutation was applied, tested, then reverted (`git checkout --`) before the next.

| # | File:line | Description | Killed? |
| - | --------- | ------------ | ------- |
| 1 | `src/shared/src/shared/reimbursement/use_cases/list_reimbursements.py:25` | Appended `and False` to the status-whitelist gate, disabling it | ✅ Killed — 2 failed (`test_list_reimbursements.py::it_raises_when_a_status_is_outside_the_client_facing_whitelist`, `test_route.py::it_returns_400_for_an_invalid_status_value`) |
| 2 | `src/shared/src/shared/reimbursement/use_cases/list_reimbursements.py:27` | `MAX_LIST_LIMIT` → `MAX_LIST_LIMIT + 1` (off-by-one on the ceiling) | ✅ Killed — 2 failed (`test_list_reimbursements.py::it_raises_when_limit_exceeds_the_ceiling`, `test_route.py::it_returns_400_when_limit_exceeds_the_ceiling`) |
| 3 | `src/shared/src/shared/reimbursement/repository.py:52` | Removed `ORDER BY r.created_at DESC` from `_FETCH_REIMBURSEMENT_PAGE` | ❌ **Survived** — all 20 targeted tests still passed, including `test_repository.py::it_orders_results_by_created_at_descending`. Root cause: every ordering-sensitive test filters by a single `status`, which Postgres serves via the `reimbursement_status_created_idx (status, created_at DESC)` index — the index scan incidentally still returns rows in DESC order even with the explicit `ORDER BY` clause gone. Re-confirmed in isolation (single-test rerun, same result). |
| 4 | `src/shared/src/shared/reimbursement/repository.py:44` | `LEFT JOIN LATERAL` → `JOIN LATERAL` (inner join silently drops the no-review case instead of returning `NULL`) | ✅ Killed — 12 failed across `test_repository.py` and `test_route.py` (e.g. `KeyError` on the row that should have `last_human_review: None`) |
| 5 | `src/api/src/main.py:28` | Dropped `replace(config.database, pool_min_size=0)`, using the shared default `pool_min_size=2` | ✅ Killed — 5 failed (`test_health.py`, `test_main.py` ×3, `create/test_route.py::DescribeTheRealApp`) with `asyncpg.exceptions.InvalidPasswordError` at lifespan construction, exactly the failure mode the design doc's Risks table predicted |

**Sensor depth**: lightweight (5 targeted mutations, default tier)
**Result**: 4/5 killed — ❌ FAIL (mutant #3 survived; per the skill's rule, a surviving mutant blocks marking the feature done until strengthened)

---

## Code Quality

| Principle        | Status |
| ---------------- | ------ |
| Minimum code     | ✅ |
| Surgical changes | ✅ — every changed file traces to a task in tasks.md |
| No scope creep   | ✅ |
| Matches patterns | ✅ — `list_reimbursements` mirrors `send_human_review`'s shape; `route.py` mirrors `create/route.py`; `FakePool` reuses `publisher/tests/fakes.py::RealPool`'s technique as documented |
| Spec-anchored outcome check (asserted values match spec) | ⚠️ 4 sub-clause gaps (see AC table) |
| Per-layer Coverage Expectation met (domain 1:1 ACs; routes happy+edge+error) | ⚠️ mostly met; non-integer bounds and mixed-segment invalid filter absent at every layer |
| Every test maps to a spec requirement — no unclaimed tests | ✅ — spot-checked all new test files; every test traces to a LIST-* AC, an edge case, or a Done-when bullet |
| Documented guidelines followed | `docs/codebase/TESTING.md` (cited directly in tasks.md's Test Coverage Matrix) — followed |

---

## Edge Cases

- [ ] `offset` beyond total row count → `200 {data: []}` — **NOT directly tested**. The only "empty result" tests (`test_route.py:125-130`, `test_repository.py:287-292`) use a non-matching `status`, not an out-of-range `offset` against real rows. `LIMIT`/`OFFSET` SQL semantics make this almost certainly correct, but there is no test evidence.
- [ ] Duplicate status in comma list (`status=human-review,human-review`) → single-value semantics, no dup rows — **NOT tested**. `ANY($1)` naturally avoids duplication (no join fan-out), but no test asserts it.
- [x] `pending` requested explicitly → `400` — `test_route.py:163-168`
- [x] Repeated `status` query param → `400` — `test_route.py:170-177`

---

## Gate Check

- **Gate command**: `uv run pytest` (full, from workspace root)
- **Result**: 363 passed, 0 failed, 0 skipped
- **Test count before feature** (`eeb47c0`, pre-GET-work): 295 collected
- **Test count after GET feature only** (`0002ff5`, pre-PUT-work): 327 passed
- **Delta attributable to this feature**: +32 (T1 net 0 relocation + T2 +1 + T5 +7 + T6 +5 + T7 +4 + T8 +2 + T9 +13 — one more than the task's own stated "+12", see Task Completion note)
- **Skipped tests**: none
- **Failures**: none
- **Current HEAD** (`7f5434a`, includes sibling `api-put-reimbursement` work through its own T5): 363 passed — consistent, no regression from GET's work

---

## Fix Plans

### Fix 1 (Blocker for sensor pass): Ordering guarantee not actually pinned by tests

- **Root cause**: every ordering-sensitive test (`test_repository.py::it_orders_results_by_created_at_descending`, `test_route.py::it_returns_200_with_rows_ordered_created_at_desc_by_default`) filters on a single `status`, which Postgres can (and does) serve via the `reimbursement_status_created_idx (status, created_at DESC)` index scan — coincidentally preserving DESC order even with the explicit `ORDER BY r.created_at DESC` clause removed.
- **Fix task**: Add or strengthen a repository-level test that cannot be satisfied by incidental index-scan order — e.g. seed rows across the **no-filter** path (which cannot use the status-scoped index the same way) with out-of-insertion-order timestamps, or force a sequential scan (`SET LOCAL enable_indexscan = off` for that connection) and assert order still holds.
- **Priority**: Major — this is the query's own explicit correctness guarantee (LIST-01, the edge-case ordering requirement) and is currently unproven by the suite.

### Fix 2: Non-integer `limit`/`offset` untested (LIST-02)

- **Root cause**: design.md correctly notes this path needs "zero new code" (native FastAPI `RequestValidationError` → `_validation_handler`), but no test was written to prove it for this route.
- **Fix task**: Add `test_route.py` case: `GET /api/v1/reimbursement?limit=abc` → `400 {"msg"}`.
- **Priority**: Minor.

### Fix 3: Mixed valid/invalid status segment untested (LIST-07)

- **Root cause**: only the single-invalid-value case (`status=pending`) is tested; spec.md's own Independent Test for this story (`status=human-review,pending → 400`) has no corresponding test.
- **Fix task**: Add a test asserting `status=human-review,pending` → `400`.
- **Priority**: Minor.

### Fix 4: "Log the failure" clause untested (LIST-11)

- **Root cause**: `it_returns_500_on_a_simulated_pool_failure` checks status/body only.
- **Fix task**: Add a `caplog`-based assertion (matching `test_producer.py:112-125`'s existing pattern) confirming `logger.exception` fired on the pool-failure path.
- **Priority**: Minor.

### Fix 5 (optional): Edge cases without direct test coverage

- Add a route or repository test for `offset` beyond total row count → `200 {data: []}`.
- Add a route or repository test for a duplicated status segment (`status=human-review,human-review`) → single-value semantics.
- **Priority**: Minor.

---

## Requirement Traceability Update

| Requirement | Previous Status | New Status |
| ----------- | ---------------- | ---------- |
| LIST-01 | Pending | ⚠️ Needs Fix (composition gap) |
| LIST-02 | Pending | ⚠️ Needs Fix (non-integer sub-case) |
| LIST-03 | Pending | ✅ Verified |
| LIST-04 | Pending | ✅ Verified |
| LIST-05 | Pending | ✅ Verified |
| LIST-06 | Pending | ✅ Verified |
| LIST-07 | Pending | ⚠️ Needs Fix (mixed-segment sub-case) |
| LIST-08 | Pending | ✅ Verified |
| LIST-09 | Pending | ✅ Verified |
| LIST-10 | Pending | ✅ Verified |
| LIST-11 | Pending | ⚠️ Needs Fix (logging sub-clause) |

---

## Summary

**Overall**: ⚠️ Issues — functionally solid (363/363 passing, 4/5 mutations killed, no incorrect behavior found), but the discrimination sensor surfaced one real weak-test finding (ordering isn't robustly pinned) and 4 AC sub-clauses have zero test evidence. Per the skill's rule, a surviving mutant blocks marking the feature done as-is.

**Spec-anchored check**: 7/11 ACs fully matched spec outcome; 4/11 have a real, narrow sub-clause gap (LIST-01, LIST-02, LIST-07, LIST-11)
**Sensor**: 4/5 mutations killed, 1 survived (ordering)
**Gate**: 363 passed, 0 failed

**What works**: Pagination defaults/custom values, status whitelist enforcement, multi-status `ANY()` filtering, `pending`-inclusion on no filter, last-human-review enrichment (present/absent), 500 on pool failure, `pool_min_size=0` lazy-connect wiring, `managed_pool` relocation (AD-029) — all precisely verified with matching evidence.

**Issues found**:
1. Discrimination sensor: dropping `ORDER BY created_at DESC` survives the full suite — see Fix 1.
2. `limit`/`offset` non-integer → 400 has no test — see Fix 2.
3. `status=<valid>,<invalid>` mixed-segment → 400 has no test (spec's own stated Independent Test) — see Fix 3.
4. 500-path "log the failure" clause has no test — see Fix 4.
5. Two spec.md Edge Cases (offset-beyond-count, duplicate-status-segment) have no test — see Fix 5.

**Next steps**: Route Fixes 1–5 as fix tasks to an implementer; re-verify (max 3 fix→re-verify iterations per the skill's guardrail). None of the 5 findings indicate incorrect production behavior — all are test-coverage gaps.
