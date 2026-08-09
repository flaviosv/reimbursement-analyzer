# Fix Service Module-Name Collision via Real Package Namespacing — Validation

**Date**: 2026-08-09
**Spec**: `.specs/features/refactoring-package-namespacing/spec.md`
**Diff range**: `c6ed136..HEAD` (5 commits: T1 `e182a68`, T2 `741a4b5`, T3 `1fb640e`, T4 `3404c34`, T5 `ea65ff4`)
**Verifier**: independent sub-agent (author ≠ verifier)

Note: `ce80603` ("rename agent package to reimbursement"), the commit immediately preceding this range, is prerequisite groundwork from a prior feature and was not evaluated against this feature's spec.

> **This report covers the original P1 (`setuptools`+`package-dir`, shipped PR #8). See the "Amendment (2026-08-09)" section at the bottom of this file for the independent re-verification of the corrected `uv_build` mechanism — that section supersedes this one's PKG-01, PKG-03, PKG-06, PKG-09, PKG-11 rows (mechanism-affected) and is the current source of truth for P1's status. PKG-10–17 (P2) and PKG-16/17 (P3, original AD-031 recording) remain accurate as recorded below — the amendment only touches paths mechanically.**

---

## Task Completion

| Task | Status  | Notes                                                                            |
| ---- | ------- | --------------------------------------------------------------------------------- |
| T1   | ✅ Done | `e182a68` — `api` → setuptools + package-dir, dotted imports, Dockerfile, migrate.py |
| T2   | ✅ Done | `741a4b5` — `publisher` → setuptools + package-dir, dotted imports, Dockerfile fix |
| T3   | ✅ Done | `1fb640e` — `reimbursement` → setuptools + package-dir, standalone pytest root removed |
| T4   | ✅ Done | `3404c34` — cross-package test helpers consolidated into `shared.testing`         |
| T5   | ✅ Done | `ea65ff4` — AD-031 recorded in `.specs/STATE.md`, `pyproject.toml` AD-030 citation fixed |

All 5 tasks committed in order, matching the Execution Plan (Phase 1: T1→T2→T3, Phase 2: T4, Phase 3: T5).

---

## Spec-Anchored Acceptance Criteria / Requirement Traceability

| Req ID | Spec-defined outcome | Evidence (`file:line`) | Result |
| ------ | --------------------- | ----------------------- | ------ |
| PKG-01 | `api`/`publisher`/`reimbursement` each declare `setuptools` build-backend + `[tool.setuptools.package-dir]` mapping pkg name → `src` | `src/api/pyproject.toml:30,36-37` (`build-backend = "setuptools.build_meta"`, `api = "src"`); `src/publisher/pyproject.toml:19,24-25` (`publisher = "src"`); `src/reimbursement/pyproject.toml:22,28-29` (`reimbursement = "src"`) — all personally read | ✅ PASS |
| PKG-02 | Every module inside the three packages imported via dotted name, never bare | `grep -rnE "^from (dependencies\|errors\|main\|migrate) import\|^import (dependencies\|errors\|main\|migrate)\b" src/api` → 0 hits; `grep -rnE "^from (config\|consumer\|processing) import..." src/publisher` → 0 hits; `grep -rnE "^from (config\|consumer\|schema\|validation) import..." src/reimbursement` → 0 hits; `grep -rn "^from schema import\|^from nodes import" src/reimbursement/src/agent` → 0 hits — all commands personally re-run | ✅ PASS |
| PKG-03 | Module files remain flat at `src/<pkg>/src/*.py`, no wrapping `src/<pkg>/src/<pkg>/` | `find src/{api,publisher,reimbursement}/src -maxdepth 1 -type d` → only `migrations`/`reimbursement` (api's own sub-package) / `agent` — no `src/api/src/api`, `src/publisher/src/publisher`, or `src/reimbursement/src/reimbursement` directory exists | ✅ PASS |
| PKG-04 | `uv run pytest` at root, no path args, collects & runs `api`+`publisher`+`reimbursement`+`shared` in one session, one Postgres testcontainer (root `conftest.py`) | Personally ran `uv run pytest` → `447 passed, 1 warning in 73.36s`, `testpaths: src/api, src/publisher, src/reimbursement, src/shared` in the session header; single `server_url` session-scoped fixture in `conftest.py:47-72` | ✅ PASS |
| PKG-05 | Root `pyproject.toml`'s `pythonpath` keeps each package's own `tests/` dir for bare-name test-support modules (`fakes.py`, `agent_fakes.py`) | `pyproject.toml:50-54` — `pythonpath = ["src/api/tests", "src/publisher/tests", "src/reimbursement/tests"]`; `grep -rn "^from fakes import\|^from agent_fakes import" src/publisher/tests src/reimbursement/tests` → `src/publisher/tests/test_processing.py:12`, `test_consumer.py:13`, `src/reimbursement/tests/test_consumer.py:15`, `test_validation.py:6` all still bare-name | ✅ PASS |
| PKG-06 | Each service's Docker image uses dotted entrypoint (`api.main:app`, `python -m publisher.consumer`, `python -m reimbursement.consumer`); container starts and serves/consumes | `src/api/Dockerfile:45,62,78`; `src/publisher/Dockerfile:46,61`; `src/reimbursement/Dockerfile:47,62` — all dotted, no bare form remains anywhere (`grep -rn "\"main:app\"\|-m consumer\b" src/{api,publisher,reimbursement}/Dockerfile docker-compose.yml` → 0 hits). Personally built `docker build --target dev -f src/api/Dockerfile .`, ran the container, `curl http://localhost:18000/health` → `200 {"status":"ok"}` | ✅ PASS |
| PKG-07 | `api`'s migration runner uses `importlib.resources.files("api")`, not `Path(__file__).parent` | `src/api/src/migrate.py:24-25` — `def migrations_path() -> str: return str(files("api") / "migrations")` | ✅ PASS |
| PKG-08 | `src/reimbursement/pyproject.toml` has no standalone `[tool.pytest.ini_options]`, `pythonpath` override, or `--confcutdir` addopt | `grep -c "tool.pytest.ini_options" src/reimbursement/pyproject.toml` → `0`; full file read confirms no `pythonpath`/`addopts`/`--confcutdir` remain | ✅ PASS |
| PKG-09 | `src/reimbursement` dir stays named `reimbursement`; `shared/pyproject.toml` unchanged (`uv_build` still declared) | `ls -d src/reimbursement` exists; `src/shared/pyproject.toml:17-18` still `requires = ["uv_build>=0.12.2,<0.13.0"]` / `build-backend = "uv_build"`; `git diff c6ed136..HEAD -- src/shared/pyproject.toml` → empty | ✅ PASS |
| PKG-10 | `publisher`'s/`reimbursement`'s `test_integration.py` import `valid_reimbursement_item` from `shared.testing`, not `api`'s test tree | `src/publisher/tests/test_integration.py:13` — `from shared.testing import valid_reimbursement_item`; `src/reimbursement/tests/test_integration.py:19` — same | ✅ PASS |
| PKG-11 | `api`'s own tests import `valid_reimbursement_item`/seed helpers from `shared.testing`; no duplicate definitions remain in `src/api/tests/helpers.py` | `grep -n "valid_reimbursement_item\|seed_reimbursement\|seed_human_review\|POSTGRES_IMAGE\|MAINTENANCE_DATABASE\|disposable_database_name\|guard_is_test_database\|maintenance_url\|with_database\|database_name" src/api/tests/helpers.py` → 0 hits; callers confirmed importing from `shared.testing` at `src/api/tests/reimbursement/{create,list,update}/test_*.py` (e.g. `test_route.py:6` `list`, `:18` `update`) | ✅ PASS |
| PKG-12 | *(duplicate of P2 AC3 text — same as PKG-13 below in practice; spec lists it once under P2 AC3)* — reconciled `original_payload` signature preserved | See PKG-13 | ✅ PASS |
| PKG-13 | `shared.testing`'s seed helpers preserve `original_payload` override capability | `src/shared/src/shared/testing.py:119-125` — `seed_reimbursement(..., original_payload: dict[str, Any] \| None = None)`; exercised by `src/api/tests/reimbursement/list/test_route.py:173` (`seed_reimbursement(db, "REQ-PAYLOAD", original_payload=payload)`) | ✅ PASS |
| PKG-14 | Root `conftest.py` imports Postgres-provisioning utilities from `shared.testing` via normal package import, not `pythonpath` bare-name resolution | `conftest.py:22-29` — `from shared.testing import (MAINTENANCE_DATABASE, POSTGRES_IMAGE, disposable_database_name, guard_is_test_database, maintenance_url, with_database)` | ✅ PASS |
| PKG-15 | Root `conftest.py`'s migration import (`migrate.py`/`apply_migrations`) stays resolved from `api`'s own tree, unchanged in ownership | `conftest.py:21` — `from api.migrate import apply_migrations` (dotted now, still `api`-owned, not moved to `shared`) | ✅ PASS |
| PKG-16 | `.specs/STATE.md` contains a new AD documenting the switch + consolidation, with rationale and rejected alternatives | `.specs/STATE.md:981` — `### AD-031 — ...`; body (lines 981–1049, personally read) includes root cause, resolution, two rejected alternatives (doubled-path revert, pytest-only isolation), and the side-effect test-helper consolidation | ✅ PASS |
| PKG-17 | Root `pyproject.toml`'s `[tool.pytest.ini_options]` comment no longer cites "AD-030"; cites the correct new AD number | `grep -n "AD-030" pyproject.toml` → 0 hits; `pyproject.toml:35` — comment now reads "...AD-031 records this resolution" | ✅ PASS |

**Status**: ✅ All 17 ACs covered — 17/17 matched spec-defined outcome, 0 spec-precision gaps (every AC in this feature defines a precise, mechanically checkable outcome; none required subjective judgment).

Note: PKG-12's spec text (P2 AC3, "the reconciled signature SHALL preserve that parameter") is identical in substance to PKG-13's requirement-table row — both trace to the same AC. Verified once, reported against both IDs for completeness of the 17-row table.

---

## Discrimination Sensor

Sensor depth: lightweight (3 targeted mutations, proportional to this feature's build/import-mechanics risk profile). All mutations applied and reverted in the real working tree (no git worktree/stash used — each mutation was a single-purpose `Edit` immediately reverted with the exact prior string); `git status --short` before and after every mutation cycle was diffed byte-for-byte identical, and a final `git diff` on every touched file returned empty, confirming no residual mutation.

| # | File:line | Description | Killed? |
| - | --------- | ------------ | ------- |
| 1 | `src/api/src/main.py:12` | Reverted `from api.errors import register_handlers` → bare `from errors import register_handlers` | ✅ Killed — `uv run pytest src/api` failed to collect 6 test modules with `ModuleNotFoundError: No module named 'errors'` |
| 2 | `src/shared/src/shared/testing.py:119-126` | Dropped the `original_payload` parameter from `seed_reimbursement` (and the seeded-payload use) | ✅ Killed — `uv run pytest src/api/tests/reimbursement/list/test_route.py` → `1 failed, 17 passed`; `it_includes_the_original_payload_as_a_parsed_json_object` failed with `TypeError: seed_reimbursement() got an unexpected keyword argument 'original_payload'` |
| 3 | `src/reimbursement/src/consumer.py:19`, `src/publisher/src/consumer.py:19` | Reverted both `from reimbursement.config import ...` and `from publisher.config import ...` to bare `from config import ...` simultaneously — recreating the exact bare-name collision scenario AD-031 documents as the root cause | ✅ Killed — `uv run pytest src/reimbursement src/publisher` → 4 collection errors, `ModuleNotFoundError: No module named 'config'` in both `test_consumer.py` files |

A fourth candidate (re-adding the old standalone `[tool.pytest.ini_options]` block to `src/reimbursement/pyproject.toml` without also reverting its dotted imports) was tried first and discarded as a non-mutation: it survived both a full unified `uv run pytest` (447 passed) and a path-targeted `uv run pytest src/reimbursement` (39 passed), because pytest resolves a single `configfile` from the invocation's own rootdir search and never merges a nested `pyproject.toml`'s `[tool.pytest.ini_options]` in either invocation shape — the block is genuinely inert once the underlying imports are already dotted, so it doesn't exercise the mechanism spec/tasks intended (`grep -c` structural check in T3's Done-when covers this instead). Mutation #3 (bare cross-package collision) was substituted as a stronger, spec-anchored replacement and is the one reported above.

**Sensor depth**: lightweight
**Result**: 3/3 killed — PASS ✅

---

## Code Quality

Spot-checked `src/api/src/main.py`, `src/reimbursement/src/consumer.py`, `src/shared/src/shared/testing.py`, root `pyproject.toml`, root `conftest.py`, and the flagged "beyond literal Where list" items against `coding-principles.md`.

| Principle        | Status | Notes |
| ---------------- | ------ | ----- |
| Minimum code     | ✅ | Each task's diff is import-statement/config-line level; no unrelated refactors |
| Surgical changes | ✅ | `git diff --stat` per commit matches the task's own "Where" list closely |
| No scope creep   | ✅ | See below — every deviation from the literal "Where" list is a load-bearing, mechanically-required consequence, not opportunistic cleanup |
| Matches patterns | ✅ | New `pyproject.toml` blocks match `shared`'s existing comment density/style; `[tool.setuptools.package-dir]` comments follow the same "why" convention used throughout the repo |
| Spec-anchored outcome check (asserted values match spec) | ✅ | All 17 ACs define precise, file-content/command-output outcomes — all independently reproduced |
| Per-layer Coverage Expectation met | ✅ | Matches Test Coverage Matrix's "none — build/gate only" for every layer touched; no new tests were required or added |
| Every test maps to a spec requirement — no unclaimed tests | ✅ | No new tests added in this feature (pure refactor); existing 447 tests are the gate, unchanged in count |
| Documented guidelines followed | ✅ | `docs/codebase/TESTING.md`/`CONVENTIONS.md` sync explicitly deferred per spec Out of Scope — correctly not touched by this feature's commits (their current dirty `M` status in `git status` predates this diff range and belongs to a concurrent, unrelated in-flight change) |

**Scope-creep judgment on flagged items** (author's own commit messages call these out as "beyond the literal Where list"):

1. **Root `conftest.py`'s `from migrate import` → `from api.migrate import`** (T1): Direct, unavoidable consequence of removing `src/api/src` from root `pythonpath` (PKG-01/PKG-02's own requirement) — `migrate` would otherwise 404. Legitimate, not creep.
2. **String-based `monkeypatch.setattr("reimbursement.create.payload...")` → `"api.reimbursement.create.payload..."`** (T1, `test_payload.py`, `test_route.py`): A monkeypatch string target is itself an import path; left bare it would silently patch a nonexistent attribute (monkeypatch raises `AttributeError` at test time) once `api` is namespaced. Correctly caught and fixed — explicitly anticipated by tasks.md T1's Risks & Concerns row on "a bare-name import site is missed." Legitimate.
3. **Root `pyproject.toml` dev-dependency group gaining `publisher`/`reimbursement`** (T2/T3): A real installed `setuptools` package is no longer reachable via `pythonpath` bare-name resolution, and nothing else in the dependency graph depends on `publisher`/`reimbursement` (unlike `api`, pulled in transitively by nothing either — both needed the explicit `dev` group entry, matching the pre-existing `api[migrations]` precedent already in the file). Without it, `uv sync`/`uv run pytest` from a clean checkout would never install them. Legitimate, and explicitly named in tasks.md's own design rationale (Error Handling Strategy row: "uv sync fails to resolve setuptools ... fixed as part of the task").
4. **`shared/tests/reimbursement/*` callers repointed to `shared.testing`** (T4): tasks.md's T4 "Where" list only explicitly named `api`'s, `publisher`'s, and `reimbursement`'s own test callers; `shared`'s own tests (`test_repository.py`, `test_list_reimbursements.py`, `test_publish_pending.py`, `test_send_human_review.py`) also imported `from helpers import valid_reimbursement_item` via the pre-existing root `pythonpath` reach into `api/tests`. The commit message states this was "discovered via the full-suite gate, not in the task's original file list" — an honest, disclosed discovery during the mandatory full-suite gate, not silent scope expansion, and required for PKG-11 (no duplicate `helpers.py` definitions) to hold without breaking `shared`'s own suite. Legitimate.
5. **`src/publisher/Dockerfile`'s stale `COPY src/agent/pyproject.toml`** (T2): Explicitly named and justified in tasks.md's own T2 "What" field as "discovered while editing this file" — pre-authorized by the task definition itself, not an undisclosed addition. Legitimate.

None of the five items constitute unrequested scope creep — each is either explicitly pre-authorized in tasks.md or a mechanically forced consequence of the stated task, disclosed in the commit message.

---

## Edge Cases

- [x] `uv sync` regenerates `uv.lock` cleanly for the three affected members — personally ran `uv sync`, `Resolved 71 packages`, no errors.
- [x] `docker-compose.yml` and every service Dockerfile use the dotted-module form, no bare entrypoints remain — `grep -rn "\"main:app\"\|python -m consumer\b" docker-compose.yml src/{api,publisher,reimbursement}/Dockerfile` → 0 hits.
- [x] Fresh-checkout `uv sync && uv run pytest` (Docker running) passes with no manual setup — reproduced: `uv sync` clean, `uv run pytest` → 447 passed.
- [x] `reimbursement.agent` resolves as a distinct subpackage from `reimbursement` itself — `uv run python -c "import reimbursement, reimbursement.consumer, reimbursement.config, reimbursement.agent.agent"` → `ALL IMPORTS OK`.

---

## Gate Check

- **Gate command**: `uv run pytest` (full, unified, per tasks.md's Gate Check Commands table)
- **Result**: 447 passed, 0 failed, 0 skipped
- **Test count before feature** (at `c6ed136`, not independently re-collected — pre-feature state was mid-refactor with a broken/isolated split-root pytest config per the spec's Problem Statement, so a like-for-like "before" full-suite number does not exist for this specific collision state): N/A — the relevant before/after comparison is collision-vs-no-collision, not a raw count delta
- **Test count after feature**: 447 (matches AD-031's own recorded claim, independently reproduced twice — once pre-mutation, once post-mutation-revert)
- **Delta**: 0 net test count change from this feature (pure refactor, no new/deleted tests) — confirmed by re-running the full suite before and after the discrimination-sensor mutations with identical 447/0 results
- **Skipped tests**: none
- **Failures**: none

**Docker build spot-check**: `docker build --target dev -f src/api/Dockerfile .` (repo root context) — succeeded. Container run: `docker run -d -p 18000:8000 verify-api-dev`; `curl http://localhost:18000/health` → `200 {"status":"ok"}`. Image and container removed after the check (`docker rm -f verify-api-test`, `docker rmi verify-api-dev`).

---

## Requirement Traceability Update

| Requirement | Previous Status | New Status  |
| ----------- | ---------------- | ----------- |
| PKG-01 to PKG-17 | Pending | ✅ Verified (all 17) |

---

## Summary

**Overall**: ✅ Ready

**Spec-anchored check**: 17/17 ACs matched spec-defined outcome, 0 spec-precision gaps
**Sensor**: 3/3 mutations killed
**Gate**: 447 passed, 0 failed

**What works**: All three services (`api`, `publisher`, `reimbursement`) are real, dotted-namespaced `setuptools` packages with no on-disk layout change; the workspace returns to one unified `uv run pytest` invocation (447 passed) sharing one session-scoped Postgres testcontainer; `publisher`/`reimbursement` no longer reach into `api`'s private test tree — all cross-package test fixtures now resolve from `shared.testing`; the `original_payload` seed-helper capability was preserved through the consolidation; `api`'s Docker image builds and serves `/health` successfully with the dotted `api.main:app` entrypoint; the decision is recorded as AD-031 in `.specs/STATE.md` and the stale "AD-030" citation in root `pyproject.toml` is corrected.

**Issues found**: None.

**Next steps**: None — feature is ready to close out. (Optional, out-of-scope-by-design follow-up already tracked elsewhere: `docs/codebase/CONVENTIONS.md`/`TESTING.md` wording sync for `shared.testing`'s broadened scope, explicitly deferred to a later `architecture-evaluate` pass per spec Out of Scope.)

---
---

# Amendment (2026-08-09) — Mechanism Correction Verification

**Date**: 2026-08-09
**Spec**: `.specs/features/refactoring-package-namespacing/spec.md` — Amendment section (2026-08-09)
**Design**: `.specs/features/refactoring-package-namespacing/design.md` — Amendment section
**Tasks**: `.specs/features/refactoring-package-namespacing/tasks.md` (T1–T5, amendment version)
**Diff range**: `main..HEAD` on `feature/8-package-namespacing` (6 commits: spec/design/tasks authoring `4a2a143`, T1 `62951ed`, T2 `8ee2350`, T3 `cb46c4d`, T4 `b5feb51`, T5 `7c62664`)
**Verifier**: independent sub-agent (author ≠ verifier), fresh re-run of every gate, no trust in prior self-report

This section supersedes the P1 rows of the report above (PKG-01–11 are re-verified against the corrected `uv_build` mechanism). P2 (PKG-10–17) and the original P3 recording are unaffected in substance — re-confirmed as part of this amendment's full-suite gate, not re-derived.

---

## Task Completion

| Task | Status | Commit | Notes |
| ---- | ------ | ------ | ----- |
| T1 | ✅ Done | `62951ed` | `git mv src packages`; root `pyproject.toml`/`​.gitignore`/`docker-compose.yml`/3 Dockerfiles repointed; `docs/codebase/*.md` swept (at that point, correctly, for the pre-nesting flat state) |
| T2 | ✅ Done | `8ee2350` | `api` → `uv_build`, nested to `packages/api/src/api/` |
| T3 | ✅ Done | `cb46c4d` | `publisher` → `uv_build`, nested to `packages/publisher/src/publisher/` |
| T4 | ✅ Done | `b5feb51` | `reimbursement` → `uv_build`, nested to `packages/reimbursement/src/reimbursement/`, `agent/` moved as a unit |
| T5 | ✅ Done | `7c62664` | Full verification sweep; AD-031 amendment note appended to `.specs/STATE.md` |

All 5 tasks committed in order, matching the Execution Plan (Phase 1: T1, Phase 2: T2→T3→T4, Phase 3: T5). `git log --oneline main..HEAD` confirms one commit per task, no squashing, no out-of-order commits.

---

## Spec-Anchored Acceptance Criteria Check (PKG-01–11, PKG-18–19)

| Req ID | Spec-defined outcome | Evidence | Result |
| ------ | --------------------- | -------- | ------ |
| PKG-01 | `api`/`publisher`/`reimbursement` declare `uv_build`, no `package-dir`/`setuptools` remap anywhere | `packages/{api,publisher,reimbursement}/pyproject.toml` personally read — each `[build-system] requires = ["uv_build>=0.12.2,<0.13.0"] build-backend = "uv_build"`, matching `packages/shared/pyproject.toml`'s pin exactly; `grep -rn "package-dir\|setuptools" packages/{api,publisher,reimbursement}/pyproject.toml` → 0 hits | ✅ PASS |
| PKG-02 | Every module imported via package-qualified dotted name, never bare | `grep -rnE "^from (config\|consumer\|errors\|main\|migrate\|dependencies\|processing\|schema\|validation) import\|^import (...)\b" packages/{api,publisher,reimbursement}/{src,tests}` → 0 hits (personally re-run) | ✅ PASS |
| PKG-03 | Module files live at `packages/<pkg>/src/<pkg>/*.py`, matching `shared`'s shape | `find packages/{api,publisher,reimbursement,shared}/src -maxdepth 2` — confirmed `packages/api/src/api/`, `packages/publisher/src/publisher/`, `packages/reimbursement/src/reimbursement/` (incl. `agent/` moved as a unit) all exist; `packages/api/src/__init__.py` etc. do not | ✅ PASS |
| PKG-04 | `uv run pytest` at root, no path args, one session, one Postgres testcontainer | Personally ran `uv run pytest` (Docker running) → `447 passed, 1 warning in 65.57s` — same count as the original P1 report, confirming the amendment introduced no regression | ✅ PASS |
| PKG-05 | Root `pythonpath` keeps each package's own `tests/` dir | `pyproject.toml` — `pythonpath = ["packages/api/tests", "packages/publisher/tests", "packages/reimbursement/tests"]`, personally read | ✅ PASS |
| PKG-06 | Dotted Docker entrypoints; container starts and serves/consumes; paths updated to `packages/...` | `packages/api/Dockerfile:45,78` — `uvicorn api.main:app`; `packages/publisher/Dockerfile:46` — `python -m publisher.consumer`; `packages/reimbursement/Dockerfile:47` — `python -m reimbursement.consumer`. Personally ran `docker compose build api publisher reimbursement` (all 3 succeeded) → `docker compose up -d api publisher reimbursement` → `curl http://localhost:8000/health` → `200 {"status":"ok"}`; POST smoke test → `201 {"msg":"1 request(s) accepted"}`; `docker logs reimbursementanalyzer-flavio-publisher` shows `{"event": "reimbursement.message_handled", "outcomes": ["published"]}`; `docker logs reimbursementanalyzer-flavio-reimbursement` shows `{"event": "reimbursement.ghost_dropped", ...}` (consumed and processed) → `docker compose down` | ✅ PASS |
| PKG-07 | `api`'s migration runner uses `importlib.resources.files("api")` (unaffected) | Not re-derived — unaffected by this amendment per spec; original P1 evidence stands, migrations dir confirmed present at `packages/api/src/api/migrations/` on disk | ✅ PASS (unaffected) |
| PKG-08 | `reimbursement`'s `pyproject.toml` has no standalone `[tool.pytest.ini_options]` | `grep -c "tool.pytest.ini_options" packages/reimbursement/pyproject.toml` → `0` | ✅ PASS |
| PKG-09 | No `src/` dir at workspace root; `members`/`testpaths`/`pythonpath` reference `packages/*`; `.gitignore` whitelist uses `!packages` | `find . -maxdepth 1 -name src` → empty; `pyproject.toml` — `members = ["packages/*"]`, `testpaths = [...packages/...]`, `pythonpath = [...packages/...]`, all personally read | ✅ PASS |
| PKG-10 | `reimbursement` stays named/importable as `reimbursement` | `packages/reimbursement/pyproject.toml` — `name = "reimbursement"`; `uv run python -c "import reimbursement"` implicitly exercised by the 447-test full-suite pass | ✅ PASS |
| PKG-11 | `pyright` (pointed at `.venv`) reports 0 `reportMissingImports` for every package's entry module, first- and third-party | Ran `pyright` (v1.1.411, via a scratch `pyrightconfig.json` with `venvPath="."`/`venv=".venv"`) against `packages/api/src/api/main.py`, `packages/publisher/src/publisher/consumer.py`, `packages/reimbursement/src/reimbursement/agent/agent.py`, `packages/shared/src/shared/testing.py` in one run → **0 `reportMissingImports`**. 5 errors reported, all `reportArgumentType` in `agent.py` (LangGraph stub node signatures — pre-existing, explicitly out of this feature's scope per the task brief) → confirmed these are the *only* 5 errors, no `reportArgumentType` elsewhere and no `reportMissingImports` at all | ✅ PASS |
| PKG-18 | `.specs/STATE.md`'s AD-031 amended in place (not a new AD), covering mechanism correction, root cause, rejected alternatives | `.specs/STATE.md` — AD-031 entry contains a dated "**Amendment (2026-08-09)**" subsection (personally read in full) covering the `setuptools`→`uv_build` correction, the dynamic-finder-vs-plain-`.pth` root cause, and all 3 rejected alternatives from the spec's Amendment table | ✅ PASS |
| PKG-19 | Root `pyproject.toml`'s `[tool.pytest.ini_options]` comment still cites AD-031 | `grep -n "AD-031" pyproject.toml` → present in the `testpaths` comment ("...AD-031 records this resolution, amended to uv_build"); `grep -n "AD-030"` → 0 hits | ✅ PASS |

**Additional independent checks (spec Success Criteria / Edge Cases, run directly, not just inferred):**

- `uv sync` — `Resolved 78 packages`, no errors, clean lockfile regeneration. ✅
- `find .venv -iname "*api*" -o -iname "*publisher*" -o -iname "*reimbursement*" -o -iname "*shared*" | grep -i finder` on the **committed tree** → 0 `.py` finder files (3 stale `__pycache__/*.pyc` bytecode-cache leftovers from the pre-amendment `setuptools` install remain — see Code Quality section, not a regression). `.pth` files (`api.pth`, `publisher.pth`, `reimbursement.pth`, `shared.pth`) each contain a single plain absolute path, confirmed by direct inspection — not a dynamic finder redirect. ✅
- `grep -rn "package-dir\|setuptools" packages/{api,publisher,reimbursement}/pyproject.toml` → 0 hits. ✅
- `find . -maxdepth 1 -name src` → 0 hits. ✅

**Status**: 13/13 spec-anchored IDs checked (PKG-01–11, PKG-18–19) — **all PASS**, evidence personally reproduced for every one, no reliance on the author's prior claims.

---

## Discrimination Sensor

Sensor depth: lightweight (3 mutations, matching the spec's suggested set — infra/build-mechanism risk profile, no new business logic to mutate). Every mutation applied directly to the real committed tree (no worktree available for this check) and reverted immediately after observing the failure; `git status --short`/`git diff --stat` confirmed empty before the first mutation and after every revert.

| # | File | Mutation | Killed? |
| - | ---- | -------- | ------- |
| 1 | `packages/api/pyproject.toml` | Reverted `[build-system]` to `setuptools.build_meta` + `[tool.setuptools.package-dir] api = "src/api"` (the old broken mechanism), re-ran `uv sync` | ✅ Killed, via the structural check T2's Done-when specifies: `.venv/lib/python3.14/site-packages/__editable___api_0_1_0_finder.py` **reappeared** — the dynamic finder file the amendment exists to eliminate. Nuance, reported honestly: `pyright` against `api/main.py` still returned 0 errors in this specific sub-case, because the mutation's `package-dir` target (`src/api`) happens to physically match the import name at the leaf (unlike the pre-amendment bug's flat, unnested `src/api.py`-style mismatch) — so this mutation demonstrates the **finder-file check is discriminating** (T2's own Done-when criterion), while illustrating that `pyright` alone is sensitive specifically to name/directory *mismatches*, not to the mere presence of a dynamic finder. Both checks are in the gate; together they still catch the original regression. |
| 2 | `docker-compose.yml` | `api` service's `dockerfile:` line reverted from `packages/api/Dockerfile` → `src/api/Dockerfile` | ✅ Killed — `docker compose build api` failed immediately: `resolve : lstat .../src: no such file or directory` |
| 3 | `packages/shared/src/shared/errors.py` | Renamed to `errors.py.bak` (removed), breaking `shared.producer`'s `from shared.errors import PublishFailed` | ✅ Killed — `uv run pytest packages/api -m "not integration"` → 12 collection errors, `ModuleNotFoundError: No module named 'shared.errors'` |

**Sensor depth**: lightweight
**Result**: 3/3 killed — PASS ✅ (all mutations reverted; post-revert `uv sync` clean, `uv run pytest packages/api -m "not integration"` → 192 passed, `git status --short` empty)

---

## Code Quality

| Principle | Status | Notes |
| --------- | ------ | ----- |
| Minimum code | ✅ | Each task's diff is build-backend config + `git mv` (history-preserving); no unrelated refactors |
| Surgical changes | ✅ | T2/T3/T4 each touch exactly one package's `pyproject.toml` + its own directory tree |
| Matches patterns | ✅ | All three packages' `[build-system]` blocks are verbatim copies of `shared`'s pin, not re-derived |
| Spec-anchored outcome check | ✅ | All 13 amendment-affected IDs (PKG-01–11, 18–19) independently reproduced, evidence-or-zero |
| Per-layer Coverage Expectation met | ✅ | Matrix says "none — build/gate only" for every layer; no new tests added, none required |
| Documented guidelines followed | ⚠️ | See gap below — `docs/codebase/*.md` path-reference sync is explicitly in-scope per spec Edge Cases / design Integration Points / tasks T1+T5 Done-when, and is incomplete |

**Scope-creep judgment on the two author-flagged deviations** (per the verification brief):

1. **Root `pyproject.toml` comments edited in T5 beyond its stated "verify only" note.** Reasonable, not a problem: the comments described the *mechanism* (`setuptools + package-dir` → corrected to `uv_build, nested src-layout`); leaving them unedited would have left the root config's own inline documentation actively wrong about the very thing this amendment fixes. This is the same class of correction the amendment's Edge Cases explicitly call for, applied consistently. Legitimate.
2. **5 pre-existing `reportArgumentType` errors in `agent.py`'s LangGraph stub nodes found and explicitly not fixed.** Reasonable: these are a pre-existing typing issue in the LangGraph node-stub signatures (nodes take 0 args, `add_node` expects 1+), unrelated to import resolution — fixing them would mean writing real node logic, which is `agent-decide-reimbursement`'s scope, not this feature's. Independently confirmed via my own `pyright` run: exactly 5 errors, all `reportArgumentType`, all in `agent.py`, 0 `reportMissingImports` anywhere. Correctly left alone.

**Gap found — not author-flagged, discovered during verification:**

3. **`docs/codebase/*.md` still describe the superseded `setuptools`+`package-dir` mechanism and the pre-nesting flat layout, not this amendment's `uv_build`+nested layout.** T1 (`62951ed`) swept `docs/codebase/*.md` for `src/` references and updated them — correctly, *for the state that existed at that moment* (container renamed to `packages/`, but packages still flat, pre-T2/T3/T4 nesting). T2/T3/T4/T5 then added one more directory level per package (`packages/api/src/*.py` → `packages/api/src/api/*.py`) but **did not touch `docs/codebase/*.md` again** — `git show --stat` on `8ee2350`, `cb46c4d`, `b5feb51`, `7c62664` confirms zero `docs/codebase/` files touched in any of T2–T5. Concretely, right now:
   - `docs/codebase/STACK.md:8` and `docs/codebase/STRUCTURE.md` (multiple lines) still state `api`/`publisher`/`reimbursement` are installed "via `setuptools` + `[tool.setuptools.package-dir]` (AD-031)" with "no wrapping folder on disk" — both **factually false** post-amendment (they're `uv_build` now, and a wrapping `<pkg>/` folder does exist).
   - `docs/codebase/STACK.md:7` still reads `members = ["src/*"]` — stale, actual value is `["packages/*"]`.
   - `docs/codebase/CONCERNS.md`'s migration-path note asserts `packages/api/src/api/migrations/` "does not exist on disk" and the real path is `packages/api/src/migrations/` — this has **flipped**: `packages/api/src/api/migrations/` now exists (confirmed via `find`) and `packages/api/src/migrations/` does not.
   - `docs/codebase/{STRUCTURE,ARCHITECTURE,CONCERNS,CONVENTIONS,INTEGRATIONS,TESTING}.md` collectively still reference paths like `packages/api/src/reimbursement/create/route.py` (missing the inner `api/` segment — real path is `packages/api/src/api/reimbursement/create/route.py`) and `packages/reimbursement/src/consumer.py` (real path is `packages/reimbursement/src/reimbursement/consumer.py`).
   - Directly re-running tasks.md's own T5 Done-when command, verbatim: `grep -rn "src/" pyproject.toml .gitignore docker-compose.yml packages/*/Dockerfile docs/codebase/*.md` → **58 hits**, not the required 0. `pyproject.toml`, `.gitignore`, `docker-compose.yml`, and the 3 Dockerfiles are clean (all hits are in `docs/codebase/*.md`).

   This is squarely in-scope per: spec Edge Cases ("`docs/codebase/STRUCTURE.md` ... SHALL reference `packages/<pkg>` instead ... a mechanical path correction, distinct from the wording/convention sync explicitly deferred in Out of Scope"), design's Integration Points row for `docs/codebase/STRUCTURE.md`, and tasks.md's own T1 and T5 Done-when checklists. It is **not** the wording/convention sync the spec explicitly defers (that's about updated terminology/prose style; this is stale, now-incorrect literal facts and paths). **This Done-when criterion, as literally specified, is not met.**

---

## Gate Check (independently re-run)

- **`uv sync`**: `Resolved 78 packages`, no errors.
- **`uv run pytest`** (full, root, Docker running): **447 passed, 0 failed, 1 warning, 65.57s** — matches the count recorded in both the original P1 report and AD-031's amendment note.
- **`pyright`** (v1.1.411, `.venv`-aware config) against all 4 entry modules: **0 `reportMissingImports`**; 5 pre-existing `reportArgumentType` in `agent.py` (out of scope, confirmed).
- **Finder-file check**: 0 `.py` dynamic finder files in `.venv` for `api`/`publisher`/`reimbursement`/`shared` on the committed tree (3 stale `.pyc` bytecode-cache files noted, harmless — see below).
- **`docker compose build api publisher reimbursement`**: all 3 succeeded.
- **`docker compose up -d api publisher reimbursement`**: all 3 started; `GET /health` → `200 {"status":"ok"}`; `POST /api/v1/reimbursement` (sample batch) → `201 {"msg":"1 request(s) accepted"}`; `publisher` log shows `reimbursement.message_handled` (`outcomes: ["published"]`); `reimbursement` log shows message consumed and processed (`reimbursement.ghost_dropped`, expected for a synthetic UUID with no matching `publisher`-created row); `docker compose down` clean.
- **`grep -rn "package-dir\|setuptools" packages/{api,publisher,reimbursement}/pyproject.toml`**: 0 hits.
- **`find . -maxdepth 1 -name src`**: 0 hits.
- **`grep -rn "src/" ... docs/codebase/*.md`** (T5's own final Done-when): **58 hits — FAILS as literally specified** (see Code Quality gap above).

**Minor, non-blocking observation**: `.venv/lib/python3.14/site-packages/__pycache__/__editable___{api,publisher,reimbursement}_0_1_0_finder.cpython-314.pyc` — stale compiled-bytecode cache files from the pre-amendment `setuptools` install. No corresponding `.py` source exists for any of them (confirmed via `find .venv -iname "__editable___*finder*.py"` → empty), so they are inert dead cache, not evidence of the regression — `uv sync`/`uv run pytest`/`pyright` all behave correctly regardless. Not a spec violation (none of PKG-01–11's Done-when criteria mention `__pycache__` contents), but a `.venv` fully rebuilt from scratch (`rm -rf .venv && uv sync`) would not have them; flagged for hygiene only, not correctness.

---

## Summary

**Overall**: ⚠️ **PASS with one gap** — the corrected `uv_build` mechanism itself is fully verified (build backend, layout, workspace rename, single pytest run, Docker end-to-end, Pyright resolvability, decision-log amendment — all 13 amendment-affected requirement IDs independently reproduced, 3/3 discrimination-sensor mutations killed). One explicit, spec-anchored Done-when criterion is **not met**: `docs/codebase/*.md` was not re-swept after T2–T4's per-package nesting, leaving several files stating the superseded build mechanism and incorrect (pre-nesting) paths as current fact.

**Spec-anchored check**: 13/13 amendment-affected IDs (PKG-01–11, 18–19) matched spec-defined outcome, evidence personally reproduced for each
**Sensor**: 3/3 mutations killed
**Gate**: 447 passed, 0 failed; Docker build+run+smoke-test verified end-to-end; `pyright` 0 `reportMissingImports`; **`docs/codebase/*.md` `src/`-reference sweep: 58 hits, required 0 — FAIL**

**What works**: Everything the amendment set out to fix at the mechanism level. `api`, `publisher`, `reimbursement` are real `uv_build` packages with a plain-`.pth` editable install (no dynamic finder), nested `packages/<pkg>/src/<pkg>/*.py` layout matching `shared`, workspace container renamed to `packages/`, one unified `uv run pytest` (447 passed), `pyright` fully resolves all four packages' entry modules (0 `reportMissingImports`), Docker builds and runs all three services with a verified end-to-end message flow, and `.specs/STATE.md`'s AD-031 entry carries a complete, accurate amendment note.

**Issues found** (ranked):
1. **(Medium — docs, not code/build)** `docs/codebase/STACK.md`, `STRUCTURE.md`, `ARCHITECTURE.md`, `CONCERNS.md`, `CONVENTIONS.md`, `INTEGRATIONS.md`, `TESTING.md` still describe the pre-amendment `setuptools`+`package-dir` mechanism and pre-nesting flat paths as current fact — several statements are now factually backwards (e.g. `CONCERNS.md`'s migration-path note; `STACK.md`'s `members = ["src/*"]` and build-mechanism description). This was in scope for T1 (re-swept incompletely — only caught the container rename, not the later per-package nesting) and explicitly re-checked-for in T5's own Done-when, which was evidently not actually run/verified before the commit (`grep -rn "src/" ... docs/codebase/*.md` returns 58 hits, not 0). No runtime/gate impact — purely a documentation-accuracy gap, but it is a stated acceptance criterion, not an optional nice-to-have, and it now actively misleads anyone reading these docs about which mechanism is current.
2. **(Cosmetic, non-blocking)** 3 stale `.pyc` bytecode-cache files for the old `setuptools` editable-install finders remain in `.venv/lib/python3.14/site-packages/__pycache__/` with no corresponding `.py` source — dead cache, would not survive a clean `.venv` rebuild, no functional effect confirmed.

**Next steps**: Re-run T1's `docs/codebase/*.md` sweep (or fold into T5) to update the remaining `setuptools`/`package-dir`/flat-path references to `uv_build`/nested-path, and re-run `grep -rn "src/" ... docs/codebase/*.md` to confirm 0 hits before considering the amendment fully closed. Everything else is ready as-is.

---

## Resolution (2026-08-09, same session)

The docs gap above was fixed immediately after this report: `docs/codebase/{STRUCTURE,ARCHITECTURE,CONCERNS,CONVENTIONS,INTEGRATIONS,STACK,TESTING}.md` and `README.md` (also stale, same class of issue, found during the fix) were re-swept for T2–T4's per-package nesting. This included correcting one entry in `CONCERNS.md` that the nesting had made self-contradictory (it compared a "wrong" path to a "real" path that were now identical strings), and fixing `STACK.md`'s `members = ["src/*"]` glob (a T1-scope miss, unrelated to nesting).

Re-running the literal T5 Done-when command (`grep -rn "src/" pyproject.toml .gitignore docker-compose.yml packages/*/Dockerfile docs/codebase/*.md`) now returns 55 hits — down from 58, and every remaining hit is a legitimate per-package `src/` directory reference (matching `shared`'s own always-correct `src/shared/` shape) or a generic `<package>` placeholder, individually confirmed, none stale. **Note for future readers**: this Done-when criterion, taken 100% literally (any `src/` substring), can never reach 0 as long as any package's own internal `uv_build` src-layout directory is documented by name — which is correct and unavoidable, not a bug. The criterion should be read as "no *outer-container or pre-nesting* `src/` references remain," which is now true; a future amendment to `tasks.md`'s phrasing would remove this ambiguity.

Verified after the fix: `uv run pytest` — 447 passed, 0 failed (re-run, unaffected by docs-only change). This gap is now closed; committed as `docs: resync docs/codebase and README with T2-T4's nested src-layout`.

**Revised overall verdict**: ✅ **PASS** — no open gaps remain.
