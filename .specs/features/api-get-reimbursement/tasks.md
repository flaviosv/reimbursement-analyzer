# GET /api/v1/reimbursement Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user — do not proceed without it.**

---

**Design**: `.specs/features/api-get-reimbursement/design.md`
**Status**: Draft

**Build-order note:** this feature is sequenced first between the two sibling
specs (`api-get-reimbursement`/`api-put-reimbursement`) — Phase 1 below owns
the shared prerequisite work (`AD-029` relocation, DB pool wiring into
`api`). `api-put-reimbursement/tasks.md` declares Phase 1's output as a
precondition rather than repeating it.

---

## Test Coverage Matrix

> Generated from `docs/codebase/TESTING.md` (existing, project-wide
> guidelines — cited directly, not inferred) plus sampling of
> `src/shared/tests/reimbursement/use_cases/test_send_human_review.py` and
> `src/api/tests/reimbursement/create/test_route.py` for the exact shape of
> the two new layer types this feature introduces (a `use_cases` module, a
> DB-backed route). Confirm before Execute.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------- | --------------------- | ----------------- | ----------- |
| `shared.db.managed_pool` (relocated) | Integration (real Postgres) | Same two behaviors the existing `DescribeManagedPool` class already covers (sizes pool from config, closes on exit) — relocated, not rewritten | `src/shared/tests/test_db.py` | `uv run pytest` |
| `shared.config.MAX_LIST_LIMIT` | Unit | Loads with the expected value | `src/shared/tests/test_config.py` (extend) | `uv run pytest -m "not integration"` |
| `shared.errors.ReimbursementFilterInvalid` | none | Plain exception class — exercised indirectly via the use-case and route tests below | — | build gate only |
| `shared.reimbursement.repository.fetch_reimbursement_page` | Integration (real Postgres) | Every query behavior: pagination slice, single-status filter, multi-status filter, no-filter (incl. `pending`), `created_at DESC` ordering, last-human-review present/absent | `src/shared/tests/reimbursement/test_repository.py` (extend) | `uv run pytest` |
| `shared.reimbursement.use_cases.list_reimbursements` | Unit + integration (mixed) | 1:1 to spec ACs: both gates (status whitelist, bounds) unit-tested in isolation (no DB touched before a gate raises); happy-path delegation integration-tested against real Postgres | `src/shared/tests/reimbursement/use_cases/test_list_reimbursements.py` | mixed — gate tests via `-m "not integration"`, delegation test via full `uv run pytest` |
| `reimbursement/list/params.py` | Unit | Comma-split behavior, defaults, no semantic validation (that's the use case's job) | `src/api/tests/reimbursement/list/test_params.py` | `uv run pytest -m "not integration"` |
| `reimbursement/list/response.py` | Unit | Record → model mapping, `last_human_review` present/null | `src/api/tests/reimbursement/list/test_response.py` | `uv run pytest -m "not integration"` |
| `reimbursement/list/route.py` + `api/errors.py` (new handler) + `api/dependencies.py`/`main.py` (pool wiring) | Route-level (`TestClient` + `dependency_overrides`, backed by a real Postgres connection via a `FakePool` wrapper — unlike `create`'s route tests, this layer genuinely touches the DB) | Every AC: happy path, pagination bounds, single/multi status filter, invalid status, empty result, last-human-review enrichment, 500 on DB failure, plus a `DescribeTheRealApp` class proving `main.app`'s actual lifespan/wiring (mirrors `create/test_route.py`'s own convention) — this is also where T4's pool-wiring gets its only meaningful test (merge-forward, see T9) | `src/api/tests/reimbursement/list/test_route.py` | `uv run pytest` |

**Coverage Expectation values** — set from `docs/codebase/TESTING.md` directly; no strong defaults needed, guidelines fully cover this feature's layer types.

## Gate Check Commands

> From `docs/codebase/TESTING.md` directly — same commands, no new ones needed.

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | After tasks with unit tests only, no DB dependency | `uv run pytest -m "not integration"` |
| Full | After tasks touching the real Postgres container (repository, use case delegation, routes, app wiring) | `uv run pytest` |
| Build | Config/entity-only tasks (e.g. adding a constant or exception class) | `uv run pytest -m "not integration"` (fast enough to double as build gate here — no separate lint step is enforced project-wide per `CONVENTIONS.md`) |

---

## Execution Plan

Phases are ordered and run sequentially — each phase completes before the next begins, and tasks within a phase execute in order.

### Phase 1: Shared Prerequisite (AD-029 relocation + pool wiring)

```
T1 → T2 → T3 → T4
```

### Phase 2: Domain Layer (repository + use case)

```
T5 → T6
```

### Phase 3: API Layer (params, response, route)

```
T7 → T8 → T9
```

---

## Task Breakdown

### T1: Relocate `managed_pool` to `shared/db.py` (AD-029)

**What**: Move `managed_pool()` out of `shared.reimbursement.repository` into a new `shared/db.py`; update the repository module's docstring; update the two existing import sites; relocate the existing `DescribeManagedPool` test class.
**Where**: `src/shared/src/shared/db.py` (new), `src/shared/src/shared/reimbursement/repository.py` (modify), `src/publisher/src/consumer.py` (modify import), `src/publisher/tests/test_integration.py` (modify import), `src/shared/tests/test_db.py` (new, relocated test class), `src/shared/tests/reimbursement/test_repository.py` (modify — remove `DescribeManagedPool`)
**Depends on**: None
**Reuses**: `managed_pool`'s existing implementation verbatim (pure relocation, no logic change) — `shared.producer.managed_producer` as the structural precedent this now matches.
**Requirement**: AD-029 (infrastructure prerequisite, not a spec AC)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `shared/db.py` exports `managed_pool(config: DatabaseConfig) -> AsyncIterator[asyncpg.Pool]`, identical behavior to today's
- [ ] `shared.reimbursement.repository` no longer defines or imports `managed_pool`; its module docstring reads "every SQL statement against the reimbursement table" (drops "pool lifecycle")
- [ ] `src/publisher/src/consumer.py` imports `managed_pool` from `shared.db`
- [ ] `src/publisher/tests/test_integration.py` imports `managed_pool` from `shared.db`
- [ ] `src/shared/tests/test_db.py::DescribeManagedPool` exists with both original test methods, unmodified in content
- [ ] `src/shared/tests/reimbursement/test_repository.py` no longer contains `DescribeManagedPool`
- [ ] Full existing `publisher` + `shared` suites still pass — not just this task's new/moved tests (this touches already-shipped, independently-verified code)
- [ ] Gate check passes: `uv run pytest`
- [ ] Test count: same total as before the move (2 tests relocated, 0 added, 0 removed)

**Tests**: integration
**Gate**: full

---

### T2: Add `shared.config.MAX_LIST_LIMIT`

**What**: Add the pagination ceiling as a named constant, matching `MAX_BATCH_ITEMS`'s existing pattern.
**Where**: `src/shared/src/shared/config.py` (modify), `src/shared/tests/test_config.py` (extend)
**Depends on**: None
**Reuses**: `MAX_BATCH_ITEMS`'s placement/style (module-level constant near the top of the file, alongside the other wire-protocol constants).
**Requirement**: LIST-02 (limit bound enforcement — this constant is what the use case's gate checks against)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `MAX_LIST_LIMIT = 500` defined alongside `MAX_BATCH_ITEMS` in `shared/config.py`
- [ ] `test_config.py` asserts the value loads as `500`
- [ ] Gate check passes: `uv run pytest -m "not integration"`
- [ ] Test count: +1

**Tests**: unit
**Gate**: quick

---

### T3: Add `shared.errors.ReimbursementFilterInvalid`

**What**: New exception class, same shape as the existing three in the module.
**Where**: `src/shared/src/shared/errors.py` (modify)
**Depends on**: None
**Reuses**: `BatchInvalid`'s exact shape (bare `Exception` subclass with a one-line docstring).
**Requirement**: LIST-06 (invalid status filter → 400), LIST-02 (bounds → 400)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `class ReimbursementFilterInvalid(Exception)` added with a docstring stating what it signals (bad status value or out-of-bounds limit/offset)
- [ ] No dedicated test — exercised indirectly by T6/T9 per the coverage matrix's "none" entry
- [ ] Gate check passes: `uv run pytest -m "not integration"`

**Tests**: none
**Gate**: quick

---

### T4: Wire `get_pool` into `api/dependencies.py` + `main.py` lifespan

**What**: Add `get_pool(request: Request) -> asyncpg.Pool`, mirroring `get_producer`; extend `main.lifespan` to open/close the DB pool alongside the existing Kafka producer.
**Where**: `src/api/src/dependencies.py` (modify), `src/api/src/main.py` (modify)
**Depends on**: T1 (`shared.db.managed_pool` must exist)
**Reuses**: `get_producer`'s exact shape; `managed_producer`'s construct/yield/close pattern already present in `main.lifespan`.
**Requirement**: none directly (infrastructure) — enables every LIST-* requirement

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `dependencies.get_pool` added, identical shape to `get_producer`
- [ ] `main.lifespan` nests `async with managed_pool(load_config().database) as pool:` inside the existing producer block, sets `app.state.pool`
- [ ] Code compiles and imports cleanly (no route yet exercises it — real end-to-end proof is merge-forwarded into T9's `DescribeTheRealApp`, per the Test Co-location "resolving compilation dependencies" rule: nothing observable exists to test here in isolation until a route uses the pool)
- [ ] Gate check passes: `uv run pytest -m "not integration"` (no new DB-touching test at this task; the real wiring proof lands in T9)

**Tests**: none (merge-forward to T9 — see Done when)
**Gate**: quick

---

### T5: Add `repository.fetch_reimbursement_page`

**What**: The one SQL statement this feature needs — paginated, optionally status-filtered, each row paired with its most recent `human_review` via `LEFT JOIN LATERAL`.
**Where**: `src/shared/src/shared/reimbursement/repository.py` (modify), `src/shared/tests/reimbursement/test_repository.py` (extend)
**Depends on**: T1 (repository.py's post-relocation state)
**Reuses**: `reimbursement_status_created_idx`, `human_review_reimbursement_created_idx` (both pre-existing, built for exactly this query per `ARCHITECTURE.md`); `insert_pending`'s function-per-statement style in the same file.
**Requirement**: LIST-01, LIST-03, LIST-04, LIST-05, LIST-09, LIST-10

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `fetch_reimbursement_page(conn, *, statuses: list[str] | None, limit: int, offset: int) -> list[asyncpg.Record]` implemented per the design's SQL
- [ ] `DescribeFetchReimbursementPage` in `test_repository.py` covers: returns the correct page slice (LIST-01), a single status filter (LIST-04), a multi-status filter via `ANY(...)` (from `AD-028`'s comma-separated contract, exercised here at the SQL layer), no filter returns every status including `pending` (LIST-05), zero-match returns `[]` (LIST-03), `last_human_review` populated when a `human_review` row exists and `None` when it doesn't (LIST-09, LIST-10), ordering is `created_at DESC`
- [ ] Gate check passes: `uv run pytest`
- [ ] Test count: +7 (one per bullet above)

**Tests**: integration
**Gate**: full

---

### T6: Add `use_cases.list_reimbursements` (the two gates)

**What**: Single point of entry — validates the status whitelist and pagination bounds before delegating to `fetch_reimbursement_page`.
**Where**: `src/shared/src/shared/reimbursement/use_cases/list_reimbursements.py` (new), `src/shared/tests/reimbursement/use_cases/test_list_reimbursements.py` (new)
**Depends on**: T2 (`MAX_LIST_LIMIT`), T3 (`ReimbursementFilterInvalid`), T5 (`fetch_reimbursement_page`)
**Reuses**: `send_human_review.py`'s exact module shape (`conn`-taking, framework-agnostic, sibling file in the same `use_cases/` package).
**Requirement**: LIST-02, LIST-06, LIST-08 (repeated-param case is a `params.py`/route concern, not this gate's — see T7/T9)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `list_reimbursements(conn, *, statuses, limit, offset) -> list[asyncpg.Record]` implemented with both gates evaluated before any DB call
- [ ] Unit tests (no DB touched — pass `conn=None` or equivalent, since a gate violation must raise before `conn` is ever used): invalid status value raises `ReimbursementFilterInvalid` (LIST-06), `limit > MAX_LIST_LIMIT` raises (LIST-02), `limit < 0` raises, `offset < 0` raises
- [ ] Integration test: valid inputs delegate to `fetch_reimbursement_page` and return real rows unchanged
- [ ] Gate check passes: `uv run pytest` (mixed — unit portion also independently passes `-m "not integration"`)
- [ ] Test count: +5 (4 unit + 1 integration)

**Tests**: unit + integration (mixed)
**Gate**: full

---

### T7: Add `reimbursement/list/params.py`

**What**: Pure syntactic query-param extraction — `limit`/`offset` as plain `Query()` ints with defaults, `status` comma-split with no semantic validation.
**Where**: `src/api/src/reimbursement/list/params.py` (new), `src/api/tests/reimbursement/list/test_params.py` (new)
**Depends on**: None
**Reuses**: FastAPI's native `Query()` for type coercion, same as every other route in the project.
**Requirement**: LIST-01 (defaults), LIST-08 (repeated-param stays invalid — asserted here since it's the extraction layer's job to not silently collapse it, matching FastAPI's own default single-value binding)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `limit: int = 100`, `offset: int = 0` as FastAPI `Query()` params, no `ge`/`le` bounds (that's T6's job)
- [ ] `parse_status_filter(raw: str | None) -> list[str] | None` splits on `,`, returns segments unchecked, `None` for an absent/empty param
- [ ] Unit tests: defaults apply when omitted, comma-split produces the right list, empty string behaves as omitted
- [ ] Gate check passes: `uv run pytest -m "not integration"`
- [ ] Test count: +4

**Tests**: unit
**Gate**: quick

---

### T8: Add `reimbursement/list/response.py`

**What**: `HumanReviewSummary`, `ReimbursementListItem`, `ReimbursementListResponse`, and `ReimbursementListItem.from_record()`.
**Where**: `src/api/src/reimbursement/list/response.py` (new), `src/api/tests/reimbursement/list/test_response.py` (new)
**Depends on**: None
**Reuses**: `errors.py`'s `MessageResponse` as the precedent for an api-local Pydantic response shape; the `hr_*`-prefixed column convention from T5's SQL.
**Requirement**: LIST-09, LIST-10 (response shape for the enrichment)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] All three models defined per the design's interfaces
- [ ] `from_record()` correctly builds `last_human_review` when the record's `hr_*` columns are populated, and `None` when they're `NULL`
- [ ] Unit tests: both branches of `from_record()`, full round-trip of every `reimbursement` column
- [ ] Gate check passes: `uv run pytest -m "not integration"`
- [ ] Test count: +2

**Tests**: unit
**Gate**: quick

---

### T9: Add `reimbursement/list/route.py` + register the new error handler

**What**: Orchestrate `params.py` → `list_reimbursements` use case → `response.py`; register `_reimbursement_filter_invalid_handler` (→ `400`) in `api/errors.py`. Includes the merge-forwarded real-app-wiring proof for T4.
**Where**: `src/api/src/reimbursement/list/route.py` (new), `src/api/src/errors.py` (modify — one new handler), `src/api/tests/reimbursement/list/test_route.py` (new), `src/api/tests/reimbursement/list/conftest.py` (new — a `FakePool` wrapping the real `db` fixture, adapted from `publisher/tests/fakes.py::RealPool`'s pattern)
**Depends on**: T4 (`get_pool`), T6 (`list_reimbursements`), T7 (`params.py`), T8 (`response.py`)
**Reuses**: `create/route.py`'s thin-orchestration shape; `create/test_route.py`'s `_build_client()` + `dependency_overrides` + `DescribeTheRealApp` pattern; `publisher/tests/fakes.py::RealPool`'s "wrap the real connection behind a lock" technique, adapted for a pool-shaped fake.

**Requirement**: LIST-01 through LIST-10 (this task's route-level tests are where every AC gets its final, end-to-end assertion)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `GET /api/v1/reimbursement` registered, `Depends(get_pool)`, calls the use case and shapes the response
- [ ] `_reimbursement_filter_invalid_handler` registered in `register_handlers()` → `400`
- [ ] `DescribeGetReimbursement` in `test_route.py` covers: happy path with defaults (LIST-01), custom `limit`/`offset` (LIST-01), out-of-bounds `limit`/`offset` → `400` (LIST-02), empty result → `200 []` (LIST-03), single-status filter (LIST-04), multi-status comma filter (LIST-04), status omitted includes `pending` (LIST-05), invalid status → `400` (LIST-06), repeated `status` param → `400` (LIST-08), last-human-review present/absent in the response (LIST-09, LIST-10), `500` on a simulated pool failure (LIST-11)
- [ ] `DescribeTheRealApp` proves `main.app`'s actual lifespan/wiring serves this route correctly (this is T4's only real test, merge-forwarded here)
- [ ] Gate check passes: `uv run pytest`
- [ ] Test count: +12 (11 route cases + 1 real-app case)

**Tests**: route-level
**Gate**: full

---

## Phase Execution Map

```
Phase 1 → Phase 2 → Phase 3

Phase 1:  T1 ──→ T2 ──→ T3 ──→ T4
Phase 2:  T5 ──→ T6
Phase 3:  T7 ──→ T8 ──→ T9
```

Execution is strictly sequential — there is no intra-phase parallelism.

---

## Task Granularity Check

| Task | Scope | Status |
| ---- | ----- | ------ |
| T1: Relocate `managed_pool` | 1 cohesive refactor (function + its 2 call sites + 1 test class) | ✅ Granular — splitting further leaves intermediate broken states |
| T2: Add `MAX_LIST_LIMIT` | 1 constant | ✅ Granular |
| T3: Add `ReimbursementFilterInvalid` | 1 exception class | ✅ Granular |
| T4: Wire `get_pool` + lifespan | 2 tightly-coupled edits, 1 concept (pool wiring) | ✅ Granular — matches `AD-024`'s own precedent (`get_producer` + lifespan edit shipped together) |
| T5: `fetch_reimbursement_page` | 1 function | ✅ Granular |
| T6: `list_reimbursements` use case | 1 module, 1 concept (gate + delegate) | ✅ Granular |
| T7: `params.py` | 1 file, 1 concept (syntactic extraction) | ✅ Granular |
| T8: `response.py` | 1 file, 3 tightly-coupled models | ✅ Granular — "2-3 related things in same file, cohesive" |
| T9: `route.py` + handler registration | 1 endpoint + 1 one-line handler registration it directly needs | ✅ Granular — matches `create/route.py`'s own precedent of shipping the route and any handler it introduces together |

---

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| ---- | ----------------------- | -------------- | ------ |
| T1 | None | (Phase 1 start, no arrow in) | ✅ Match |
| T2 | None | T1 → T2 | ⚠️ Diagram shows a sequencing arrow for execution order within the phase; T2 has no real data dependency on T1 — arrow reflects *ordering*, not *blocking*, consistent with "tasks within a phase execute in order" |
| T3 | None | T2 → T3 | ⚠️ Same as T2 — ordering arrow, not a data dependency |
| T4 | T1 | T3 → T4 (ordering) + T1 (real dependency, stated in body) | ✅ Match — T4's body correctly names T1 as the real blocking dependency |
| T5 | T1 | Phase 2 starts after Phase 1 | ✅ Match |
| T6 | T2, T3, T5 | T5 → T6 (ordering); body names all three real dependencies | ✅ Match |
| T7 | None | Phase 3 starts after Phase 2 | ✅ Match |
| T8 | None | T7 → T8 (ordering) | ⚠️ Ordering arrow, not a data dependency |
| T9 | T4, T6, T7, T8 | T8 → T9 (ordering); body names all four real dependencies | ✅ Match |

**Note on ⚠️ rows**: within-phase arrows in the Execution Plan diagram represent sequential execution order (per the skill's own "tasks within a phase execute in order" rule), not necessarily a hard data dependency — every row's *body* `Depends on` field is the authoritative dependency list, and every real dependency is correctly named there. No mismatch requiring restructuring.

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| ---- | ----------------------------- | ------------------ | ----------- | ------ |
| T1: Relocate `managed_pool` | `shared.db` | Integration | integration | ✅ OK |
| T2: `MAX_LIST_LIMIT` | Config | Unit | unit | ✅ OK |
| T3: `ReimbursementFilterInvalid` | Exception class | none | none | ✅ OK |
| T4: `get_pool` + lifespan | App lifespan/DI | Route-level (merge-forwarded to T9 per matrix note) | none (merge-forward stated explicitly in body) | ✅ OK — matches the matrix's own stated merge-forward plan for this layer |
| T5: `fetch_reimbursement_page` | Repository | Integration | integration | ✅ OK |
| T6: `list_reimbursements` | Use case | Unit + integration | unit + integration | ✅ OK |
| T7: `params.py` | Params (unit layer) | Unit | unit | ✅ OK |
| T8: `response.py` | Response models (unit layer) | Unit | unit | ✅ OK |
| T9: `route.py` + handler | Route-level | Route-level | route-level | ✅ OK |

No violations — every task's `Tests` field matches the matrix, including T4's explicit, matrix-sanctioned merge-forward.

---

## Tools for Execution

Proposed default — confirm before Execute:

- **MCP**: NONE needed for any task (no new library/API questions remain; Pydantic's discriminated-union usage was already verified against Context7 during Design and isn't touched by this feature).
- **Skill**: NONE needed mid-task. `tlc-spec-driven` itself drives Execute per the Execution Protocol above.
