# Fix Service Module-Name Collision via Real Package Namespacing — Specification

## Problem Statement

`src/reimbursement` was flattened (matching `api`/`publisher`'s `package = false` pattern) as part of the `src/agent` → `src/reimbursement` rename, which reintroduced a bare-module-name collision (`config.py`, `consumer.py`) against `src/publisher` on the single shared pytest `sys.path` — the exact collision `agent-consume-reimbursement/design.md` had previously avoided by deliberately keeping `agent` an installable, namespaced package. The interim fix (a standalone nested pytest root for `reimbursement`, reaching into `api/tests/helpers.py` via `--confcutdir`) traded the collision for a worse problem: `reimbursement`'s tests now depend on `api`'s private test tree, which is not acceptable. This feature replaces both the collision and the interim fix with real Python package namespacing for `api`, `publisher`, and `reimbursement`, and consolidates the cross-package test helpers that motivated the interim fix into the shared kernel.

## Goals

- [ ] `api`, `publisher`, and `reimbursement` become real, dotted-import-namespaced packages, so no two services can ever collide on a bare module name again — without reintroducing `uv_build`'s doubled `src/<pkg>/src/<pkg>/*.py` layout.
- [ ] The workspace returns to a single, unified `uv run pytest` invocation at the root, sharing one session-scoped Postgres testcontainer across all four packages.
- [ ] `reimbursement` and `publisher` stop depending on `api`'s private test tree for shared test fixtures — the shared payload/seed helpers move into the shared kernel (`shared.testing`).
- [ ] The decision is recorded in `.specs/STATE.md`, and the existing miscitation of "AD-030" in the root `pyproject.toml` comment is corrected.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature                                                                             | Reason                                                                                                                                            |
| ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `shared`'s own build backend                                                         | Stays on `uv_build` with its existing doubled `src/shared/src/shared/*.py` layout — not part of the collision, no reason to touch it                |
| `docs/codebase/CONVENTIONS.md` / `docs/codebase/TESTING.md` wording sync             | Explicitly deferred by the user to a later `architecture-evaluate` pass, matching the precedent already set for `TESTING.md`'s other known staleness |
| The `shared`-kernel single-consumer tension (AD-025 / R-010)                         | Already explicitly declined by the user in `refactoring-layering/spec.md` — out of scope there and here                                             |
| `agent-decide-reimbursement`'s decision-policy design (AD-030's actual content)      | Unrelated; this feature only fixes a stale citation of the AD-030 *number*, never touches AD-030's content                                          |
| CI/CD pipeline setup                                                                 | No CI exists in this repo today; not being introduced as part of this fix                                                                            |
| Reverting the `src/agent` → `src/reimbursement` directory rename                     | Already done and explicitly kept — only the internal module *structure* changes, not the directory name                                             |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here — nothing is left silently unclear. All forks below were surfaced and resolved in a prior `/grill-me` session; this table records the outcomes for traceability.

| Assumption / decision                                                                                              | Chosen default                                                                                                     | Rationale                                                                                                                                                                                             | Confirmed? |
| --------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- |
| Collision fix mechanism: build-backend switch vs. per-package pytest isolation + container-sharing wrapper script     | Switch `api`/`publisher`/`reimbursement` to `setuptools` with `package-dir` mapping ("Option B")                       | Preserves the single-shared-Postgres-container architecture with zero new test-orchestration tooling; `uv_build` cannot decouple an import name from a same-named directory even with `namespace = true` (verified against its Rust source via Context7) | y          |
| Scope of the namespacing fix: `reimbursement` only vs. project-wide (`api`/`publisher` too)                            | Project-wide — `api` and `publisher` also switch to `setuptools`                                                       | User's stated principle ("each package isolated with the possibility of having collision filenames") is general, not reimbursement-specific                                                          | y          |
| `shared`'s build backend                                                                                              | Left untouched on `uv_build`                                                                                            | Not part of the collision; touching it is unrelated churn                                                                                                                                              | y          |
| Destination module for the relocated DB-provisioning utilities (`POSTGRES_IMAGE`, `disposable_database_name`, etc.)   | `shared/testing.py` (same file as the relocated seed helpers), broadening its documented scope beyond "test doubles for a shared contract" | Avoids introducing a second test-support module inside `shared` where one sanctioned location (`shared/testing.py`) already exists; the scope-broadening is exactly the `CONVENTIONS.md` change the user pre-authorized | n — delegated to agent per user ("we can change the conventions if needed") |
| Documentation sync timing (`CONVENTIONS.md`, `TESTING.md`)                                                            | Deferred to a later `architecture-evaluate` pass                                                                        | Explicit user instruction: "update the documentation when reaches the moment"                                                                                                                        | y          |

**Open questions:** none — all resolved or logged above.

---

## User Stories

### P1: Real package namespacing eliminates the module collision ⭐ MVP

**User Story**: As a developer running the test suite, I want `api`, `publisher`, and `reimbursement` to be real, dotted-import-namespaced packages, so that `uv run pytest` at the workspace root runs every package's tests in one session without `ImportError`s from colliding bare module names — today or for any future service.

**Why P1**: This is the actual bug. Nothing else in this feature matters if the suite still can't run unified.

**Acceptance Criteria**:

1. WHEN `src/api/pyproject.toml`, `src/publisher/pyproject.toml`, and `src/reimbursement/pyproject.toml` are inspected THEN each SHALL declare `setuptools` as its build backend with a `[tool.setuptools.package-dir]` entry mapping the package name to its existing `src` directory, with no new subdirectory created.
2. WHEN any module inside `api`, `publisher`, or `reimbursement` is imported (production or test code) THEN it SHALL be imported via its package-qualified dotted name (e.g. `from api.config import ...`), never a bare top-level name.
3. WHEN `src/api`, `src/publisher`, `src/reimbursement` are inspected on disk THEN their module files SHALL remain at `src/<pkg>/src/*.py` — flat, single `src` level, no wrapping `src/<pkg>/src/<pkg>/` directory.
4. WHEN `uv run pytest` is invoked once at the workspace root with no path arguments THEN it SHALL collect and execute tests from `src/api`, `src/publisher`, `src/reimbursement`, and `src/shared` in one pytest session, sharing one session-scoped Postgres testcontainer from the root `conftest.py`.
5. WHEN a package's own `tests/` directory contains a bare-name-imported test-support module (e.g. `fakes.py`, `agent_fakes.py`) THEN the root `pyproject.toml`'s `pythonpath` config SHALL continue to include that package's own `tests/` directory so those imports keep resolving.
6. WHEN each service's Docker image is built and started THEN its entrypoint SHALL use the dotted module path (`api.main:app` for the uvicorn ASGI target; `python -m publisher.consumer`; `python -m reimbursement.consumer`), and the container SHALL start and serve/consume successfully.
7. WHEN `api`'s migration runner resolves its migrations directory THEN it SHALL use `importlib.resources.files("api")`, not a `Path(__file__).parent`-based lookup.
8. WHEN `src/reimbursement/pyproject.toml` is inspected THEN it SHALL NOT contain a standalone `[tool.pytest.ini_options]` block, `pythonpath` override, or `--confcutdir` addopt — `reimbursement` participates in the single root pytest config only.
9. WHEN `src/reimbursement`'s directory name and `shared`'s `pyproject.toml` are inspected THEN the directory SHALL remain named `reimbursement` (not reverted to `agent`), and `shared`'s `pyproject.toml` SHALL be unchanged (`uv_build` still declared).

**Independent Test**: From a clean checkout, run `uv sync && uv run pytest` at the workspace root and confirm all four packages' tests collect and pass in one session with no `ImportError`. Then `docker compose up` and confirm every service starts and serves/consumes.

---

### P2: Shared-kernel test-helper consolidation

**User Story**: As a developer, I want `publisher` and `reimbursement`'s test suites to depend only on the shared kernel for cross-service test fixtures, so that no service's tests reach into another service's private test tree.

**Why P2**: This was the concrete complaint that started the fix ("reimbursement will depend on the tests from api, and that's not happen") — P1 makes the suite *runnable*, P2 makes the dependency direction correct.

**Acceptance Criteria**:

1. WHEN `publisher`'s or `reimbursement`'s `test_integration.py` needs the canonical reimbursement-creation payload builder THEN it SHALL import `valid_reimbursement_item` from `shared.testing`, not from `api`'s test tree.
2. WHEN `api`'s own tests need `valid_reimbursement_item` or the seed helpers (`seed_reimbursement`, `seed_reimbursement_with_receipts`, `seed_human_review`) THEN they SHALL also import them from `shared.testing` — no duplicate definitions SHALL remain in `src/api/tests/helpers.py`.
3. WHEN `shared.testing`'s seed helpers are called with an `original_payload` override THEN the reconciled signature SHALL preserve that parameter (no loss of `api/tests/helpers.py`'s current capability during consolidation).
4. WHEN the workspace-root `conftest.py` resolves its Postgres-provisioning utilities (`POSTGRES_IMAGE`, `MAINTENANCE_DATABASE`, `disposable_database_name`, `guard_is_test_database`, `maintenance_url`, `with_database`, `database_name`) THEN it SHALL import them from `shared.testing` via a normal package import, not `pythonpath` bare-name resolution into `api`'s test tree.
5. WHEN the root `conftest.py`'s migration-runner import (`migrate.py` / `apply_migrations`) is inspected THEN it SHALL remain resolved from `api`'s own tree, unchanged — migrations stay `api`-owned, not moved to `shared`.
6. WHEN `api`'s service-specific test fakes (`FakePool`, `_build_client`, `valid_approve_payload`, `valid_reject_payload`) are inspected THEN they SHALL remain local to `src/api/tests/helpers.py`, unmoved.

**Independent Test**: `grep` for `from helpers import` across `src/publisher/tests` and `src/reimbursement/tests` returns nothing; `uv run pytest src/api src/publisher src/reimbursement src/shared` passes.

---

### P3: Decision log correction

**User Story**: As a maintainer reading `.specs/STATE.md`, I want this fix recorded as its own decision, and the existing stale "AD-030" citation fixed, so the decision log stays trustworthy.

**Why P3**: Housekeeping — doesn't block the fix working, but leaving the log wrong actively misleads future readers.

**Acceptance Criteria**:

1. WHEN `.specs/STATE.md` is inspected THEN it SHALL contain a new AD (next sequential number) documenting the `uv_build` → `setuptools` switch for `api`/`publisher`/`reimbursement` and the shared-kernel test-helper consolidation, including rationale and the rejected alternatives (doubled-path revert, pytest-only isolation).
2. WHEN the root `pyproject.toml`'s `[tool.pytest.ini_options]` comment is inspected THEN it SHALL NOT cite "AD-030" for the pytest-isolation resolution — it SHALL cite the correct new AD number.

**Independent Test**: `grep -n "AD-030" pyproject.toml` returns nothing; the new AD number appears in both `STATE.md` and the `pyproject.toml` comment.

---

## Edge Cases

- WHEN `uv sync` is run after the build-backend switch THEN `uv.lock` SHALL regenerate cleanly with `setuptools` resolved for the three affected members, with no dependency-resolution errors.
- WHEN `docker-compose.yml` and each service's Dockerfile are inspected THEN every entrypoint/CMD SHALL use the dotted-module form — none SHALL reference the old bare entrypoints (`python -m consumer`, bare `main:app`).
- WHEN the test suite is run against a freshly cloned checkout (`uv sync` then `uv run pytest`, Docker running) THEN it SHALL pass with no manual environment setup beyond Docker being available for the Postgres testcontainer.
- WHEN `reimbursement`'s own `agent.py` submodule (the LangGraph agent definition) is imported THEN it SHALL resolve as `reimbursement.agent`, distinct from the top-level `reimbursement` package itself, with no import ambiguity.

---

## Requirement Traceability

| Requirement ID | Story                                    | Phase  | Status  |
| --------------- | ----------------------------------------- | ------ | ------- |
| PKG-01          | P1: Real package namespacing              | Design | Pending |
| PKG-02          | P1: Real package namespacing              | Design | Pending |
| PKG-03          | P1: Real package namespacing              | Design | Pending |
| PKG-04          | P1: Real package namespacing              | Design | Pending |
| PKG-05          | P1: Real package namespacing              | Design | Pending |
| PKG-06          | P1: Real package namespacing              | Design | Pending |
| PKG-07          | P1: Real package namespacing              | Design | Pending |
| PKG-08          | P1: Real package namespacing              | Design | Pending |
| PKG-09          | P1: Real package namespacing              | Design | Pending |
| PKG-10          | P2: Shared-kernel helper consolidation    | Design | Pending |
| PKG-11          | P2: Shared-kernel helper consolidation    | Design | Pending |
| PKG-12          | P2: Shared-kernel helper consolidation    | Design | Pending |
| PKG-13          | P2: Shared-kernel helper consolidation    | Design | Pending |
| PKG-14          | P2: Shared-kernel helper consolidation    | Design | Pending |
| PKG-15          | P2: Shared-kernel helper consolidation    | Design | Pending |
| PKG-16          | P3: Decision log correction               | Design | Pending |
| PKG-17          | P3: Decision log correction               | Design | Pending |

**ID format:** `PKG-[NUMBER]`

**Status values:** Pending → In Design → In Tasks → Implementing → Verified

**Coverage:** 17 total, 0 mapped to tasks, 17 unmapped ⚠️ (Tasks phase maps these next)

---

## Success Criteria

- [ ] `uv run pytest` at the workspace root (no path arguments) passes for all four packages in one session, using one Postgres testcontainer.
- [ ] `grep -rn "package = false"` across `src/api`, `src/publisher`, `src/reimbursement`'s `pyproject.toml` files returns nothing.
- [ ] `grep -rn "from helpers import"` across `src/publisher/tests` and `src/reimbursement/tests` returns nothing.
- [ ] `docker compose up` starts `api`, `publisher`, and `reimbursement` successfully with their new dotted entrypoints.
- [ ] `.specs/STATE.md` contains the new AD; `pyproject.toml`'s comment no longer cites "AD-030".
