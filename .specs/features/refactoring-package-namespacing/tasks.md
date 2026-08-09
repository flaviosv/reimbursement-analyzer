# Fix Service Module-Name Collision via Real Package Namespacing — Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user — do not proceed without it.**

---

**Design**: `.specs/features/refactoring-package-namespacing/design.md`
**Status**: Draft

---

## Test Coverage Matrix

> Generated from codebase, project guidelines, and spec — confirm before Execute. Guidelines found: `docs/codebase/TESTING.md`, root `pyproject.toml`'s `[tool.pytest.ini_options]`, `.claude/CLAUDE.md`'s "test the guarantees infrastructure produces, never the machinery itself" (also recorded as project memory `test-scope-bootstrap-code`).

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------- | ---------------------- | ------------------- | ------------- |
| Build/package config (`pyproject.toml` build-backend, `package-dir`, Dockerfiles) | none | Build/gate only — proven by the full existing suite passing under the new config plus a Docker build+run smoke check; no new config-layer tests (matches this repo's existing "Entity/Config: none — build gate only" pattern) | `src/{api,publisher,reimbursement}/pyproject.toml`, `src/{api,publisher,reimbursement}/Dockerfile` | `uv run pytest`, `docker build --target dev -f src/<pkg>/Dockerfile .` |
| Import-path conversion (bare → dotted) across each package's `src/` and `tests/` | none | Build/gate only — a mechanical rename; correctness is proven by the package's own existing tests continuing to pass under the new dotted imports, not by new tests (no behavior changes) | `src/{api,publisher,reimbursement}/{src,tests}/**/*.py` | `uv run pytest src/<pkg>` |
| Shared test-helper consolidation (`shared/testing.py`) | none | Build/gate only — these are test-infrastructure functions (payload builders, seed helpers, DB-provisioning utilities), not domain logic; exercised indirectly by the integration/route tests that already call them, matching this project's own stated convention against testing test-infrastructure directly | `src/shared/src/shared/testing.py` | `uv run pytest` |
| Decision log (`STATE.md`, `pyproject.toml` inline comment) | none | Docs only | `.specs/STATE.md`, `pyproject.toml` | manual `grep` verification (see task) |

**Coverage Expectation rationale**: This feature introduces zero new domain/business logic — every change is build configuration, import-path mechanics, or a test-infrastructure relocation. Per the strong-default table's own "Entity / config / schema → none, build gate only" row, and per this project's explicit convention (project memory `test-scope-bootstrap-code`: "test the guarantees infrastructure produces, never the machinery itself"), no task in this feature adds dedicated unit tests — every task's gate is the existing suite passing under the changed configuration/imports, which is the correct and sufficient proof for a pure refactor.

## Gate Check Commands

> Generated from codebase — confirm before Execute.

| Gate Level | When to Use | Command |
| ----------- | ------------- | --------- |
| Quick | Immediately after converting one package's imports, before the slower container-backed check | `uv run pytest src/<pkg> -m "not integration"` |
| Full | After each package task's Docker check; after the shared-helper consolidation task; after the final decision-log task | `uv run pytest` (Docker must be running — this project has no CI, so this is also the closest thing to a build gate) |
| Build | Docker image sanity per service | `docker build --target dev -f src/<pkg>/Dockerfile .` (repo root as context, matching `docker-compose.yml`), then a live smoke check: `GET /health` for `api`, one consumed-message log line for `publisher`/`reimbursement` |

---

## Execution Plan

Phases are ordered and run sequentially — each phase completes before the next begins, and tasks within a phase execute in order.

### Phase 1: Per-service package namespacing (P1 — MVP)

```
T1 (api) → T2 (publisher) → T3 (reimbursement)
```

### Phase 2: Shared-kernel test-helper consolidation (P2)

```
T4
```

### Phase 3: Decision log (P3)

```
T5
```

---

## Task Breakdown

### T1: `api` becomes a real, dotted-namespaced package

**What**: Switch `api`'s build backend to `setuptools` with `package-dir` mapping; convert every bare intra-package import (in both `src/` and `tests/`) to the dotted `api.*` form; update the Dockerfile entrypoint; revert `migrate.py`'s resource resolution.
**Where**:
- `src/api/pyproject.toml` — `[build-system]` → `setuptools`/`setuptools.build_meta`; add `[tool.setuptools.package-dir] api = "src"`.
- `src/api/src/__init__.py` — new, minimal (matches the original `agent` package's shape).
- `src/api/src/{main,dependencies,errors,migrate}.py`, `src/api/src/reimbursement/**/*.py` — every bare `from dependencies import ...`, `from errors import ...`, `from reimbursement.* import ...` (already dotted-from-root style, just needs the `api.` prefix) becomes `from api.dependencies import ...`, `from api.errors import ...`, `from api.reimbursement.* import ...`.
- `src/api/src/migrate.py` — `migrations_path()` reverts from `Path(__file__).parent` to `importlib.resources.files("api")`.
- `src/api/tests/helpers.py`, `test_errors.py`, `test_health.py`, `test_main.py`, `reimbursement/create/test_integration.py`, `reimbursement/create/test_route.py`, `reimbursement/update/test_route.py` — bare `from dependencies import ...`, `from errors import ...`, `from main import ...` become `from api.dependencies import ...`, `from api.errors import ...`, `from api.main import ...`.
- `src/api/Dockerfile` — every `CMD`'s `main:app` → `api.main:app`; every `python -m migrate` (if present) → `python -m api.migrate`; remove the now-unnecessary `ENV PYTHONPATH="/app/src/api/src"` line in every stage (`dev`, `migrate`, `prod`).
- `pyproject.toml` (root) — remove `src/api/src` from `pythonpath` (real installed package doesn't need it); `src/api/tests` stays.

**Depends on**: None
**Reuses**: The original `agent` package's `__init__.py`/`[build-system]` shape (git history, pre-AD-018-equivalent flatten) as the template for what a minimal namespaced package's `__init__.py` looks like; `setuptools`' `package-dir` mechanism (verified via Context7 in Design).
**Requirement**: PKG-01, PKG-02, PKG-03, PKG-05, PKG-06, PKG-07

**Tools**:

- MCP: NONE (mechanical `grep`/edit; build-backend mechanics already verified via Context7 during Design)
- Skill: NONE

**Done when**:

- [ ] `uv sync` completes cleanly with `api` resolved via `setuptools`; `uv.lock` diff reviewed and sane.
- [ ] `grep -rnE "^from (dependencies|errors|main|migrate) import|^import (dependencies|errors|main|migrate)\b" src/api` returns nothing (every bare cross-module import converted).
- [ ] `import api; import api.main; import api.dependencies; import api.errors; import api.migrate; import api.reimbursement.create.route` all succeed from a plain `python -c` invocation inside the `uv` venv (proves real installed-package resolution, not a `pythonpath` accident).
- [ ] `docker build --target dev -f src/api/Dockerfile .` succeeds; the container starts and `GET /health` returns 200.
- [ ] Gate check passes: `uv run pytest src/api` (full — includes `@pytest.mark.integration`).
- [ ] Test count: same test count as before this task (no silent deletions) — record the exact number from the `uv run pytest src/api` summary line.

**Tests**: none (build/gate only — see Test Coverage Matrix)
**Gate**: full (`uv run pytest src/api`) + build (`docker build --target dev -f src/api/Dockerfile .` + `/health` smoke check)

**Commit**: `refactor(api): switch to setuptools package-dir, restore dotted-import namespacing`

---

### T2: `publisher` becomes a real, dotted-namespaced package

**What**: Same treatment as T1, for `publisher`. Also fixes a pre-existing stale reference in `publisher`'s own Dockerfile (`COPY src/agent/pyproject.toml` — a leftover from before the `src/agent` → `src/reimbursement` rename, unrelated to this feature's root cause but discovered while editing this file).
**Where**:
- `src/publisher/pyproject.toml` — `[build-system]` → `setuptools`; `[tool.setuptools.package-dir] publisher = "src"`.
- `src/publisher/src/__init__.py` — new.
- `src/publisher/src/{config,consumer,processing}.py` — bare `from config import ...`, `from processing import ...` → `from publisher.config import ...`, `from publisher.processing import ...`.
- `src/publisher/tests/{conftest,fakes,test_config,test_consumer,test_integration,test_processing}.py` — bare `from config import ...`, `import consumer as consumer_module`, `from consumer import ...`, `from processing import ...`, `import processing` → dotted `publisher.*` equivalents. (`from helpers import valid_reimbursement_item` and `from fakes import ...` are **not** touched here — `fakes.py` stays bare-name/`pythonpath`-resolved per `CONVENTIONS.md`'s test-doubles-as-a-module pattern; `helpers` is T4's concern.)
- `src/publisher/Dockerfile` — `CMD`'s `python -m consumer` → `python -m publisher.consumer`; remove `ENV PYTHONPATH="/app/src/publisher/src"`; fix the stale `COPY src/agent/pyproject.toml src/agent/pyproject.toml` line to `COPY src/reimbursement/pyproject.toml src/reimbursement/pyproject.toml`.
- `pyproject.toml` (root) — remove `src/publisher/src` from `pythonpath`; `src/publisher/tests` stays.

**Depends on**: None (independent of T1 — different package, no shared file overlap)
**Reuses**: Same pattern as T1.
**Requirement**: PKG-01, PKG-02, PKG-03, PKG-06

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `uv sync` completes cleanly with `publisher` resolved via `setuptools`; `uv.lock` diff reviewed.
- [ ] `grep -rnE "^from (config|consumer|processing) import|^import (config|consumer|processing)\b" src/publisher` returns nothing.
- [ ] `import publisher; import publisher.consumer; import publisher.config; import publisher.processing` succeed from the `uv` venv.
- [ ] `grep -n "src/agent" src/publisher/Dockerfile` returns nothing.
- [ ] `docker build --target dev -f src/publisher/Dockerfile .` succeeds; the container starts and consumes at least one message end-to-end (or the equivalent existing manual verification this project already uses for `publisher`).
- [ ] Gate check passes: `uv run pytest src/publisher`.
- [ ] Test count: same as before this task, recorded from the summary line.

**Tests**: none (build/gate only)
**Gate**: full (`uv run pytest src/publisher`) + build (`docker build --target dev -f src/publisher/Dockerfile .` + consume smoke check)

**Commit**: `refactor(publisher): switch to setuptools package-dir, restore dotted-import namespacing`

---

### T3: `reimbursement` becomes a real, dotted-namespaced package; standalone pytest root removed

**What**: Same treatment as T1/T2, for `reimbursement` — including its nested `agent/` subpackage (LangGraph internals), which currently has no `__init__.py` and reaches its parent package's `schema`/`config` bare. Deletes the interim-fix's standalone `[tool.pytest.ini_options]` block entirely and restores `reimbursement` to the root's unified pytest config.
**Where**:
- `src/reimbursement/pyproject.toml` — `[build-system]` → `setuptools`; `[tool.setuptools.package-dir] reimbursement = "src"`; **delete** the entire `[tool.pytest.ini_options]` block (`testpaths`, `pythonpath`, `--confcutdir` addopt, markers — markers move to being inherited from the unified root config instead).
- `src/reimbursement/src/__init__.py` — new.
- `src/reimbursement/src/agent/__init__.py` — new (currently absent; needed for `reimbursement.agent` to be a real subpackage, not an implicit namespace package).
- `src/reimbursement/src/{config,consumer,schema,validation}.py` — bare `from config import ...`, `from validation import ...` → `from reimbursement.config import ...`, `from reimbursement.validation import ...`.
- `src/reimbursement/src/agent/agent.py` — `from schema import State` → `from reimbursement.schema import State`; `from nodes import extract_fields, validate, apply_policies, analysis, apply_agent_decision` → `from reimbursement.agent.nodes import extract_fields, validate, apply_policies, analysis, apply_agent_decision` (matches this codebase's established absolute-dotted style, e.g. `api/src/main.py`'s `from reimbursement.create.route import router`). `src/reimbursement/src/agent/nodes/__init__.py`'s existing relative imports (`.analysis`, `.apply_policies`, ...) are already correct and need no change.
- `src/reimbursement/tests/{agent_fakes,conftest,test_config,test_consumer,test_integration,test_validation}.py` — bare `from config import ...`, `import consumer as consumer_module`, `from consumer import ...`, `from validation import ...` → dotted `reimbursement.*` equivalents. (`from helpers import valid_reimbursement_item` and `from agent_fakes import ...` are **not** touched here — same reasoning as T2; `helpers` is T4's concern.)
- `src/reimbursement/Dockerfile` — `CMD`'s `python -m consumer` → `python -m reimbursement.consumer`; remove `ENV PYTHONPATH="/app/src/reimbursement/src"`.
- `pyproject.toml` (root) — add `src/reimbursement` to `testpaths` (currently excluded); add `src/reimbursement/tests` to `pythonpath` (currently absent — needed for `agent_fakes.py`'s bare-name resolution); remove `--confcutdir` special-casing if any leaked into the root file (none currently does — confirming no cleanup needed there beyond what's stated).

**Depends on**: None (independent of T1/T2 — different package; the root `pyproject.toml` edits touch different keys within the same file's `testpaths`/`pythonpath` lists than T1/T2 do, no line-level overlap)
**Reuses**: Same pattern as T1/T2.
**Requirement**: PKG-01, PKG-02, PKG-03, PKG-04, PKG-05, PKG-06, PKG-08, PKG-09

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `uv sync` completes cleanly with `reimbursement` resolved via `setuptools`; `uv.lock` diff reviewed.
- [ ] `grep -rnE "^from (config|consumer|schema|validation) import|^import (config|consumer|schema|validation)\b" src/reimbursement` and `grep -rn "^from schema import\|^from nodes import" src/reimbursement/src/agent` both return nothing.
- [ ] `import reimbursement; import reimbursement.consumer; import reimbursement.config; import reimbursement.agent.agent` succeed from the `uv` venv.
- [ ] `grep -c "tool.pytest.ini_options" src/reimbursement/pyproject.toml` returns `0`.
- [ ] Root `pyproject.toml`'s `testpaths` includes `"src/reimbursement"`; `pythonpath` includes `"src/reimbursement/tests"`.
- [ ] `docker build --target dev -f src/reimbursement/Dockerfile .` succeeds; the container starts and consumes at least one message end-to-end.
- [ ] Gate check passes: `uv run pytest` (the **full, unified** workspace run — this is the first point where all four packages run together again; this is the actual proof the collision is gone).
- [ ] Test count: `api` + `publisher` + `reimbursement` + `shared` totals match the sum of each package's own last-known count (no silent deletions), recorded from the summary line.

**Tests**: none (build/gate only)
**Gate**: full (`uv run pytest`, unified) + build (`docker build --target dev -f src/reimbursement/Dockerfile .` + consume smoke check)

**Commit**: `refactor(reimbursement): switch to setuptools package-dir, restore dotted-import namespacing; drop standalone pytest root`

---

### T4: Consolidate cross-package test helpers into `shared.testing`

**What**: Move `valid_reimbursement_item` and the DB-provisioning utilities from `src/api/tests/helpers.py` into `src/shared/src/shared/testing.py`; reconcile `shared.testing`'s existing near-duplicate seed helpers (`seed_reimbursement`, `seed_reimbursement_with_receipts`, `seed_human_review`) with `api/tests/helpers.py`'s fuller signatures (adding `original_payload` support); repoint every caller; delete the now-dead duplicates from `api/tests/helpers.py`.
**Where**:
- `src/shared/src/shared/testing.py` — add `valid_reimbursement_item`, `POSTGRES_IMAGE`, `MAINTENANCE_DATABASE`, `database_name`, `with_database`, `maintenance_url`, `disposable_database_name`, `guard_is_test_database`; reconcile `seed_reimbursement`/`seed_reimbursement_with_receipts`/`seed_human_review` to accept `original_payload`; update the module docstring to state the broadened scope (test doubles for shared contracts **and** generic cross-service test-provisioning utilities).
- `src/api/tests/helpers.py` — delete the relocated definitions; keep `FakePool`, `_build_client`, `valid_approve_payload`, `valid_reject_payload` (confirmed API-specific, not moved).
- `src/api/tests/**/*.py` (wherever the moved seed helpers / `valid_reimbursement_item` were previously imported from `helpers` locally within `api`'s own tests) — repoint to `from shared.testing import ...`.
- `src/publisher/tests/{test_consumer,test_integration,test_processing}.py` — `from helpers import valid_reimbursement_item` → `from shared.testing import valid_reimbursement_item`.
- `src/reimbursement/tests/test_integration.py` — same change.
- `conftest.py` (root) — `from helpers import (POSTGRES_IMAGE, MAINTENANCE_DATABASE, disposable_database_name, guard_is_test_database, maintenance_url, with_database)` → `from shared.testing import (...)` (same names). `from migrate import apply_migrations` is **not** touched — stays `api`-owned, resolved via `api`'s own `pythonpath`-listed `tests`/`src` as before.

**Depends on**: T1, T2, T3 (touches the same test files those tasks already converted to dotted imports; running after avoids two separate edits landing out of order, and the full-suite gate below needs the collision already fixed to be meaningful)
**Reuses**: `shared/testing.py`'s existing `FakeProducer` class and module structure (extended in place, not replaced).
**Requirement**: PKG-10, PKG-11, PKG-12, PKG-13, PKG-14, PKG-15

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `grep -rn "from helpers import" src/publisher/tests src/reimbursement/tests` returns nothing.
- [ ] `grep -n "POSTGRES_IMAGE\|MAINTENANCE_DATABASE\|disposable_database_name\|guard_is_test_database\|maintenance_url\|with_database\|database_name\|valid_reimbursement_item\|seed_reimbursement\|seed_reimbursement_with_receipts\|seed_human_review" src/api/tests/helpers.py` returns nothing (all relocated, none left duplicated).
- [ ] `conftest.py`'s import line reads `from shared.testing import (...)`, not `from helpers import (...)`.
- [ ] A call to `shared.testing.seed_reimbursement(..., original_payload=...)` behaves identically to `api/tests/helpers.py`'s pre-move version (covered by the existing tests that already exercise `original_payload`, e.g. in `api`'s or `reimbursement`'s integration tests).
- [ ] Gate check passes: `uv run pytest` (full, unified — the definitive proof nothing broke across all four packages).
- [ ] Test count: unchanged from T3's recorded total (no silent deletions).

**Tests**: none (build/gate only — see Test Coverage Matrix)
**Gate**: full (`uv run pytest`)

**Commit**: `refactor(shared): consolidate cross-package test helpers into shared.testing`

---

### T5: Record the decision; fix the stale AD-030 citation

**What**: Append a new AD to `.specs/STATE.md` documenting the `uv_build` → `setuptools` switch and the test-helper consolidation; mark AD-018 and AD-026 as amended by it; fix the root `pyproject.toml` comment that currently miscites "AD-030".
**Where**:
- `.specs/STATE.md` — new `### AD-031 — ...` entry under `## Decisions` (rationale: collision root cause, rejected alternatives — doubled-path revert, pytest-only isolation — per design.md's Tech Decisions table); `AD-018`'s and `AD-026`'s `**Status:**` lines updated to `Amended by AD-031 (build backend: uv_build → setuptools) — 2026-08-09`, matching the existing `**Status:** Amended by AD-018 (path) and AD-019 (root-exception list) — 2026-08-08` style already used for AD-009.
- `pyproject.toml` (root) — the `[tool.pytest.ini_options]` comment currently reading "...AD-026 flagged the risk; AD-030 records this resolution" is corrected to cite AD-031 (or removed if no longer applicable once the config is unified — confirm against the actual post-T3 comment content).

**Depends on**: T1, T2, T3, T4 (the decision record documents the completed state, so it's written last)
**Reuses**: AD-009's existing "Amended by..." status-line format as the template.
**Requirement**: PKG-16, PKG-17

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `grep -c "^### AD-031" .specs/STATE.md` returns `1`.
- [ ] `grep -n "AD-031" .specs/STATE.md` shows it referenced in AD-018's and AD-026's `**Status:**` lines.
- [ ] `grep -n "AD-030" pyproject.toml` returns nothing.
- [ ] Gate check passes: `uv run pytest` (final full-suite confirmation; this task is docs-only, so this is a sanity re-run, not expected to catch anything new).

**Tests**: none (docs only)
**Gate**: full (`uv run pytest`, final confirmation)

**Commit**: `docs(state): record AD-031 — setuptools package-dir namespacing, shared test-helper consolidation`

---

## Phase Execution Map

```
Phase 1 → Phase 2 → Phase 3

Phase 1:  T1 ──→ T2 ──→ T3
Phase 2:  T4
Phase 3:  T5
```

Execution is strictly sequential — there is no intra-phase parallelism. A single agent works one task at a time, in order. (T1/T2/T3 have no functional inter-dependency — they touch disjoint package directories — but are still executed in this fixed order per the skill's sequential-execution model; T3 is placed last within Phase 1 because it's the task that restores the unified `uv run pytest` gate, giving T2's and T1's own gates one already-passing package apiece to build confidence on first.)

Total: 5 tasks, fits a single batch (≤ ~8) — no sub-agent offer needed; runs inline.

---

## Task Granularity Check

| Task | Scope | Status |
| ---- | ------- | -------- |
| T1: `api` namespacing | 1 component (the whole package becomes namespaced — no smaller boundary is independently gateable; a partial conversion leaves broken imports regardless of which subset is picked) | ✅ Granular (cohesive by necessity — see "Resolving compilation dependencies" in this skill's Tasks process) |
| T2: `publisher` namespacing | 1 component, same reasoning as T1 | ✅ Granular |
| T3: `reimbursement` namespacing | 1 component, same reasoning as T1 | ✅ Granular |
| T4: shared test-helper consolidation | 1 component (one coherent relocation + repoint, spanning the callers that already exist) | ✅ Granular |
| T5: decision log | 1 component (one AD entry + one comment fix) | ✅ Granular |

**Granularity check**: Each task's file count is larger than a typical "one function/one file" task, but every file touched within a task is part of the *same* atomic deliverable — the package isn't namespaced until every one of its own bare imports is converted, so there is no smaller unit that is independently testable without an artificially broken intermediate state. This matches the Tasks process's own "merge forward/merge backward" guidance rather than the file-count heuristic.

---

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| ---- | ------------------------ | ---------------- | -------- |
| T1 | None | None (start of Phase 1) | ✅ Match |
| T2 | None | Follows T1 in sequence (same phase, no data dependency) | ✅ Match |
| T3 | None | Follows T2 in sequence (same phase, no data dependency) | ✅ Match |
| T4 | T1, T2, T3 | Phase 2 follows Phase 1 | ✅ Match |
| T5 | T1, T2, T3, T4 | Phase 3 follows Phase 2 | ✅ Match |

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| ---- | ------------------------------ | ------------------ | ----------- | -------- |
| T1: `api` namespacing | Build/package config + import-path conversion | none | none | ✅ OK |
| T2: `publisher` namespacing | Build/package config + import-path conversion | none | none | ✅ OK |
| T3: `reimbursement` namespacing | Build/package config + import-path conversion | none | none | ✅ OK |
| T4: shared test-helper consolidation | Shared test-helper consolidation | none | none | ✅ OK |
| T5: decision log | Decision log (docs) | none | none | ✅ OK |

No violations — every task's layer maps to the matrix's "none — build/gate only" row, and every task's gate is the full existing suite (or the package-scoped subset), which is the correct proof for a pure refactor with zero new domain logic.

---

## Tools Question

For each task above, I'm planning: **no MCPs, no skills** — this is mechanical config/import-path work, already grounded in the codebase's real current imports (inspected directly) and in `setuptools`'/`uv_build`'s actual mechanics (verified via Context7 during Design). Confirm this is right, or tell me if you'd like Context7 re-consulted at any specific task (e.g. if `uv sync` surfaces an unexpected resolution error).
