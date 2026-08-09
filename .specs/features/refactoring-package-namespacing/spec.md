# Fix Service Module-Name Collision via Real Package Namespacing — Specification

## Problem Statement

`src/reimbursement` was flattened (matching `api`/`publisher`'s `package = false` pattern) as part of the `src/agent` → `src/reimbursement` rename, which reintroduced a bare-module-name collision (`config.py`, `consumer.py`) against `src/publisher` on the single shared pytest `sys.path` — the exact collision `agent-consume-reimbursement/design.md` had previously avoided by deliberately keeping `agent` an installable, namespaced package. The interim fix (a standalone nested pytest root for `reimbursement`, reaching into `api/tests/helpers.py` via `--confcutdir`) traded the collision for a worse problem: `reimbursement`'s tests now depend on `api`'s private test tree, which is not acceptable. This feature replaces both the collision and the interim fix with real Python package namespacing for `api`, `publisher`, and `reimbursement`, and consolidates the cross-package test helpers that motivated the interim fix into the shared kernel.

### Amendment (2026-08-09) — AD-031's mechanism regressed IDE/static-analysis tooling

AD-031 shipped (PR #8) using `setuptools` + `[tool.setuptools.package-dir]` to map each package's import name onto its existing flat `src/<pkg>/src/*.py` directory without adding a wrapping subdirectory. This fixed the pytest collision (verified, `validation.md`, PKG-01–17 all PASS) but was never checked against IDE tooling. It broke go-to-definition and autocomplete for `api`/`publisher`/`reimbursement` in every editor using Pyright-family static analysis (VS Code/Pylance, Cursor/cursorpyright), confirmed by direct `pyright` CLI runs against the shipped tree:

```
src/reimbursement/src/agent/agent.py:3  error: Import "reimbursement.schema" could not be resolved
src/reimbursement/src/agent/agent.py:4  error: Import "reimbursement.agent.nodes" could not be resolved
src/reimbursement/src/consumer.py:19    error: Import "reimbursement.config" could not be resolved
src/reimbursement/src/consumer.py:20    error: Import "reimbursement.validation" could not be resolved
```

Root cause: `setuptools`' PEP 660 editable install generates a dynamic `MetaPathFinder`-based finder script (`__editable___<pkg>_finder.py`, a `MAPPING`/`NAMESPACES` dict executed at import time) to redirect the import name onto the differently-named `src` directory. Static analyzers don't execute that script — they resolve editable installs by walking directory names, so an import name that doesn't correspond to any physically-matching directory fails to resolve. `shared` (untouched by AD-031, still on `uv_build`, still using a conventional nested `src/shared/src/shared/*.py` layout) resolves cleanly, because `uv_build`'s editable install is a plain static `.pth` path, not a dynamic finder — empirically confirmed via an isolated repro (a throwaway `uv_build` package with `module-root = ""`/default `"src"` produced a plain `.pth`, and `pyright` returned 0 errors against it, versus the dynamic-finder case's `reportMissingImports`).

This amendment corrects the mechanism: `api`, `publisher`, and `reimbursement` move from `setuptools` + `package-dir` remapping to `uv_build` with the same conventional nested src-layout `shared` already uses successfully — `packages/<pkg>/src/<pkg>/*.py`, import name matching a real physical directory. The workspace-member container also renames from `src/` to `packages/`, matching uv's own documented workspace example (`/astral-sh/uv`, `docs/concepts/projects/workspaces.md`, verified via Context7). No dynamic remapping remains anywhere in the workspace.

Alternatives considered and rejected (surfaced and resolved in conversation, not a formal `/grill-me` session — recorded here for traceability since this is a technical/architectural correction, not a UX gray area):

| Alternative | Rejected because |
| --- | --- |
| Keep `src/` as the outer container name, just add the inner `<pkg>/` folder (`src/reimbursement/src/reimbursement/...`) | Works and was seriously considered, but doesn't match uv's own documented workspace convention (which names the container `packages/`, not `src/`) — no reason to deviate from the documented pattern when adopting it costs nothing extra |
| Flat layout (`packages/<pkg>/<pkg>/*.py`, no inner `src/`) via `uv_build`'s `module-root = ""` | Empirically verified to also resolve cleanly in Pyright and is one directory level shallower, but flat-layout trades away src-layout's protection against a package being importable straight out of the project directory without being properly installed — the exact class of import-resolution bug this feature already exists to fix once (PyPA's own packaging guide recommends src-layout for this reason). Not worth the tradeoff for one fewer path segment. |
| Decouple the outer container name from the import name (e.g. `packages/reimbursement-service/src/reimbursement/...`) to avoid the repeated package name reading twice in the path | Real fix for the cosmetic "name repeats" concern, but doesn't fix anything setuptools' mechanism didn't already fix, adds a naming convention decision, and every other package in this repo (including the already-working `shared`) has the exact same repeated-name shape — rejected as unnecessary churn for a purely cosmetic, non-blocking concern |
| Symlink + `pyrightconfig.json` `extraPaths` workaround, keeping the flat `setuptools` layout on disk | More fragile (git symlink support, Docker `COPY -L`, cross-platform), and only patches the symptom for one tool rather than fixing the underlying dynamic-finder mismatch for all static tooling | 

## Goals

- [ ] `api`, `publisher`, and `reimbursement` become real, dotted-import-namespaced packages, so no two services can ever collide on a bare module name again — using the same conventional nested src-layout `shared` already uses (`packages/<pkg>/src/<pkg>/*.py`), not a dynamic import-name remap.
- [ ] Go-to-definition and autocomplete work in Pyright-family IDEs (VS Code/Pylance, Cursor/cursorpyright) for every workspace package, first- and third-party alike — verified with a direct `pyright` run, not just "tests pass."
- [ ] The workspace-member container directory renames from `src/` to `packages/`, matching uv's own documented workspace layout.
- [ ] The workspace returns to a single, unified `uv run pytest` invocation at the root, sharing one session-scoped Postgres testcontainer across all four packages.
- [ ] `reimbursement` and `publisher` continue to depend only on the shared kernel (`shared.testing`) for cross-package test fixtures — already delivered by this feature's P2, unaffected in substance by this amendment, paths updated mechanically by the rename.
- [ ] The correction is recorded in `.specs/STATE.md` as an amendment to AD-031 (not a new AD — same decision, corrected mechanism).

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature                                                                             | Reason                                                                                                                                            |
| ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `shared`'s own internal `src/shared/*.py` layout                                     | Already conventional (nested src-layout) and already resolves cleanly — only its outer container directory moves (`src/shared/` → `packages/shared/`), its internal structure is untouched |
| Flat-layout (`packages/<pkg>/<pkg>/*.py`) or decoupled-name (`packages/<pkg>-service/src/<pkg>/*.py`) alternatives | Both considered and rejected — see Amendment table above |
| `docs/codebase/CONVENTIONS.md` / `docs/codebase/TESTING.md` wording sync             | Explicitly deferred by the user to a later `architecture-evaluate` pass, matching the precedent already set for `TESTING.md`'s other known staleness — this amendment updates `docs/codebase/STRUCTURE.md`'s literal path references only, since those are now factually wrong, not a wording/convention sync |
| The `shared`-kernel single-consumer tension (AD-025 / R-010)                         | Already explicitly declined by the user in `refactoring-layering/spec.md` — out of scope there and here                                             |
| P2 (shared-kernel test-helper consolidation) and P3 (decision-log correction) content | Already implemented and verified (`validation.md`, PKG-10–17 all PASS) — this amendment only touches path references mechanically shifted by the rename, not their substance |
| CI/CD pipeline setup                                                                 | No CI exists in this repo today; not being introduced as part of this fix                                                                            |
| Reverting the `src/agent` → `src/reimbursement` (now `packages/reimbursement`) rename | The package's identity/import name stays `reimbursement` — only the physical container path changes                                                |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here — nothing is left silently unclear. Rows above the divider are carried over from the original spec (already resolved, unaffected by this amendment); rows below are new to this amendment, resolved in conversation with the user before this spec update.

| Assumption / decision                                                                                              | Chosen default                                                                                                     | Rationale                                                                                                                                                                                             | Confirmed? |
| --------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- |
| Scope of the namespacing fix: `reimbursement` only vs. project-wide (`api`/`publisher` too)                            | Project-wide — `api` and `publisher` also switch build backend                                                       | User's stated principle ("each package isolated with the possibility of having collision filenames") is general, not reimbursement-specific                                                          | y          |
| Destination module for the relocated DB-provisioning utilities (`POSTGRES_IMAGE`, `disposable_database_name`, etc.)   | `shared/testing.py`                                                                                                     | Already delivered (P2) — unaffected by this amendment                                                                                                                                                   | y          |
| Documentation sync timing (`CONVENTIONS.md`, `TESTING.md`)                                                            | Deferred to a later `architecture-evaluate` pass                                                                        | Explicit user instruction: "update the documentation when reaches the moment"                                                                                                                        | y          |
| **Collision fix mechanism (superseded)**: build-backend switch to `setuptools` + `package-dir` remap ("Option B")     | **Superseded by this amendment** — switch to `uv_build` with conventional nested src-layout instead                    | `setuptools`' `package-dir` remap produces a dynamic PEP 660 finder that static analyzers can't resolve; `uv_build`'s plain-`.pth` editable install (already proven working via `shared`) has no such problem | y          |
| Outer workspace-member container name: keep `src/` vs. rename to `packages/`                                          | Rename to `packages/`                                                                                                   | Matches uv's own documented workspace example exactly (verified via Context7); removes any ambiguity about whether `src/` refers to the workspace container or a package's own src-layout             | y          |
| Per-package internal layout: flat (`packages/<pkg>/<pkg>/`) vs. nested src-layout (`packages/<pkg>/src/<pkg>/`)        | Nested src-layout, matching `shared`                                                                                    | Flat-layout was empirically verified to also work but trades away src-layout's protection against accidental uninstalled-package imports; not worth it for one fewer path segment, per PyPA's own guidance for "serious projects" | y          |
| AD-031 amendment vs. new AD number                                                                                     | Amend AD-031 in place (add a correction note), not a new AD                                                             | Same underlying decision ("namespace api/publisher/reimbursement"); only the *mechanism* changed, which is what an amendment is for                                                                    | y          |

**Open questions:** none — all resolved or logged above.

---

## User Stories

### P1: Real, statically-resolvable package namespacing eliminates the module collision ⭐ MVP

**User Story**: As a developer running the test suite *and* working in an IDE, I want `api`, `publisher`, and `reimbursement` to be real, dotted-import-namespaced packages using a conventional layout, so that `uv run pytest` at the workspace root runs every package's tests in one session with no `ImportError`s from colliding bare module names, **and** go-to-definition/autocomplete work for every package in Pyright-family editors.

**Why P1**: This is the actual bug, corrected. The original mechanism fixed the test collision but broke IDE tooling — both halves matter; neither is optional.

**Acceptance Criteria**:

1. WHEN `packages/api/pyproject.toml`, `packages/publisher/pyproject.toml`, and `packages/reimbursement/pyproject.toml` are inspected THEN each SHALL declare `uv_build` as its build backend, with no `[tool.setuptools.package-dir]` or any other import-name remap present anywhere in the workspace.
2. WHEN any module inside `api`, `publisher`, or `reimbursement` is imported (production or test code) THEN it SHALL be imported via its package-qualified dotted name (e.g. `from api.config import ...`), never a bare top-level name.
3. WHEN `packages/api`, `packages/publisher`, `packages/reimbursement` are inspected on disk THEN their module files SHALL live at the conventional nested src-layout `packages/<pkg>/src/<pkg>/*.py` — the same shape `packages/shared/src/shared/*.py` already uses.
4. WHEN `uv run pytest` is invoked once at the workspace root with no path arguments THEN it SHALL collect and execute tests from `packages/api`, `packages/publisher`, `packages/reimbursement`, and `packages/shared` in one pytest session, sharing one session-scoped Postgres testcontainer from the root `conftest.py`.
5. WHEN a package's own `tests/` directory contains a bare-name-imported test-support module (e.g. `fakes.py`, `agent_fakes.py`) THEN the root `pyproject.toml`'s `pythonpath` config SHALL continue to include that package's own `tests/` directory so those imports keep resolving.
6. WHEN each service's Docker image is built and started THEN its entrypoint SHALL use the dotted module path (`api.main:app` for the uvicorn ASGI target; `python -m publisher.consumer`; `python -m reimbursement.consumer`), and the container SHALL start and serve/consume successfully, with every `COPY`/bind-mount path in its Dockerfile and `docker-compose.yml` updated to `packages/...`.
7. WHEN `api`'s migration runner resolves its migrations directory THEN it SHALL use `importlib.resources.files("api")`, not a `Path(__file__).parent`-based lookup (unaffected by this amendment — already correct).
8. WHEN `packages/reimbursement/pyproject.toml` is inspected THEN it SHALL NOT contain a standalone `[tool.pytest.ini_options]` block, `pythonpath` override, or `--confcutdir` addopt — `reimbursement` participates in the single root pytest config only.
9. WHEN the workspace root is inspected THEN there SHALL be no `src/` directory remaining — `pyproject.toml`'s `[tool.uv.workspace] members`, `testpaths`, and `pythonpath` SHALL all reference `packages/*`; `.gitignore`'s whitelist SHALL reference `!packages`/`!packages/**` instead of `!src`/`!src/**`.
10. WHEN `packages/reimbursement/pyproject.toml` and `packages/reimbursement`'s directory name are inspected THEN the package SHALL remain named/importable as `reimbursement` (not reverted to `agent`).
11. WHEN `packages/reimbursement/src/agent/agent.py` (or the equivalent file in `api`/`publisher`) is checked with `pyright` (pointed at the workspace `.venv`) THEN it SHALL report zero `reportMissingImports` errors for any first-party (`api`/`publisher`/`reimbursement`/`shared`) or third-party (e.g. `langgraph`) import.

**Independent Test**: From a clean checkout, run `uv sync && uv run pytest` at the workspace root and confirm all four packages' tests collect and pass in one session with no `ImportError`. Then `docker compose up` and confirm every service starts and serves/consumes. Then run `pyright` against each package's entry module and confirm 0 `reportMissingImports` errors.

---

### P2: Shared-kernel test-helper consolidation *(already delivered — unaffected in substance)*

Delivered and verified in the original implementation (`validation.md`, PKG-10–15, all PASS). This amendment's directory rename shifts file *paths* (e.g. `src/publisher/tests/test_integration.py` → `packages/publisher/tests/test_integration.py`) but does not change any import statement, helper signature, or dependency direction established by P2. No new acceptance criteria — re-verified as part of this amendment's gate (full `uv run pytest` run) rather than re-specified.

---

### P3: Decision log correction *(amended, not re-done)*

**User Story**: As a maintainer reading `.specs/STATE.md`, I want AD-031 amended in place to reflect the corrected mechanism, so the decision log stays trustworthy and doesn't read as if the original `setuptools`/`package-dir` approach is still current.

**Why P3**: Housekeeping — doesn't block the fix working, but leaving the log describing a superseded mechanism actively misleads future readers.

**Acceptance Criteria**:

1. WHEN `.specs/STATE.md`'s AD-031 entry is inspected THEN it SHALL contain an amendment note (not a new AD number) documenting the `setuptools` → `uv_build` correction, the IDE-tooling root cause, and the rejected alternatives from this spec's Amendment section.
2. WHEN the root `pyproject.toml`'s `[tool.pytest.ini_options]` comment is inspected THEN it SHALL continue to cite AD-031 (unchanged — the original correction to the stale "AD-030" citation already holds).

**Independent Test**: `.specs/STATE.md`'s AD-031 section contains both the original decision and the amendment note, in one place.

---

## Edge Cases

- WHEN `uv sync` is run after the build-backend switch THEN `uv.lock` SHALL regenerate cleanly with `uv_build` resolved for the three affected members, with no dependency-resolution errors.
- WHEN `docker-compose.yml` and each service's Dockerfile are inspected THEN every path SHALL reference `packages/...`, and every entrypoint/CMD SHALL use the dotted-module form — none SHALL reference `src/...` or bare entrypoints.
- WHEN `docs/codebase/STRUCTURE.md` (and any other doc with literal `src/<pkg>` path references) is inspected THEN it SHALL reference `packages/<pkg>` instead — a mechanical path correction, distinct from the wording/convention sync explicitly deferred in Out of Scope.
- WHEN the test suite is run against a freshly cloned checkout (`uv sync` then `uv run pytest`, Docker running) THEN it SHALL pass with no manual environment setup beyond Docker being available for the Postgres testcontainer.
- WHEN `reimbursement`'s own `agent` submodule (the LangGraph agent definition) is imported THEN it SHALL resolve as `reimbursement.agent`, distinct from the top-level `reimbursement` package itself, with no import ambiguity.

---

## Requirement Traceability

| Requirement ID | Story                                    | Phase  | Status  |
| --------------- | ----------------------------------------- | ------ | ------- |
| PKG-01          | P1: Real package namespacing (corrected)  | Design | Pending |
| PKG-02          | P1: Real package namespacing (corrected)  | Design | Pending |
| PKG-03          | P1: Real package namespacing (corrected)  | Design | Pending |
| PKG-04          | P1: Real package namespacing (corrected)  | Design | Pending |
| PKG-05          | P1: Real package namespacing (corrected)  | Design | Pending |
| PKG-06          | P1: Real package namespacing (corrected)  | Design | Pending |
| PKG-07          | P1: Real package namespacing (corrected)  | Design | Verified (unaffected) |
| PKG-08          | P1: Real package namespacing (corrected)  | Design | Pending |
| PKG-09          | P1: Real package namespacing (corrected)  | Design | Pending |
| PKG-10          | P1: Real package namespacing (corrected)  | Design | Pending |
| PKG-11          | P1: Real package namespacing (corrected)  | Design | Pending — new, IDE resolvability |
| PKG-12–17       | P2: Shared-kernel helper consolidation    | -      | Verified (already delivered, paths re-verified in this amendment's gate) |
| PKG-18          | P3: Decision log correction (amendment)   | Design | Pending |
| PKG-19          | P3: Decision log correction (amendment)   | -      | Verified (unaffected) |

**ID format:** `PKG-[NUMBER]`

**Status values:** Pending → In Design → In Tasks → Implementing → Verified

**Coverage:** 19 total, 0 mapped to tasks (this amendment), 11 net-new/changed, 8 already-verified-and-unaffected — Tasks phase maps the 11 next

---

## Success Criteria

- [ ] `uv run pytest` at the workspace root (no path arguments) passes for all four packages in one session, using one Postgres testcontainer.
- [ ] `grep -rn "package-dir"` across `packages/api`, `packages/publisher`, `packages/reimbursement`'s `pyproject.toml` files returns nothing.
- [ ] `pyright` (pointed at `.venv`) reports 0 `reportMissingImports` against `packages/reimbursement/src/reimbursement/agent/agent.py`, and the equivalent entry modules for `api`/`publisher`.
- [ ] No `src/` directory remains at the workspace root; `docker compose up` starts `api`, `publisher`, and `reimbursement` successfully from `packages/...` paths.
- [ ] `.specs/STATE.md`'s AD-031 entry contains the amendment note.
