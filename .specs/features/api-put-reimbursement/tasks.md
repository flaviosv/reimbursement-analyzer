# PUT /api/v1/reimbursement/:uuid Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user — do not proceed without it.**

---

**Design**: `.specs/features/api-put-reimbursement/design.md`
**Status**: Draft

**Precondition (not a task here):** `api-get-reimbursement/tasks.md`'s Phase 1
must be complete before this feature's Phase 2 starts — `shared/db.py`
(`AD-029`) and `api`'s `get_pool` dependency/lifespan wiring are built
there, not repeated here. If this feature is somehow executed first instead,
stop and run that Phase 1 first rather than duplicating it.

---

## Test Coverage Matrix

> Generated from `docs/codebase/TESTING.md` (existing, project-wide
> guidelines) plus `api-get-reimbursement/tasks.md`'s matrix, which already
> established this feature's two new layer *types* (`use_cases` module,
> DB-backed route) — reused directly rather than re-derived. Confirm before
> Execute.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------- | --------------------- | ----------------- | ----------- |
| `shared.errors.{ReviewInvalid, ReimbursementNotFound, ReimbursementNotEligible}` | none | Plain exception classes — exercised indirectly via use-case and route tests | — | build gate only |
| `shared.reimbursement.repository.{approve, reject, find_reimbursement_state, record_human_review_decision}` | Integration (real Postgres) | Every SQL behavior: atomic update succeeds/no-ops correctly per eligibility, reject's completeness gate enforced at the `WHERE` clause, `human_review` insert, `find_reimbursement_state`'s three outcomes (no row / ineligible / incomplete) | `src/shared/tests/reimbursement/test_repository.py` (extend) | `uv run pytest` |
| `shared.reimbursement.use_cases.review_reimbursement` | Unit + integration (mixed) | 1:1 to spec ACs (`REVIEW-01` through `REVIEW-10`), including the concurrency guarantee (`REVIEW-09`) | `src/shared/tests/reimbursement/use_cases/test_review_reimbursement.py` | mixed |
| `reimbursement/update/validation.py` | Unit | 1:1 to spec ACs for payload shape: missing fields, malformed `receipts_*`, discriminated-union routing on `status` | `src/api/tests/reimbursement/update/test_validation.py` | `uv run pytest -m "not integration"` |
| `reimbursement/update/route.py` + `api/errors.py` (3 new handlers) | Route-level (`TestClient` + `dependency_overrides`, backed by a real Postgres connection — same `FakePool` built in `api-get-reimbursement`'s T9, reused here) | Every AC: approve/reject happy paths, `422` on shape failure, `400` on state/completeness/uuid-mismatch, `404` on unknown uuid, `500` on DB failure, `DescribeTheRealApp` | `src/api/tests/reimbursement/update/test_route.py` | `uv run pytest` |

## Gate Check Commands

> From `docs/codebase/TESTING.md` directly — same commands, no new ones needed.

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | After tasks with unit tests only, no DB dependency | `uv run pytest -m "not integration"` |
| Full | After tasks touching the real Postgres container | `uv run pytest` |
| Build | Config/entity-only tasks | `uv run pytest -m "not integration"` |

---

## Execution Plan

### Phase 1: Domain Layer (errors + repository + use case)

```
T1 → T2 → T3
```

### Phase 2: API Layer (validation, route)

```
T4 → T5
```

---

## Task Breakdown

### T1: Add the three new `shared.errors` exceptions

**What**: `ReviewInvalid`, `ReimbursementNotFound`, `ReimbursementNotEligible` — same shape as the existing three in the module.
**Where**: `src/shared/src/shared/errors.py` (modify)
**Depends on**: None
**Reuses**: `BatchInvalid`'s exact shape.
**Requirement**: REVIEW-02, REVIEW-03 (`ReviewInvalid`); REVIEW-07 (`ReimbursementNotFound`); REVIEW-04, REVIEW-08, REVIEW-10 (`ReimbursementNotEligible`)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] All three classes added with docstrings stating what each signals
- [ ] No dedicated test — exercised indirectly by T3/T5 per the coverage matrix's "none" entry
- [ ] Gate check passes: `uv run pytest -m "not integration"`

**Tests**: none
**Gate**: quick

---

### T2: Add the four repository primitives + a docstring note on the naming trap

**What**: `approve`, `reject`, `find_reimbursement_state`, `record_human_review_decision` — pure SQL, no gating logic. Also add a one-line docstring cross-reference on the existing `insert_human_review` clarifying it targets `reimbursement`, not `human_review` (Risks & Concerns from the design).
**Where**: `src/shared/src/shared/reimbursement/repository.py` (modify), `src/shared/tests/reimbursement/test_repository.py` (extend)
**Depends on**: None
**Reuses**: `insert_pending`/`insert_human_review`'s function-per-statement style in the same file (this codebase's own precedent for adding several related repository functions in one commit — `55cbf0d feat(shared): add reimbursement repository`).
**Requirement**: REVIEW-01, REVIEW-04, REVIEW-05, REVIEW-10 (the SQL-level behavior each of these ACs ultimately rests on)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `approve(conn, uuid, *, eligible_statuses, receipts_value, receipts_date, receipts_currency, reason) -> Record | None` — atomic `UPDATE ... RETURNING`
- [ ] `reject(conn, uuid, *, eligible_statuses, reason) -> Record | None` — atomic `UPDATE ... WHERE ... AND receipts_value/date/currency IS NOT NULL RETURNING`
- [ ] `find_reimbursement_state(conn, uuid) -> Record | None`
- [ ] `record_human_review_decision(conn, reimbursement_uuid, status, reviewed_by, reason) -> UUID`
- [ ] `insert_human_review`'s docstring gains a one-line note distinguishing it from `record_human_review_decision`
- [ ] `DescribeApprove`/`DescribeReject`/`DescribeFindReimbursementState`/`DescribeRecordHumanReviewDecision` in `test_repository.py` cover: `approve` succeeds on an eligible row and returns the updated row (REVIEW-01), `approve` returns `None` on an ineligible status (REVIEW-04), `reject` succeeds when all three receipt fields are already set (REVIEW-05), `reject` returns `None` when any receipt field is `NULL` even on an eligible status (REVIEW-10), `find_reimbursement_state` returns `None` for an unknown uuid / the row for a known one, `record_human_review_decision` inserts exactly one `human_review` row with the given fields
- [ ] Gate check passes: `uv run pytest`
- [ ] Test count: +8

**Tests**: integration
**Gate**: full

---

### T3: Add `use_cases.review_reimbursement` (approve/reject orchestration)

**What**: `approve_reimbursement`/`reject_reimbursement` — atomic transition, conditional `human_review` insert, 404/400/400 disambiguation, all inside one transaction.
**Where**: `src/shared/src/shared/reimbursement/use_cases/review_reimbursement.py` (new), `src/shared/tests/reimbursement/use_cases/test_review_reimbursement.py` (new)
**Depends on**: T1 (exceptions), T2 (repository primitives)
**Reuses**: `send_human_review.py`'s exact module shape; `AD-017`'s `async with conn.transaction():` discipline.
**Requirement**: REVIEW-01 through REVIEW-10 (this is where every one of them is actually orchestrated)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `approve_reimbursement(conn, uuid, *, receipts_value, receipts_date, receipts_currency, reason, approved_by) -> Record` implemented: calls `repository.approve`, on success calls `record_human_review_decision` in the same transaction and returns the row; on `None`, calls `find_reimbursement_state` and raises `ReimbursementNotFound` or `ReimbursementNotEligible`
- [ ] `reject_reimbursement(conn, uuid, *, reason, approved_by) -> Record` — same shape via `repository.reject`
- [ ] Integration tests: approve happy path (REVIEW-01), reject happy path (REVIEW-05), unknown uuid → `ReimbursementNotFound` for both actions (REVIEW-07), ineligible status → `ReimbursementNotEligible` for both actions (REVIEW-04, REVIEW-08), reject with an incomplete entity → `ReimbursementNotEligible` (REVIEW-10), exactly one new `human_review` row per successful call, no `human_review` row and no status change on any raised path
- [ ] Concurrency integration test (REVIEW-09): two concurrent `asyncio.gather`'d calls against the same eligible row — assert exactly one succeeds (returns a row) and the other raises `ReimbursementNotEligible`, and exactly one `human_review` row exists afterward
- [ ] Gate check passes: `uv run pytest`
- [ ] Test count: +8

**Tests**: unit + integration (mixed — this module has no pure-unit-only branch since both gates require a DB round trip to know the row's state, unlike `list_reimbursements`'s pre-DB gates; classified "mixed" per the matrix, all cases here run under the full gate)
**Gate**: full

---

### T4: Add `reimbursement/update/validation.py`

**What**: `ApproveReview`/`RejectReview` discriminated union on `status`, plus `validate_review()` raising `ReviewInvalid` on any shape failure.
**Where**: `src/api/src/reimbursement/update/validation.py` (new), `src/api/tests/reimbursement/update/test_validation.py` (new)
**Depends on**: T1 (`ReviewInvalid` must exist)
**Reuses**: `create/validation.py`'s exact manual-`TypeAdapter`-then-catch-`ValidationError`-then-raise pattern. Discriminated-union syntax already verified against Context7/Pydantic docs during Design.
**Requirement**: REVIEW-02, REVIEW-03

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `ApproveReview`/`RejectReview` models per the design's field list, `ReviewRequest` discriminated union, `REVIEW_ADAPTER`
- [ ] `validate_review(raw: bytes) -> ApproveReview | RejectReview` catches `ValidationError`, raises `ReviewInvalid` naming the offending field
- [ ] Unit tests: valid approve payload parses to `ApproveReview`, valid reject payload parses to `RejectReview`, missing required approve field raises `ReviewInvalid` (REVIEW-02), malformed `receipts_date`/`receipts_currency`/`receipts_value` each raise `ReviewInvalid` (REVIEW-03), missing `reason`/`approved_by` on reject raises `ReviewInvalid` (REVIEW-06), unknown `status` value raises `ReviewInvalid`
- [ ] Gate check passes: `uv run pytest -m "not integration"`
- [ ] Test count: +7

**Tests**: unit
**Gate**: quick

---

### T5: Add `reimbursement/update/route.py` + register the three new error handlers

**What**: Parse → check body/path `uuid` consistency → delegate to the use case → respond. Register `_review_invalid_handler` (→ `422`), `_reimbursement_not_found_handler` (→ `404`), `_reimbursement_not_eligible_handler` (→ `400`) in `api/errors.py`.
**Where**: `src/api/src/reimbursement/update/route.py` (new), `src/api/src/errors.py` (modify — three new handlers), `src/api/tests/reimbursement/update/test_route.py` (new), `src/api/tests/reimbursement/update/conftest.py` (new — reuses the `FakePool` built in `api-get-reimbursement`'s T9)
**Depends on**: T3 (`review_reimbursement`), T4 (`validation.py`); external precondition: `api-get-reimbursement`'s Phase 1 (`get_pool`) and T9 (the `FakePool` test fixture)
**Reuses**: `create/route.py`'s thin-orchestration shape; `create/test_route.py`'s `_build_client()`/`dependency_overrides`/`DescribeTheRealApp` pattern; the `FakePool` fixture from `api-get-reimbursement/T9` (not rebuilt here).

**Requirement**: REVIEW-01 through REVIEW-10 (route-level, end-to-end)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `PUT /api/v1/reimbursement/{uuid}` registered, `Depends(get_pool)`
- [ ] Body/path `uuid` mismatch → `400`, before the use case is ever called
- [ ] Three handlers registered in `register_handlers()`
- [ ] `DescribePutReimbursement` in `test_route.py` covers: approve happy path → `200` (REVIEW-01), missing approve field → `422` (REVIEW-02), malformed `receipts_*` → `422` (REVIEW-03), approve on ineligible status → `400` (REVIEW-04), reject happy path → `200` (REVIEW-05), missing `reason`/`approved_by` on reject → `422` (REVIEW-06), unknown uuid → `404` (REVIEW-07), reject on ineligible status → `400` (REVIEW-08), concurrent PUTs — one `200`, one `400` (REVIEW-09), reject blocked on incomplete entity → `400` (REVIEW-10), body/path uuid mismatch → `400`, `500` on a simulated pool failure
- [ ] `DescribeTheRealApp` proves `main.app`'s real wiring serves this route (second proof point for the pool wiring, alongside GET's)
- [ ] Gate check passes: `uv run pytest`
- [ ] Test count: +13

**Tests**: route-level
**Gate**: full

---

## Phase Execution Map

```
Phase 1 → Phase 2

Phase 1:  T1 ──→ T2 ──→ T3
Phase 2:  T4 ──→ T5
```

Execution is strictly sequential — there is no intra-phase parallelism.

---

## Task Granularity Check

| Task | Scope | Status |
| ---- | ----- | ------ |
| T1: Three new exceptions | 1 file, 3 tightly-related classes | ✅ Granular — "2-3 related things in same file, cohesive" |
| T2: Four repository primitives + 1 docstring note | 1 file, 4 tightly-cohesive functions (one use case's SQL needs) | ✅ Granular — matches this codebase's own precedent for repository.py |
| T3: `review_reimbursement` use case | 1 module, 1 concept (orchestrate + disambiguate) | ✅ Granular |
| T4: `validation.py` | 1 file, 1 concept (parse + validate) | ✅ Granular |
| T5: `route.py` + 3 handler registrations | 1 endpoint + the 3 one-line registrations it directly needs | ✅ Granular — same precedent as GET's T9 |

---

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| ---- | ----------------------- | -------------- | ------ |
| T1 | None | (Phase 1 start) | ✅ Match |
| T2 | None | T1 → T2 (ordering, not a real dependency) | ⚠️ Ordering-only arrow, consistent with intra-phase sequential execution |
| T3 | T1, T2 | T2 → T3 (ordering) + body names both real dependencies | ✅ Match |
| T4 | T1 | Phase 2 starts after Phase 1; body names the real dependency | ✅ Match |
| T5 | T3, T4 (+ external: GET's Phase 1/T9) | T4 → T5 (ordering); body names all dependencies incl. the cross-feature one | ✅ Match |

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| ---- | ----------------------------- | ------------------ | ----------- | ------ |
| T1: Three exceptions | Exception classes | none | none | ✅ OK |
| T2: Repository primitives | Repository | Integration | integration | ✅ OK |
| T3: `review_reimbursement` | Use case | Unit + integration | unit + integration | ✅ OK |
| T4: `validation.py` | Unit (payload shape) | Unit | unit | ✅ OK |
| T5: `route.py` + handlers | Route-level | Route-level | route-level | ✅ OK |

No violations.

---

## Tools for Execution

Proposed default — confirm before Execute:

- **MCP**: NONE needed for any task.
- **Skill**: NONE needed mid-task. `tlc-spec-driven` itself drives Execute per the Execution Protocol above.
