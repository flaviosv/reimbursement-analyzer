# Reimbursement Detail (GET by uuid & PUT payload) Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user — do not proceed without it.**

---

**Design**: `.specs/features/api-reimbursement-detail/design.md`
**Status**: Draft

---

## Test Coverage Matrix

> Generated from `docs/codebase/TESTING.md` (existing, project-wide
> guidelines — cited directly, not inferred) plus sampling of
> `packages/shared/tests/reimbursement/test_repository.py`,
> `packages/api/tests/reimbursement/list/test_response.py`, and
> `packages/api/tests/reimbursement/update/test_route.py` for the exact
> shape of this feature's layer types. Confirm before Execute.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------- | --------------------- | ----------------- | ----------- |
| `shared.reimbursement.repository.fetch_reimbursement_by_uuid` | Integration (real Postgres) | Row found with a `human_review` entry, row found with none, no matching uuid → `None` | `packages/shared/tests/reimbursement/test_repository.py` (extend) | `uv run pytest` |
| `shared.reimbursement.use_cases.get_reimbursement` | Unit + integration (mixed) | 1:1 to DETAIL-01/02: raises `ReimbursementNotFound` on no match (isolable with a stub repository call), delegates to `fetch_reimbursement_by_uuid` against real Postgres for the found path | `packages/shared/tests/reimbursement/use_cases/test_get_reimbursement.py` (new) | mixed — gate test via `-m "not integration"`, delegation test via full `uv run pytest` |
| `api.reimbursement.response` (`HumanReviewSummary`, `ReimbursementItem`, `ReimbursementDetailResponse`) | Unit | Record → model mapping (relocated from `list/response.py`, renamed): `last_human_review` present, `null`, and the all-nullable-`pending`-row case | `packages/api/tests/reimbursement/test_response.py` (new, relocated) | `uv run pytest -m "not integration"` |
| `api.reimbursement.list.response.ReimbursementListResponse` (post-relocation) | none | Plain 2-field composition, no logic left after `ReimbursementItem` moves out — exercised indirectly via `list/test_route.py`; matches project convention (don't test bootstrap/no-logic code) | — | build gate only |
| `api.reimbursement.get.route` + `api/main.py` (router wiring) | Route-level (`TestClient` + `dependency_overrides`, real-Postgres-backed via `FakePool`) | Every AC: DETAIL-01 (200 + shape), DETAIL-02 (404), DETAIL-03 (400, malformed uuid), DETAIL-04 (500, DB failure), plus a `DescribeTheRealApp` class proving `main.app`'s actual wiring (mirrors `list`/`update`'s own convention) | `packages/api/tests/reimbursement/get/test_route.py` (new) | `uv run pytest` |
| `api.reimbursement.update.route` (modified) | Route-level (`TestClient` + `dependency_overrides`) | DETAIL-05 (200 + `data` shape, both approve and reject), DETAIL-06 (400/404/413/422/500 bodies unchanged — regression check) | `packages/api/tests/reimbursement/update/test_route.py` (extend) | `uv run pytest` |

**Coverage Expectation values** — set from `docs/codebase/TESTING.md` directly; no strong defaults needed, guidelines fully cover this feature's layer types.

## Gate Check Commands

> From `docs/codebase/TESTING.md` directly — same commands, no new ones needed.

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | After tasks with no Kafka dependency (still Docker/Postgres-backed — `integration` means "needs a container *beyond* the suite's own Postgres default") | `uv run pytest -m "not integration"` |
| Full | Before considering a task done — the conservative default for anything touching Postgres-backed code (repository, use case, routes) | `uv run pytest` |
| Lint (ad hoc) | Optional sanity check, not gated | `uv run ruff check <path>` |

---

## Execution Plan

Phases are ordered and run sequentially — each phase completes before the next begins, and tasks within a phase execute in order.

### Phase 1: Domain Layer (repository + use case)

```
T1 → T2
```

### Phase 2: API Response Model Relocation

```
T3 → T4
```

### Phase 3: API Routes

```
T5 → T6
```

---

## Task Breakdown

### T1: Add `fetch_reimbursement_by_uuid` repository query

**What**: Add a new `_FETCH_REIMBURSEMENT_BY_UUID` SQL constant (the `_FETCH_REIMBURSEMENT_PAGE` `LEFT JOIN LATERAL` enrichment shape, `WHERE r.uuid = $1` instead of the status/limit/offset clause) and an `async def fetch_reimbursement_by_uuid(conn, uuid) -> asyncpg.Record | None`.
**Where**: `packages/shared/src/shared/reimbursement/repository.py` (modify), `packages/shared/tests/reimbursement/test_repository.py` (extend)
**Depends on**: None
**Reuses**: `_FETCH_REIMBURSEMENT_PAGE`'s `LEFT JOIN LATERAL` shape (design.md's Code Reuse Analysis)
**Requirement**: DETAIL-01, DETAIL-02 (data layer)

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `fetch_reimbursement_by_uuid` returns a row with `hr_*`-prefixed columns populated when a `human_review` exists
- [ ] Returns a row with `hr_*` columns `NULL` when none exists
- [ ] Returns `None` for a non-existent uuid
- [ ] Gate check passes: `uv run pytest`
- [ ] Test count: 3 new tests pass (no silent deletions)

**Tests**: integration
**Gate**: full

**Commit**: `feat(shared): add fetch_reimbursement_by_uuid repository query`

---

### T2: Add `get_reimbursement` use case

**What**: `async def get_reimbursement(conn, uuid) -> asyncpg.Record`, calling `fetch_reimbursement_by_uuid` and raising `ReimbursementNotFound(f"no reimbursement with uuid {uuid}")` on `None` — same message convention as `review_reimbursement.py`'s `_disambiguate`.
**Where**: `packages/shared/src/shared/reimbursement/use_cases/get_reimbursement.py` (new), `packages/shared/tests/reimbursement/use_cases/test_get_reimbursement.py` (new)
**Depends on**: T1
**Reuses**: `shared.errors.ReimbursementNotFound`; `review_reimbursement.py`'s raise-on-`None` shape
**Requirement**: DETAIL-01, DETAIL-02

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] Returns the row when `fetch_reimbursement_by_uuid` finds one
- [ ] Raises `ReimbursementNotFound` when it returns `None`
- [ ] Gate check passes: `uv run pytest`
- [ ] Test count: 2 new tests pass (no silent deletions)

**Tests**: integration (mixed — the not-found branch is exercised against real Postgres too, since there's no DB-touching gate worth isolating a fake for here)
**Gate**: full

**Commit**: `feat(shared): add get_reimbursement use case`

---

### T3: Relocate the reimbursement item/response models to a shared module

**What**: Create `api/reimbursement/response.py` holding `HumanReviewSummary`, `ReimbursementItem` (renamed from `ReimbursementListItem`, `from_record` unchanged), and the new `ReimbursementDetailResponse(msg: str, data: ReimbursementItem)`.
**Where**: `packages/api/src/api/reimbursement/response.py` (new), `packages/api/tests/reimbursement/test_response.py` (new — relocated from `list/test_response.py`, class renamed `DescribeReimbursementItemFromRecord`, same 3 assertions)
**Depends on**: None
**Reuses**: `ReimbursementListItem`/`HumanReviewSummary`'s existing field mapping, moved verbatim (design.md Tech Decisions)
**Requirement**: DETAIL-01, DETAIL-05 (shared payload shape)

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `ReimbursementItem.from_record` behaves identically to the old `ReimbursementListItem.from_record` (same 3 cases: `last_human_review` present/null/all-nullable-pending-row)
- [ ] `ReimbursementDetailResponse` composes `msg`/`data` correctly (`model_dump()` round-trips both fields)
- [ ] Gate check passes: `uv run pytest -m "not integration"`
- [ ] Test count: 3 relocated + 1 new test pass (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `refactor(api): relocate reimbursement item/detail models to a shared response module`

---

### T4: Point the list slice at the relocated `ReimbursementItem`

**What**: Update `list/response.py` to import `ReimbursementItem` from `api.reimbursement.response` and keep only `ReimbursementListResponse(msg: str, data: list[ReimbursementItem])`; update `list/route.py`'s `ReimbursementListItem.from_record(row)` call to `ReimbursementItem.from_record(row)`; delete `packages/api/tests/reimbursement/list/test_response.py` (its coverage moved to T3's new file; the remaining `ReimbursementListResponse` has no logic of its own — matches the project's "don't test bootstrap code" convention).
**Where**: `packages/api/src/api/reimbursement/list/response.py` (modify), `packages/api/src/api/reimbursement/list/route.py` (modify), `packages/api/tests/reimbursement/list/test_response.py` (delete)
**Depends on**: T3
**Reuses**: N/A — pure rewiring
**Requirement**: N/A (refactor supporting DETAIL-01/05, no new behavior)

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `list/response.py` no longer defines `HumanReviewSummary`/`ReimbursementListItem`
- [ ] `list/route.py` imports and calls `ReimbursementItem.from_record`
- [ ] Existing `packages/api/tests/reimbursement/list/test_route.py` still passes unmodified (proves the rename didn't change observable behavior)
- [ ] Gate check passes: `uv run pytest`
- [ ] Test count: 0 new tests (pure refactor); existing list route-test count unchanged, no silent deletions

**Tests**: none (covered by existing `list/test_route.py`, per the layer's own "none" matrix entry)
**Gate**: full

**Commit**: `refactor(api): point list slice at the relocated ReimbursementItem`

---

### T5: Add `GET /api/v1/reimbursement/{uuid}`

**What**: New `get/route.py` slice: `async def get_reimbursement_by_uuid(uuid: UUID, pool = Depends(get_pool)) -> ReimbursementDetailResponse`, calling `get_reimbursement` and returning `ReimbursementDetailResponse(msg="reimbursement found", data=ReimbursementItem.from_record(row))`. Wire its router into `api/main.py` alongside the three existing includes (needed for `DescribeTheRealApp`, so kept in this same task — merge-forward, design.md's own precedent).
**Where**: `packages/api/src/api/reimbursement/get/route.py` (new), `packages/api/src/api/reimbursement/get/__init__.py` (new), `packages/api/src/api/main.py` (modify), `packages/api/tests/reimbursement/get/test_route.py` (new)
**Depends on**: T2, T3
**Reuses**: `list/route.py`'s `pool.acquire(timeout=...)` pattern; `update/route.py`'s `uuid: UUID` path-param + validation-handler pattern (design.md Code Reuse Analysis)
**Requirement**: DETAIL-01, DETAIL-02, DETAIL-03, DETAIL-04

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `200` + `{"msg": "reimbursement found", "data": {...}}` on a match, `data` matching one list-endpoint item's shape
- [ ] `404` on a well-formed, non-existent uuid
- [ ] `400` on a malformed uuid path segment, no query run
- [ ] `500` on a simulated DB/pool failure
- [ ] `DescribeTheRealApp` proves the route through `main.app`'s real wiring
- [ ] Gate check passes: `uv run pytest`
- [ ] Test count: 5 new tests pass (no silent deletions)

**Tests**: route-level
**Gate**: full

**Commit**: `feat(api): add GET /api/v1/reimbursement/{uuid} endpoint`

---

### T6: Return the updated single-item payload from PUT

**What**: After `approve_reimbursement`/`reject_reimbursement` commits, call `get_reimbursement(conn, uuid)` on the same `conn` and return `ReimbursementDetailResponse(msg="reimbursement decision recorded", data=ReimbursementItem.from_record(row))`; update `response_model=` accordingly. Error `responses={}` entries (`400/404/413/422/500`) stay unchanged.
**Where**: `packages/api/src/api/reimbursement/update/route.py` (modify), `packages/api/tests/reimbursement/update/test_route.py` (extend)
**Depends on**: T2, T3
**Reuses**: `get_reimbursement` (same function T5's route calls) — the single source of truth for "current state of a reimbursement" (design.md Tech Decisions)
**Requirement**: DETAIL-05, DETAIL-06

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] Approve success: `200` with `data.status == "human-approved"` and `data.last_human_review` matching the submitted decision
- [ ] Reject success: `200` with `data.status == "human-rejected"` and `data.last_human_review` matching the submitted decision
- [ ] A subsequent `GET /api/v1/reimbursement/{uuid}` returns `data` byte-identical to the `PUT` response's `data`
- [ ] Existing `400`/`404`/`413`/`422`/`500` error-body assertions in `update/test_route.py` still pass unmodified (regression proof for DETAIL-06)
- [ ] Gate check passes: `uv run pytest`
- [ ] Test count: 3 new tests pass, existing error-path tests unchanged (no silent deletions)

**Tests**: route-level
**Gate**: full

**Commit**: `feat(api): return the updated reimbursement payload from PUT`

---

## Phase Execution Map

Visual representation of task ordering. Phases run in sequence, and tasks within a phase run in order:

```
Phase 1 → Phase 2 → Phase 3

Phase 1:  T1 ──→ T2
Phase 2:  T3 ──→ T4
Phase 3:  T5 ──→ T6
```

Execution is strictly sequential — there is no intra-phase parallelism. A single agent (or batch worker) works one task at a time, in order.

Total: 6 tasks — fits a single batch (≤ ~8 tasks); execution happens inline, no sub-agent delegation.

---

## Task Granularity Check

| Task | Scope | Status |
| ---- | ----- | ------ |
| T1: Add `fetch_reimbursement_by_uuid` | 1 repository function + 1 SQL constant | ✅ Granular |
| T2: Add `get_reimbursement` use case | 1 function | ✅ Granular |
| T3: Relocate item/response models | 1 file (3 cohesive, tightly-related classes) | ✅ Granular (cohesive move, matches Task=ONE-file-change rule) |
| T4: Point list slice at relocated model | 2 files, pure import/rename rewiring | ✅ Granular |
| T5: Add GET route | 1 endpoint (route + required wiring) | ✅ Granular |
| T6: PUT returns updated payload | 1 endpoint (modify existing route) | ✅ Granular |

---

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| ---- | ----------------------- | -------------- | ------ |
| T1 | None | No incoming arrow | ✅ Match |
| T2 | T1 | T1 → T2 | ✅ Match |
| T3 | None | No incoming arrow | ✅ Match |
| T4 | T3 | T3 → T4 | ✅ Match |
| T5 | T2, T3 | Phase 1 → Phase 3, Phase 2 → Phase 3 (T2/T3 complete before Phase 3 starts) | ✅ Match |
| T6 | T2, T3 | Same — Phase 3 starts only after Phases 1 and 2 both complete | ✅ Match |

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| ---- | ---------------------------- | ---------------- | ---------- | ------ |
| T1 | `fetch_reimbursement_by_uuid` | Integration | integration | ✅ OK |
| T2 | `get_reimbursement` | Unit + integration (mixed) | integration (mixed) | ✅ OK |
| T3 | `api.reimbursement.response` | Unit | unit | ✅ OK |
| T4 | `api.reimbursement.list.response`/`route` | none (covered by existing `list/test_route.py`) | none | ✅ OK |
| T5 | `api.reimbursement.get.route` + `main.py` wiring | Route-level | route-level | ✅ OK |
| T6 | `api.reimbursement.update.route` | Route-level | route-level | ✅ OK |
