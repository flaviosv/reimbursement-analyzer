# Fix Service Module-Name Collision via Real Package Namespacing — Validation

**Date**: 2026-08-09
**Spec**: `.specs/features/refactoring-package-namespacing/spec.md`
**Diff range**: `c6ed136..HEAD` (5 commits: T1 `e182a68`, T2 `741a4b5`, T3 `1fb640e`, T4 `3404c34`, T5 `ea65ff4`)
**Verifier**: independent sub-agent (author ≠ verifier)

Note: `ce80603` ("rename agent package to reimbursement"), the commit immediately preceding this range, is prerequisite groundwork from a prior feature and was not evaluated against this feature's spec.

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
