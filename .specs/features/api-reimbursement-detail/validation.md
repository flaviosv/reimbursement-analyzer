# api-reimbursement-detail Validation

**Date**: 2026-08-09
**Spec**: `.specs/features/api-reimbursement-detail/spec.md`
**Diff range**: `5969f21..HEAD` (feature commits `bfa3f9e`, `b434ef0`, `f76d84d`, `a130ace`, `cfd3c10`, `e0071bd`; `c614634` is docs-only, excluded from code review)
**Verifier**: independent sub-agent (author ≠ verifier)

---

## Task Completion

| Task | Status  | Notes |
| ---- | ------- | ----- |
| T1: `fetch_reimbursement_by_uuid` repository query | ✅ Done | — |
| T2: `get_reimbursement` use case | ✅ Done | — |
| T3: Relocate item/response models | ✅ Done | — |
| T4: Point list slice at relocated model | ✅ Done | — |
| T5: Add `GET /api/v1/reimbursement/{uuid}` | ✅ Done | — |
| T6: PUT returns updated payload | ✅ Done | — |

---

## Spec-Anchored Acceptance Criteria

| Criterion (WHEN X THEN Y) | Spec-defined outcome | `file:line` + assertion | Result |
| -------------------------- | --------------------- | ------------------------ | ------ |
| DETAIL-01: GET with matching `uuid` → 200 + `data` (incl. `last_human_review`) | `200`, `{"msg": "reimbursement found", "data": {...}}`, `data` = full item shape, `last_human_review` populated or `null` | `packages/api/tests/reimbursement/get/test_route.py:21-31` — `assert response.status_code == 200; assert body["msg"] == "reimbursement found"; assert body["data"]["uuid"] == str(uuid); assert body["data"]["status"] == "human-approved"` and `:33-50` — `assert with_review_response.json()["data"]["last_human_review"]["reason"] == "second look"; assert without_review_response.json()["data"]["last_human_review"] is None` | ✅ PASS |
| DETAIL-02: GET well-formed `uuid`, no match → 404 | `404`, `{"msg": "..."}` | `packages/api/tests/reimbursement/get/test_route.py:52-61` — `assert response.status_code == 404; assert response.json() == {"msg": f"no reimbursement with uuid {unknown_uuid}"}` | ✅ PASS |
| DETAIL-03: GET malformed `uuid` path segment → 400, no query run | `400`, `{"msg": "..."}`, no query executed | `packages/api/tests/reimbursement/get/test_route.py:63-70` — `assert response.status_code == 400; assert "uuid" in response.json()["msg"]` (proven not-run via `FakePool(None)`: a real query against a `None` connection would raise, not return a clean 400) | ✅ PASS |
| DETAIL-04: DB pool failure → 500 + log | `500`, `{"msg": "..."}`, failure logged | `packages/api/tests/reimbursement/get/test_route.py:72-83` — `assert response.status_code == 500; assert response.json() == {"msg": "internal error"}; assert "connection reset" in caplog.text` | ✅ PASS |
| DETAIL-05: PUT success → 200 + `data` reflecting post-commit state | `200`, `data` = same shape as GET, built after decision commit, incl. just-inserted `last_human_review` | `packages/api/tests/reimbursement/update/test_route.py:41-55` (approve) and `:57-71` (reject) — `assert body["data"]["status"] == "human-approved"`/`"human-rejected"`; `assert body["data"]["last_human_review"]["status"] == "approved"`/`"rejected"`, matching submitted `reviewed_by`/`reason`. Byte-identical-to-GET edge case: `packages/api/tests/reimbursement/update/test_route.py:317-340` — `assert put_response.json()["data"] == get_response.json()["data"]` | ✅ PASS |
| DETAIL-06: PUT error responses (`400`/`404`/`413`/`422`/`500`) unchanged | Existing `{"msg": "..."}` bodies, no `data` added | `packages/api/tests/reimbursement/update/test_route.py:73-219` (422 field-missing, 422 malformed currency, 400 ineligible status, 404 unknown uuid, 400 uuid mismatch, 500 pool failure — all pre-existing assertions, unmodified) | ✅ PASS |

**Status**: ✅ All ACs covered

---

## Edge Cases

- [x] Row with no `human_review` ever → both GET and PUT show `last_human_review: null` — `get/test_route.py:47,50` (`without_review`), `update/test_route.py` implicitly via approve/reject always inserting a review so PUT's own edge (first-ever review, never `null`) is what's directly proven.
- [x] PUT decision is the row's first-ever `human_review` row → PUT's `last_human_review` reflects it, never `null` — `update/test_route.py:41-55`, `:57-71` (both seed a row with *no* prior review, then assert `last_human_review` is populated post-PUT).
- [x] Two back-to-back GETs with no state change → identical `data` — not directly tested by an explicit "two GETs" test, but structurally guaranteed (same `get_reimbursement` read path, no mutation between calls) and covered transitively by the PUT/GET byte-identical test (`update/test_route.py:317-340`), which is a strictly stronger version of the same guarantee (write between reads, still identical). ⚠️ Minor spec-precision gap: no test literally issues two consecutive `GET`s.

---

## Discrimination Sensor

| Mutation | File:line | Description | Killed? |
| -------- | --------- | ------------ | ------- |
| 1 | `packages/api/src/api/reimbursement/response.py:41` | Flipped `record["hr_status"] is not None` → `is None` in `ReimbursementItem.from_record` | ✅ Killed (6 tests failed: `test_response.py` x4, `get/test_route.py` x2) |
| 2 | `packages/shared/src/shared/reimbursement/repository.py:82` | Inverted `_FETCH_REIMBURSEMENT_BY_UUID`'s `WHERE r.uuid = $1` → `WHERE r.uuid != $1` | ✅ Killed (4 tests failed: `test_repository.py::DescribeFetchReimbursementByUuid` x2, `get/test_route.py` x2) |
| 3 | `packages/api/src/api/reimbursement/update/route.py:45-59` | Moved the `get_reimbursement` re-fetch to *before* the write, so PUT returns pre-decision (stale) data instead of post-commit state | ✅ Killed (3 tests failed: `update/test_route.py::it_returns_the_updated_payload_on_a_successful_approve`, `..._reject`, `DescribeTheRealApp::it_returns_a_put_payload_byte_identical_to_a_subsequent_get`) |

**Sensor depth**: lightweight (3 targeted mutations, default tier — this is not a P0 domain-logic path in the mutmut/Stryker sense, it's read/response-shaping code layered on top of the already-covered write path)
**Result**: 3/3 killed — PASS ✅

All mutations reverted via `git checkout -- <file>` immediately after each run; working tree confirmed clean (`git status --porcelain` empty) after each revert and at sensor completion.

---

## Code Quality

| Principle | Status |
| --------- | ------ |
| Minimum code | ✅ — each new file/function is exactly what design.md specified; no speculative parameters or options |
| Surgical changes | ✅ — `list/response.py` and `list/route.py` touched only for the mechanical rename/import move; `update/route.py`'s diff is the minimal re-fetch-and-wrap addition |
| No scope creep | ✅ — no new fields, no auth, no change to PUT's non-2xx bodies (matches spec's explicit Out of Scope table) |
| Matches existing patterns | ✅ — `get/route.py` mirrors `list/route.py`'s `pool.acquire(timeout=...)` pattern and `update/route.py`'s `uuid: UUID` param; `fetch_reimbursement_by_uuid` copies `_FETCH_REIMBURSEMENT_PAGE`'s LATERAL shape verbatim, kept as its own statement per the module's "one statement, one job" convention |
| Would senior engineer approve? | ✅ — the one flagged architectural tension (`update/route.py` importing a read use case) is named and justified in design.md's Risks & Concerns, not silently introduced |
| Tests map to acceptance criteria and are non-shallow (spot-check one story) | ✅ — spot-checked P1 "PUT returns the updated single-item payload": both approve and reject assert `data.status`, `data.last_human_review.status/reviewed_by/reason` against the exact submitted values, plus a dedicated byte-identical-to-GET test — not shallow "response exists" assertions |
| Spec-anchored outcome check: each test's asserted value matches the spec-defined outcome (or gap flagged) | ✅ — see table above; one minor spec-precision gap flagged (back-to-back GET edge case, covered transitively) |
| Per-layer Coverage Expectation met: domain logic has 1:1 AC mapping; routes/e2e cover happy + edge + error paths for every route in scope | ✅ — matches the Test Coverage Matrix in tasks.md exactly: repository (found-with-review/found-without/not-found), use case (found/raises), response model (3 record-mapping cases + composition), GET route (200/404/400/500 + real-app wiring), PUT route (200 approve/reject + all 5 existing error paths regression-checked) |
| Every test in scope maps to a spec AC, listed edge case, or Done-when criterion (no unclaimed tests) | ✅ — every new test traces to a task's Done-when list; no orphan/exploratory tests found |
| Documented project quality/testing guidelines followed (cite guideline file, or "none — strong defaults applied") | ✅ — `docs/codebase/TESTING.md`, cited directly in tasks.md's Test Coverage Matrix header; gate commands run exactly as documented there |

---

## Gate Check

- **Gate command**: `uv run pytest --deselect "packages/reimbursement/tests/test_integration.py::DescribeTheEndToEndRoundTrip::it_resolves_a_fresh_message_for_a_real_row"` (Full gate per tasks.md; the deselected test is a pre-existing Kafka-testcontainer flake, confirmed present identically on unmodified `main`/base `5969f21`, unrelated to this feature)
- **Result**: 461 passed, 0 failed, 1 deselected
- **Test count before feature** (base `5969f21`, verified via a throwaway `git worktree add`): 447 collected
- **Test count after feature** (HEAD): 462 collected
- **Delta**: +15 new tests — reconciles exactly against the per-task counts: T1 +3 (repository), T2 +2 (use case), T3/T4 net +1 (3 relocated tests deleted from `list/test_response.py`, 4 landed in the new `test_response.py`), T5 +6 (`get/test_route.py`: 5 `DescribeGetReimbursementByUuid` cases + 1 `DescribeTheRealApp`, one more than the task's "5 new tests" Done-when — extra coverage, not a shortfall), T6 +3 (`update/test_route.py`)
- **Skipped tests**: none
- **Failures**: none

---

## Fix Plans

None — no gaps or surviving mutants found.

---

## Requirement Traceability Update

| Requirement | Previous Status | New Status  |
| ----------- | ---------------- | ------------ |
| DETAIL-01   | Implementing      | ✅ Verified  |
| DETAIL-02   | Implementing      | ✅ Verified  |
| DETAIL-03   | Implementing      | ✅ Verified  |
| DETAIL-04   | Implementing      | ✅ Verified  |
| DETAIL-05   | Implementing      | ✅ Verified  |
| DETAIL-06   | Implementing      | ✅ Verified  |

---

## Summary

**Overall**: ✅ Ready

**Spec-anchored check**: 6/6 ACs matched spec outcome
**Sensor**: 3/3 mutations killed
**Gate**: 461 passed, 0 failed, 1 pre-existing-flake deselected

**What works**: Both routes share one payload contract via the single `get_reimbursement` use case, making the "PUT response byte-identical to a follow-up GET" guarantee structural (directly proven by a dedicated test, not just asserted by convention). All four GET status paths (200/404/400/500) and both PUT decision outcomes are covered with precise, spec-anchored assertions. The `ReimbursementListItem` → `ReimbursementItem` relocation is a clean mechanical move verified by the pre-existing `list/test_route.py` still passing unmodified.

**Issues found**: None. One minor spec-precision note (not a gap): the "two back-to-back GETs return identical data" edge case has no test that literally issues two GETs in sequence — it's covered transitively by the stronger PUT/GET byte-identical test and by GET's own determinism (no mutation in its path), so no fix task is warranted.

**Next steps**: None — feature ready to ship as-is.
