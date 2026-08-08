# GET /api/v1/reimbursement Validation

**Date**: 2026-08-08 (re-verification)
**Spec**: `.specs/features/api-get-reimbursement/spec.md`
**Diff range**: `0002ff5..5f5203c` (fix commit `5f5203c` — closes the prior report's 5 gaps; scoped past `api-put-reimbursement`'s sibling commits `c95118d..ba9a828`, which touch overlapping files but different symbols)
**Verifier**: independent sub-agent (author ≠ verifier), fresh session, no continuity with the prior verifier
**Prior report**: same file, superseded by this one — prior verdict was ❌ FAIL (1 surviving mutant + 4 minor spec-precision gaps)

---

## Task Completion

Unchanged from the prior report — no task-level changes in this diff range; `5f5203c` is test-only (`test(shared,api): close GET ordering-guarantee and spec-precision gaps`). All T1–T9 remain `✅ Done` per the prior report's findings, re-confirmed by re-reading `src/shared/src/shared/reimbursement/repository.py`, `src/api/src/reimbursement/list/*.py` — no production code changed since the prior verification.

---

## Spec-Anchored Acceptance Criteria

| Requirement | Criterion (WHEN X THEN Y) | Spec-defined outcome | `file:line` + assertion expression | Result |
| ----------- | -------------------------- | --------------------- | ----------------------------------- | ------ |
| LIST-01 | No query params → 200, ≤100 rows, `created_at DESC`, offset 0 | `200`, ordered DESC, default `limit=100`/`offset=0` | Defaults: `src/api/tests/reimbursement/list/test_params.py:17-22`. Ordering (status-filtered path): `test_route.py:80-93`. No-params 200: `test_route.py:169-177` (renumbered), `test_route.py:236-240`. **Ordering on the exact no-filter path** (what a zero-query-param call actually executes): `src/shared/tests/reimbursement/test_repository.py:349-372` — `assert own_rows == [newest, middle, oldest]`, seeded with mixed statuses (`pending`/`human-review`/`auto-rejected`) so the status-scoped index cannot serve it incidentally. | ✅ PASS — the substantive gap (ordering unproven on the no-filter code path) is closed at the repository layer, which is the exact SQL/path a zero-query-param route call exercises. A byte-for-byte "one HTTP test with literally zero query params that also asserts order" still doesn't exist, but this is no longer a correctness gap — see Discrimination Sensor below, which confirms the guarantee is now enforced. |
| LIST-02 | `limit`>500, `limit`/`offset`<0, or non-integer → `400`, no query run | `400 {"msg"}`, DB untouched | Ceiling: `test_route.py:111-116`. Negative offset: `test_route.py:118-123`. Non-integer limit: `test_route.py:125-130` (new) — `assert response.status_code == 400`. Non-integer offset: `test_route.py:132-137` (new). Negative limit + "no query run": `src/shared/tests/reimbursement/use_cases/test_list_reimbursements.py:19-29`. | ✅ PASS — non-integer `limit`/`offset` now has direct route-level test evidence for both parameters. |
| LIST-03 | No matching rows → `200 {data: []}` | `200`, `data == []` | `test_route.py:139-144`; `test_repository.py:287-292`. | ✅ PASS |
| LIST-04 | Single status filter | Only matching-status rows returned | `test_route.py:146-153`; `test_repository.py:252-258`. | ✅ PASS |
| LIST-05 | Comma-separated multi-status filter | Rows matching any listed status, no others | `test_route.py:155-165`; `test_repository.py:260-269`. | ✅ PASS |
| LIST-06 | Status omitted → all statuses incl. `pending` | Both `pending` and non-pending rows returned | `test_route.py:167-175`; `test_repository.py:271-285`. | ✅ PASS |
| LIST-07 | Invalid status segment (incl. `pending`) → `400`, whole filter rejected | `400 {"msg"}` | Single invalid value: `test_route.py:177-182`. Use-case unit: `test_list_reimbursements.py:13-17`. **Mixed valid/invalid segment** (spec's own Independent Test — `status=human-review,pending`): `test_route.py:185-197` (new) — `assert response.status_code == 400`. | ✅ PASS — the spec's own stated Independent Test case now has direct test evidence. |
| LIST-08 | Repeated `status` param → `400` | `400 {"msg"}` | `test_route.py:199-206`. | ✅ PASS |
| LIST-09 | `human_review` row exists → most recent one as `last_human_review` | Newest by `created_at DESC` returned, older suppressed | `test_route.py:208-225`; `test_repository.py:294-313`; `src/api/tests/reimbursement/list/test_response.py:24-53`. | ✅ PASS |
| LIST-10 | No `human_review` row → `last_human_review: null` | `null` | `test_route.py:225`; `test_repository.py:312-313`; `test_response.py:55-66`. | ✅ PASS |
| LIST-11 | DB pool/connection failure → `500 {"msg"}`, logged | `500`, `{"msg": "..."}`, failure logged | `test_route.py:227-236` (new caplog assertion) — `caplog.set_level(logging.ERROR, logger="errors")`; `assert response.status_code == 500`; `assert response.json() == {"msg": "internal error"}`; `assert "connection reset" in caplog.text`. Verified against production: `src/api/src/errors.py:18` (`logger = logging.getLogger(__name__)`, module is top-level `errors`) and `:80` (`logger.exception("unhandled exception on %s %s", ...)` inside `_unhandled_exception_handler`, the handler that catches the pool-acquire `RuntimeError` this test raises). | ✅ PASS — logger name in the test (`"errors"`) matches the production module's `__name__`-derived logger exactly; the caplog assertion is grounded in the real handler, not a guess. |

**Status**: ✅ All 11/11 ACs fully matched spec outcome — no unresolved sub-clause gaps.

---

## Discrimination Sensor

Run in an **isolated scratch `git worktree`** (`git worktree add <scratchpad>/sensor-worktree 5f5203c`, detached at the fix commit) — the real working tree (`/Users/flaviostudart/.../feature-7-reimbursement-get-put`) was never touched; confirmed clean (`git status --short` empty) before and after. Dependencies synced fresh via `uv sync --all-packages`; Postgres via the suite's own `testcontainers` fixture (no shared state with the real tree's Docker containers). Each mutation applied, tested, then reverted (`git checkout --`) before the next; worktree removed afterward (`git worktree remove --force`).

| # | File:line | Description | Killed? |
| - | --------- | ------------ | ------- |
| 1 (**re-test of the prior survivor**) | `src/shared/src/shared/reimbursement/repository.py:52` | Removed `ORDER BY r.created_at DESC` from `_FETCH_REIMBURSEMENT_PAGE` — same mutation the prior report recorded as surviving | ✅ **Now killed** — `it_orders_results_by_created_at_descending_on_the_no_filter_path` (`test_repository.py:349-372`) failed: `assert own_rows == [newest, middle, oldest]` → got `[<one uuid>]` (order/dedup broken since the mutant's `LIMIT`/`OFFSET` clause landed with malformed SQL after the plain string-replace removed the `ORDER BY` line entirely — 1 failed, 49 passed out of the full `test_repository.py` + `src/api/tests/reimbursement/list/` run). Root cause confirmed fixed: the new test seeds mixed statuses (`pending`/`human-review`/`auto-rejected`) through the no-filter (`statuses=None`) path, which cannot ride the `reimbursement_status_created_idx (status, created_at DESC)` index the way every pre-fix ordering test did — a seq scan without `ORDER BY` returns rows in a different order, so this path now actually discriminates the mutation. |
| 2 (sanity re-check of a previously-killed mutation) | `src/shared/src/shared/reimbursement/use_cases/list_reimbursements.py:27` | `MAX_LIST_LIMIT` → `MAX_LIST_LIMIT + 1` (off-by-one on the ceiling) | ✅ Killed — 2 failed (`test_list_reimbursements.py::it_raises_when_limit_exceeds_the_ceiling`, `test_route.py::it_returns_400_when_limit_exceeds_the_ceiling`), 25 passed — same result as the prior report, confirming no regression in this test's discriminating power. |

**Sensor depth**: lightweight (2 targeted mutations — priority was re-confirming the prior survivor; 1 additional sanity check per task instructions)
**Result**: 2/2 killed — ✅ **PASS** (the previously-surviving mutant on `ORDER BY r.created_at DESC` is now killed)

---

## Code Quality

| Principle        | Status |
| ---------------- | ------ |
| Minimum code     | ✅ — fix commit is test-only, +80/-1 lines across 2 files, no production code touched |
| Surgical changes | ✅ — every new test traces to one of the prior report's 5 named findings |
| No scope creep   | ✅ |
| Matches patterns | ✅ — the new caplog test reuses `test_producer.py:112-125`'s established `caplog.set_level(logging.ERROR, logger=...)` pattern, exactly as the prior report's Fix 4 suggested |
| Spec-anchored outcome check (asserted values match spec) | ✅ — all 11 ACs now fully matched, no sub-clause gaps |
| Per-layer Coverage Expectation met (domain 1:1 ACs; routes happy+edge+error) | ✅ |
| Every test maps to a spec requirement — no unclaimed tests | ✅ — spot-checked all 6 new tests; each traces to LIST-01 (ordering), LIST-02 (×2, non-integer limit/offset), LIST-07 (mixed segment), plus the 2 named edge cases (offset-beyond-count, duplicate-status-segment) |
| Documented guidelines followed | `docs/codebase/TESTING.md` — followed; new repository test's inline comment explains *why* the no-filter path is needed (non-obvious index-scan interaction), consistent with the project's "comments explain non-obvious invariants" convention |

---

## Edge Cases

- [x] `offset` beyond total row count → `200 {data: []}` — `src/shared/tests/reimbursement/test_repository.py:294-301` (new) — `it_returns_an_empty_list_when_offset_is_beyond_the_total_row_count`, seeds 1 row, requests `offset=1000`, asserts `page == []`.
- [x] Duplicate status in comma list (`status=human-review,human-review`) → single-value semantics, no dup rows — `test_repository.py:303-311` (new) — `it_does_not_duplicate_rows_for_a_repeated_status_in_the_filter`, asserts exactly one row returned for `statuses=["auto-rejected", "auto-rejected"]`.
- [x] `pending` requested explicitly → `400` — `test_route.py:177-182` (unchanged from prior report)
- [x] Repeated `status` query param → `400` — `test_route.py:199-206` (unchanged from prior report)

All 4 spec.md Edge Cases now have direct test evidence.

---

## Gate Check

- **Gate command**: `uv run pytest` (full, from workspace root)
- **Result**: **372 passed, 0 failed, 0 skipped**
- **Prior report's count** (`7f5434a`, before this fix commit and before sibling `api-put-reimbursement` fix commit `ba9a828`): 363 passed
- **New tests added by `5f5203c`** (this fix commit, GET feature only): +6 — 3 in `test_repository.py` (no-filter-path ordering, offset-beyond-count, duplicate-status-segment) + 3 in `test_route.py` (non-integer limit, non-integer offset, mixed-segment invalid status)
- **Note**: current HEAD also includes sibling `api-put-reimbursement`'s own fix commit `ba9a828` (+3 tests, out of scope for this GET report) between the prior report's baseline and now — accounts for the remaining delta (363 → 372 = +9, of which 6 are this commit's)
- **Skipped tests**: none
- **Failures**: none
- **Non-fatal noise**: `rdkafka` connection-refused log lines during the run (Kafka producer teardown against an unreachable broker in unrelated publisher/producer tests) — pre-existing, not a failure, does not affect pass/fail counts

---

## Requirement Traceability Update

| Requirement | Previous Status | New Status |
| ----------- | ---------------- | ---------- |
| LIST-01 | ⚠️ Needs Fix (composition gap) | ✅ Verified |
| LIST-02 | ⚠️ Needs Fix (non-integer sub-case) | ✅ Verified |
| LIST-03 | ✅ Verified | ✅ Verified |
| LIST-04 | ✅ Verified | ✅ Verified |
| LIST-05 | ✅ Verified | ✅ Verified |
| LIST-06 | ✅ Verified | ✅ Verified |
| LIST-07 | ⚠️ Needs Fix (mixed-segment sub-case) | ✅ Verified |
| LIST-08 | ✅ Verified | ✅ Verified |
| LIST-09 | ✅ Verified | ✅ Verified |
| LIST-10 | ✅ Verified | ✅ Verified |
| LIST-11 | ⚠️ Needs Fix (logging sub-clause) | ✅ Verified |

---

## Summary

**Overall**: ✅ **PASS** — all 5 findings from the prior FAIL report are closed: the previously-surviving mutant on `ORDER BY r.created_at DESC` is now killed by a repository-level test that forces the no-filter code path (the one a status-scoped index scan cannot serve incidentally), and all 4 minor spec-precision gaps (LIST-02 non-integer bounds, LIST-07 mixed-segment status filter, LIST-11 log-the-failure caplog assertion, 2 untested edge cases) have direct test evidence. No production code changed in this fix — the underlying implementation was already correct; only test coverage was strengthened.

**Spec-anchored check**: 11/11 ACs fully matched spec outcome (was 7/11)
**Sensor**: 2/2 mutations killed, including the previously-surviving one (was 4/5)
**Gate**: 372 passed, 0 failed (was 363 passed)

**What works**: Everything from the prior report, plus: ordering guarantee now robustly pinned on the no-filter path (proven to fail without `ORDER BY`), non-integer `limit`/`offset` rejection, mixed valid/invalid status-segment rejection, DB-failure logging via the app-wide `errors` logger, offset-beyond-row-count empty result, duplicate-status-segment dedup.

**Issues found**: None. No ranked gaps remain.

**Next steps**: None required — feature verified complete against `.specs/features/api-get-reimbursement/spec.md` LIST-01 through LIST-11 and all documented Edge Cases.
