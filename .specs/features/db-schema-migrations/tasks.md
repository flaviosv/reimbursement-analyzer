# Database Schema & Migrations Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name
and follow its Execute flow and Critical Rules.** Do not search for skill files
by filesystem path. The skill is the source of truth for the full flow
(per-task cycle, sub-agent delegation, adequacy review, Verifier,
discrimination sensor).

**If the skill cannot be activated, STOP and tell the user — do not proceed
without it.**

---

**Design**: `.specs/features/db-schema-migrations/design.md`
**Status**: Draft

---

## Test Coverage Matrix

> Generated from codebase, project guidelines, and spec — confirm before
> Execute. Guidelines found: **none** — no `AGENTS.md`, no `CONTRIBUTING.md`,
> no `.github/workflows`, no pytest config in any manifest, and `TESTING.md:3`
> confirms zero test infrastructure. Strong defaults applied.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------ | -------------------- | ---------------- | ----------- |
| Docs / context files | none | — (review only) | `docs/codebase/*.md` | — |
| Infra config (compose) | none | — (build gate only: config parse + stack boot) | `docker-compose.yml` | `docker compose config -q` |
| Migration runner (`migrate.py`) | unit | All branches of `backend_url()` and `migrations_path()`; 1:1 to spec ACs DBM-01…04 | `src/api/tests/test_migrate.py` | `uv run pytest src/api/tests/test_migrate.py -q` |
| Migration SQL / schema | integration | Every `CHECK`, `UNIQUE`, `FK`, `DEFAULT` and trigger in spec ACs DBM-05…09 and DBM-13…15 has a test asserting the **specific** PostgreSQL error class — not a bare exception. Every listed edge case covered | `src/api/tests/test_migrations.py` | `uv run pytest src/api/tests -q` |
| Test fixtures (`conftest.py`, `helpers.py`) | unit + integration | DSN helpers covered by branch; provisioning yields a migrated DB on the expected PostgreSQL major; the `_test` guard raises on any other database name | `src/api/tests/test_fixtures.py` | `uv run pytest -q` |
| Package manifests / lockfile | none | — (build gate only) | `pyproject.toml`, `uv.lock` | `uv sync --all-packages` |

## Gate Check Commands

> Generated from codebase — confirm before Execute. There were no pre-existing
> commands to discover; these are established by T1 and T3 of this feature.

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | After tasks with unit tests only | `uv run pytest src/api/tests/test_migrate.py -q` |
| Full | After tasks with integration tests | `uv run pytest -q` |
| Build | After config/manifest/docs-only tasks | `uv sync --all-packages && docker compose config -q` |

**Note:** as of T9 the Full gate needs no running stack — testcontainers
provisions its own PostgreSQL. A Docker daemon is required.

---

## Execution Plan

Phases are ordered and run sequentially — each phase completes before the next
begins, and tasks within a phase execute in order.

### Phase 1: Harness

Dependencies, the runner, and the test fixtures — everything needed before any
SQL can be written *and verified*.

```
T1 → T2 → T3
```

### Phase 2: Schema

One migration per table, each with its constraint tests, then the
cross-migration rollback/idempotency behaviour.

```
T4 → T5 → T6
```

### Phase 3: Bootstrap

Wire migrations into stack startup, record the new test command, and make the
suite hermetic.

```
T7 → T8 → T9
```

---

## Task Breakdown

### T1: Add migration and test dependencies

**What**: Declare `yoyo-migrations` + `psycopg[binary]` on the `api` package
and `pytest` in a root dev dependency group, then refresh the lockfile.
**Where**: `src/api/pyproject.toml`, `pyproject.toml`, `uv.lock`
**Depends on**: None
**Reuses**: existing uv workspace layout; `[tool.uv.sources] shared = { workspace = true }` pattern
**Requirement**: DBM-01 (enabler)

**Tools**:

- MCP: `context7` (confirm current versions before pinning)
- Skill: NONE

**Done when**:

- [ ] `src/api/pyproject.toml` declares `yoyo-migrations>=9.0.0` and `psycopg[binary]>=3.3.4`
- [ ] Root `pyproject.toml` declares `[dependency-groups] dev = ["pytest>=9.1.1"]`
- [ ] `uv sync --all-packages` succeeds and `uv.lock` is updated
- [ ] `uv run python -c "import yoyo, psycopg"` exits 0
- [ ] Gate check passes: `uv sync --all-packages && docker compose config -q`

**Tests**: none
**Gate**: build

**Commit**: `chore(api): add yoyo-migrations, psycopg and pytest dependencies`

---

### T2: Create the migration runner

**What**: Add `migrate.py` exposing `migrations_path()`, `backend_url()` and
`main()`, plus unit tests for the two pure functions.
**Where**: `src/api/src/api/migrate.py`, `src/api/tests/test_migrate.py`
**Depends on**: T1
**Reuses**: `CONVENTIONS.md:28-31` entry-point and `os.environ` config patterns
**Requirement**: DBM-01, DBM-04

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `backend_url()` rewrites `postgresql://` → `postgresql+psycopg://` and leaves an already-adapted DSN unchanged
- [ ] `migrations_path()` resolves via `importlib.resources.files("api")` and points inside the installed package
- [ ] `main()` acquires `backend.lock()` before applying `backend.to_apply(...)`
- [ ] Missing `DATABASE_URL` raises immediately rather than defaulting
- [ ] Gate check passes: `uv run pytest src/api/tests/test_migrate.py -q`
- [ ] Test count: 5 tests pass (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(api): add yoyo migration runner`

---

### T3: Create the test database fixtures

**What**: Add `conftest.py` provisioning a disposable `reimbursementanalyzer_test`
database with the `_test` safety guard, plus tests proving both behaviours.
**Where**: `src/api/tests/conftest.py`, `src/api/tests/test_fixtures.py`
**Depends on**: T2
**Reuses**: `migrate.main()` from T2 — the fixture applies migrations through the same code path AC DBM-01 describes
**Requirement**: DBM-02

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] Session fixture drops and recreates `reimbursementanalyzer_test`, then applies migrations from zero
- [ ] Fixture raises before any DDL when the resolved database name does not end in `_test`
- [ ] Applying migrations against an empty migrations set exits cleanly (DBM-02 baseline)
- [ ] A function-scoped connection fixture is available to later tests
- [ ] Gate check passes: `docker compose up -d postgres && uv run pytest src/api/tests -q`
- [ ] Test count: 8 tests pass (no silent deletions)

**Tests**: integration
**Gate**: full

**Commit**: `test(api): add disposable test database fixtures`

---

### T4: Create the reimbursement migration

**What**: Add `0001.create-reimbursement.sql` and its rollback, plus
integration tests for every constraint, default and trigger on the table.
**Where**: `src/api/src/api/migrations/0001.create-reimbursement.sql`,
`…rollback.sql`, `src/api/tests/test_migrations.py`
**Depends on**: T3
**Reuses**: DDL verified against PG 18.4 in `design.md` § Version Verification
**Requirement**: DBM-05, DBM-06, DBM-07, DBM-09, DBM-13, DBM-14, DBM-15

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] Table created with all 13 columns per the spec's Schema Definition
- [ ] `uuid` defaults to `uuidv7()`; two sequential inserts yield ascending UUIDs
- [ ] `status` defaults to `pending`; unknown values rejected by `reimbursement_status_check`
- [ ] Duplicate `(request_id, NULL)` rejected — `NULLS NOT DISTINCT` proven, not assumed
- [ ] Malformed / over-254-char `submitted_by` rejected; `NULL` accepted
- [ ] `currency` outside `^[A-Z]{3}$` rejected; 65-char `request_id` rejected
- [ ] `decision_reason` and `human_review_notes` accept large values (deliberately unbounded)
- [ ] Insert with only `request_id` + `original_payload` succeeds
- [ ] `set_updated_at()` trigger overwrites a caller-supplied `updated_at`
- [ ] Index on `status` exists
- [ ] Rollback file drops the table, its trigger and the trigger function
- [ ] Gate check passes: `docker compose up -d postgres && uv run pytest src/api/tests -q`
- [ ] Test count: 22 tests pass (no silent deletions)

**Tests**: integration
**Gate**: full

**Commit**: `feat(api): add reimbursement table migration`

---

### T5: Create the human_review migration

**What**: Add `0002.create-human-review.sql` and its rollback, plus
integration tests for its constraints and referential behaviour.
**Where**: `src/api/src/api/migrations/0002.create-human-review.sql`,
`…rollback.sql`, `src/api/tests/test_migrations.py` (extend)
**Depends on**: T4
**Reuses**: the `-- depends: 0001.create-reimbursement` ordering header; T4's fixtures and assertions style
**Requirement**: DBM-05, DBM-07, DBM-08, DBM-14

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] Table created with all 6 columns per the spec's Schema Definition
- [ ] `uuid` defaults to `uuidv7()`
- [ ] `status` outside `('approved','rejected')` rejected
- [ ] Malformed / over-254-char `reviewed_by` rejected
- [ ] `reason` accepts large values and rejects `NULL`
- [ ] Orphan `reimbursement_uuid` rejected by the foreign key
- [ ] Deleting a referenced `reimbursement` row rejected by `ON DELETE RESTRICT`
- [ ] Multiple reviews for one reimbursement coexist (append-only)
- [ ] Index on `(reimbursement_uuid, created_at DESC)` exists
- [ ] `-- depends:` header enforces ordering after `0001`
- [ ] Rollback file drops the table
- [ ] Gate check passes: `docker compose up -d postgres && uv run pytest src/api/tests -q`
- [ ] Test count: 33 tests pass (no silent deletions)

**Tests**: integration
**Gate**: full

**Commit**: `feat(api): add human_review table migration`

---

### T6: Prove idempotency, rollback and locking

**What**: Add integration tests covering the cross-migration behaviours that
no single migration file owns.
**Where**: `src/api/tests/test_migration_lifecycle.py`
**Depends on**: T5
**Reuses**: fixtures from T3; both migrations from T4/T5
**Requirement**: DBM-02, DBM-03, DBM-04

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] Re-applying migrations against a migrated database applies zero and exits 0
- [ ] Each applied version is recorded in yoyo's ledger table
- [ ] Full rollback drops both tables, leaving no leftover objects
- [ ] Rollback order is the reverse of apply order (`0002` before `0001`)
- [ ] Two concurrent runners serialize via `backend.lock()`, each migration applied exactly once
- [ ] Gate check passes: `docker compose up -d postgres && uv run pytest src/api/tests -q`
- [ ] Test count: 38 tests pass (no silent deletions)

**Tests**: integration
**Gate**: full

**Commit**: `test(api): cover migration idempotency, rollback and locking`

---

### T7: Wire the migrate service into compose

**What**: Add the one-shot `migrate` service and gate the three application
services on its successful completion.
**Where**: `docker-compose.yml`
**Depends on**: T6
**Reuses**: `src/api/Dockerfile` `dev` target; the `DATABASE_URL` string at `docker-compose.yml:286`; the `postgres` healthcheck at `:37-41`
**Requirement**: DBM-10, DBM-11, DBM-12

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `migrate` service builds `src/api/Dockerfile` target `dev`, command `python -m api.migrate`
- [ ] `restart: "no"` set explicitly — it must not loop
- [ ] `depends_on: postgres: {condition: service_healthy}`
- [ ] `api`, `agent` and `publisher` each gain `migrate: {condition: service_completed_successfully}`
- [ ] `api` gains `DATABASE_URL`, matching the agent/publisher value exactly
- [ ] `docker compose down -v && docker compose up -d` → `migrate` exits 0 and both tables exist
- [ ] Restarting against existing volumes applies zero migrations and still exits 0
- [ ] Gate check passes: `docker compose config -q` and the boot check above
- [ ] Test count: 38 tests pass, unchanged (no silent deletions)

**Tests**: none
**Gate**: build

**Commit**: `feat(compose): apply migrations at bootstrap via one-shot service`

---

### T8: Record the new test and migration commands

**What**: Update the context files so the repo's first test command and the
migration workflow are discoverable.
**Where**: `docs/codebase/TESTING.md`, `docs/codebase/STACK.md`
**Depends on**: T7
**Reuses**: existing table formats in both files
**Requirement**: Success Criteria (spec)

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `TESTING.md` replaces "There is no test infrastructure yet" with the real matrix and gate commands
- [ ] `STACK.md` Commands table gains the pytest and migration commands
- [ ] `STACK.md` Key libraries table gains yoyo-migrations, psycopg and pytest
- [ ] Both files stay alphabetically sorted per the project's markdown rule
- [ ] Gate check passes: `uv sync --all-packages && docker compose config -q`

**Tests**: none
**Gate**: build

**Commit**: `docs(codebase): record migration and test commands`

---

### T9: Switch the test strategy to testcontainers

**What**: Replace the compose-backed test database with a throwaway
`postgres:18` container provisioned by testcontainers, keeping
`TEST_DATABASE_URL` as a guarded escape hatch.
**Where**: `pyproject.toml`, `src/api/tests/conftest.py`,
`src/api/tests/helpers.py`, `src/api/tests/test_fixtures.py`,
`src/api/tests/test_migration_lifecycle.py`, `README.md`
**Depends on**: T8
**Reuses**: the existing fixture shape and `_test` guard; only the source of the server changes
**Requirement**: Success Criteria (spec); supersedes AD-006 with AD-008

**Tools**:

- MCP: `context7` (confirm the current testcontainers API)
- Skill: NONE

**Done when**:

- [ ] `testcontainers[postgres]>=4.15.0` declared in the root dev group
- [ ] `server_url` starts a `postgres:18` container; `TEST_DATABASE_URL` overrides it and stays guarded
- [ ] Lifecycle tests derive their throwaway databases from the container, not a hardcoded DSN
- [ ] The hardcoded dev DSN is gone from the test helpers
- [ ] A test asserts the server major is >= 18, so the image cannot silently drift from compose
- [ ] Imports use `testcontainers.community.postgres` (4.15.0 deprecates `testcontainers.postgres`)
- [ ] Full suite passes **with the compose `postgres` service stopped**
- [ ] `README.md`, `spec.md`, `design.md` and `STATE.md` reflect the new strategy
- [ ] Gate check passes: `uv run pytest -q`
- [ ] Test count: 86 tests pass (no silent deletions)

**Tests**: integration
**Gate**: full

**Commit**: `test(api): provision postgres via testcontainers`

---

## Phase Execution Map

```
Phase 1 → Phase 2 → Phase 3

Phase 1:  T1 ──→ T2 ──→ T3
Phase 2:  T4 ──→ T5 ──→ T6
Phase 3:  T7 ──→ T8 ──→ T9
```

Execution is strictly sequential — there is no intra-phase parallelism.

**Batch packing:** 9 tasks total. At ~7 tasks per batch this still packs into a
single batch, so execution happened **inline in the main window with no
sub-agents spawned**. The Verifier still runs automatically after T9.

---

## Task Granularity Check

| Task | Scope | Status |
| ---- | ----- | ------ |
| T1: Add dependencies | 3 manifest files, one concern | ✅ Granular |
| T2: Create the migration runner | 1 module + its unit tests | ✅ Granular |
| T3: Create the test database fixtures | 1 conftest + its tests | ✅ Granular |
| T4: Create the reimbursement migration | 1 migration (+ rollback) + its tests | ✅ Granular |
| T5: Create the human_review migration | 1 migration (+ rollback) + its tests | ✅ Granular |
| T6: Prove idempotency, rollback and locking | 1 test module | ✅ Granular |
| T7: Wire the migrate service into compose | 1 config file | ✅ Granular |
| T8: Record the new commands | 1 docs file, one concern | ✅ Granular |
| T9: Switch to testcontainers | 1 fixture layer, one concern | ✅ Granular |

---

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| ---- | ---------------------- | ------------- | ------ |
| T1 | None | — (chain head) | ✅ Match |
| T2 | T1 | T1 → T2 | ✅ Match |
| T3 | T2 | T2 → T3 | ✅ Match |
| T4 | T3 | T3 → T4 (Phase 1 → Phase 2) | ✅ Match |
| T5 | T4 | T4 → T5 | ✅ Match |
| T6 | T5 | T5 → T6 | ✅ Match |
| T7 | T6 | T6 → T7 (Phase 2 → Phase 3) | ✅ Match |
| T8 | T7 | T7 → T8 | ✅ Match |
| T9 | T8 | T8 → T9 | ✅ Match |

No task depends on a later-phase task; the graph is a single linear chain.

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| ---- | --------------------------- | --------------- | --------- | ------ |
| T1: Add dependencies | Package manifests / lockfile | none | none | ✅ OK |
| T2: Migration runner | Migration runner (`migrate.py`) | unit | unit | ✅ OK |
| T3: Test fixtures | Test fixtures (`conftest.py`) | integration | integration | ✅ OK |
| T4: Reimbursement migration | Migration SQL / schema | integration | integration | ✅ OK |
| T5: human_review migration | Migration SQL / schema | integration | integration | ✅ OK |
| T6: Lifecycle tests | Migration SQL / schema | integration | integration | ✅ OK |
| T7: Compose wiring | Infra config (compose) | none | none | ✅ OK |
| T8: Context files | Docs / context files | none | none | ✅ OK |
| T9: Test fixtures | Test fixtures (`conftest.py`, `helpers.py`) | unit + integration | integration | ✅ OK |

No violations. Every task that creates a tested layer carries its own tests —
none are deferred.

---

## Cumulative Test Counts

Counts are cumulative, so a drop between tasks signals a silent deletion.
Planned figures were estimates made before the tests were written; actuals are
what the runner reported at each commit.

| After | Planned | Actual | Note |
| ----- | ------- | ------ | ---- |
| T2 | 5 | 7 | Extra branch coverage on `backend_url()` |
| T3 | 8 | 14 | Guard parametrised across four database names |
| T4 | 22 | 52 | Status, email and currency checks parametrised |
| T5 | 33 | 71 | Review status and email checks parametrised |
| T6 | 38 | 79 | One planned "test" was a miscollected helper, since renamed |
| T7 | 38 | 79 | Infra only |
| T8 | 38 | 79 | Docs only |
| T9 | 86 | 86 | Adds DSN-helper and server-version coverage |

Every step is monotonic — no silent deletions.
