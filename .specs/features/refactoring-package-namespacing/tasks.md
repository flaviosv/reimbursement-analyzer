# Fix Service Module-Name Collision via Real Package Namespacing — Tasks (Amendment)

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user — do not proceed without it.**

---

**Design**: `.specs/features/refactoring-package-namespacing/design.md`
**Status**: Approved

**Scope note**: This supersedes the original T1–T5 (already implemented and verified — `validation.md`). Those tasks shipped `setuptools`+`package-dir`, which fixed the pytest collision but broke Pyright resolution. These 5 tasks correct the mechanism only — P2 (shared-kernel consolidation) and P3 (decision-log correction, original content) are NOT redone, only mechanically touched by file moves.

**Batch sizing**: 5 tasks total, single batch (≤ ~8 threshold) — executes inline, no sub-agent offer needed.

---

## Test Coverage Matrix

> Generated from codebase, project guidelines, and spec — confirm before Execute. Guidelines found: `docs/codebase/TESTING.md`, root `pyproject.toml`'s `[tool.pytest.ini_options]`, `CLAUDE.md`'s "test the guarantees infrastructure produces, never the machinery itself" (project memory `test-scope-bootstrap-code`). Same conclusion as the original tasks.md — this amendment is also a pure refactor with zero new domain logic.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------- | ---------------------- | ------------------- | ------------- |
| Build/package config (`pyproject.toml` build-backend, directory layout, `.gitignore`, `docker-compose.yml`, Dockerfiles) | none | Build/gate only — proven by the full existing suite passing under the new layout, a Docker build+run smoke check, and (new for this amendment) a `pyright` static-resolution check | `packages/*/pyproject.toml`, `docker-compose.yml`, `packages/*/Dockerfile`, `.gitignore` | `uv run pytest`, `docker compose build <svc>`, `pyright` |
| Directory/path relocation (`git mv`, no import-statement changes) | none | Build/gate only — pure file relocation; correctness proven by each package's own existing tests continuing to pass, not new tests (no behavior change, imports were already dotted) | `packages/**/*.py` | `uv run pytest packages/<pkg>` |
| Decision log (`STATE.md` amendment) | none | Docs only | `.specs/STATE.md` | manual `grep` verification |

**Coverage Expectation rationale**: Zero new domain/business logic — every change is directory relocation, build-backend config, or path references. No task adds dedicated unit tests; each task's gate is the existing suite (+ a new `pyright` check, since IDE resolvability is this amendment's actual deliverable and needs its own verification, not just "tests still pass").

## Gate Check Commands

> Generated from codebase — confirm before Execute.

| Gate Level | When to Use | Command |
| ----------- | ------------- | --------- |
| Quick | After each per-package task (T2–T4), before the slower full-suite check | `uv run pytest packages/<pkg> -m "not integration"` |
| Full | After T1 (rename) and after each of T2–T4 | `uv run pytest` (Docker must be running) |
| Static | After each of T2–T4, and again in T5 for all four packages | `pyright` pointed at `.venv` (via a scratch `pyrightconfig.json` or `--pythonpath .venv/bin/python`), checked against that package's entry module — 0 `reportMissingImports` |
| Build | Docker image sanity, once per touched Dockerfile | `docker compose build <service>` then `docker compose up -d <service>`, smoke check (`GET /health` for `api`; one consumed-message log line for `publisher`/`reimbursement`), then `docker compose down` |

---

## Execution Plan

Phases are ordered and run sequentially — each phase completes before the next begins, and tasks within a phase execute in order.

### Phase 1: Foundation — workspace container rename

```
T1
```

### Phase 2: Per-package mechanism correction (P1, corrected)

```
T2 (api) → T3 (publisher) → T4 (reimbursement)
```

### Phase 3: Verification & decision log (P3, amendment)

```
T5
```

---

## Task Breakdown

### T1: Rename workspace container `src/` → `packages/` (all four packages)

**What**: `git mv src packages` (single operation, preserves history for all four packages at once); update root `pyproject.toml`'s `[tool.uv.workspace] members`, `testpaths`, `pythonpath` from `src/...` to `packages/...`; update `.gitignore`'s whitelist (`!src`/`!src/**` → `!packages`/`!packages/**`); update `docker-compose.yml`'s `dockerfile:` and bind-mount paths (10 lines across `api`/`publisher`/`reimbursement` services); update each of the 3 Dockerfiles' `COPY` path prefixes (directory-level copies, e.g. `COPY src/reimbursement src/reimbursement` → `COPY packages/reimbursement packages/reimbursement` — entrypoints/CMDs are untouched here, they don't reference the container name); sweep `docs/codebase/*.md` for literal `src/<pkg>` path references and correct to `packages/<pkg>`.
**Where**:
- `src/` → `packages/` (repo root, `git mv`)
- `pyproject.toml` (root) — `members`, `testpaths`, `pythonpath`
- `.gitignore`
- `docker-compose.yml`
- `packages/{api,publisher,reimbursement}/Dockerfile` — `COPY` path prefixes only
- `docs/codebase/STRUCTURE.md` and any other `docs/codebase/*.md` matching `grep -rln "src/"`

**Depends on**: None
**Reuses**: N/A — mechanical rename
**Requirement**: PKG-09

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `find . -maxdepth 1 -name src` returns nothing; `packages/{api,publisher,reimbursement,shared}` exist with all prior content intact (`git mv`, not copy+delete, so `git log --follow` on a moved file still shows pre-move history)
- [ ] `grep -rn "\bsrc/\(api\|publisher\|reimbursement\|shared\)\b" pyproject.toml .gitignore docker-compose.yml packages/*/Dockerfile docs/codebase/*.md` returns nothing
- [ ] `uv sync` succeeds with no errors (build backend/layout inside each package unchanged at this point — still `setuptools`+`package-dir`, just relocated; this task does NOT touch that)
- [ ] Gate check passes: `uv run pytest` (full) — 447 passed, same count as before the rename
- [ ] `docker compose build api` succeeds (Dockerfile paths now correct; build-backend/entrypoint unchanged)

**Tests**: none (build/gate only, per matrix)
**Gate**: full + build

**Commit**: `refactor(workspace): rename src/ container to packages/, matching uv's documented workspace layout`

---

### T2: `api` — switch to `uv_build`, nest to `packages/api/src/api/`

**What**: Read `packages/shared/pyproject.toml`'s exact `uv_build` version pin (don't assume it); replace `api`'s `[build-system]` (`setuptools`/`package-dir`) with the same `uv_build` pin, no package-dir remap; `git mv packages/api/src/*.py` (and subdirs) into a new `packages/api/src/api/` directory, moving `packages/api/src/__init__.py`'s content to `packages/api/src/api/__init__.py` (the outer `src/` gets no `__init__.py` of its own, matching `shared`'s shape).
**Where**:
- `packages/api/pyproject.toml` — `[build-system]`
- `packages/api/src/` → `packages/api/src/api/` (`git mv` for every file/subdir currently at `packages/api/src/*`, including the `reimbursement/` sub-package)

**Depends on**: T1
**Reuses**: `packages/shared/pyproject.toml`'s `[build-system]` block (copy verbatim, substituting nothing)
**Requirement**: PKG-01, PKG-03

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `packages/api/pyproject.toml` has no `[tool.setuptools.package-dir]` or `setuptools` reference; `[build-system]` matches `packages/shared/pyproject.toml`'s `uv_build` pin exactly
- [ ] `packages/api/src/api/__init__.py` exists; `packages/api/src/__init__.py` does not
- [ ] `uv sync` succeeds; `.venv/lib/python*/site-packages/api.pth` (or equivalent) is a plain static path, not a `__editable___api_*_finder.py` dynamic finder — `find .venv -iname "*api*" -not -path "*__pycache__*"` confirms no finder file remains for `api`
- [ ] Gate check passes: `uv run pytest packages/api -m "not integration"` (quick), then `uv run pytest` (full)
- [ ] `pyright` check: 0 `reportMissingImports` against `packages/api/src/api/main.py`, covering both first-party (`api.*`) and third-party imports
- [ ] `docker compose build api` succeeds; `docker compose up -d api` then `curl http://localhost:<port>/health` returns 200; `docker compose down`

**Tests**: none (build/gate only, per matrix)
**Gate**: full + static + build

**Commit**: `refactor(api): switch to uv_build, adopt nested src-layout matching shared`

---

### T3: `publisher` — switch to `uv_build`, nest to `packages/publisher/src/publisher/`

**What**: Same treatment as T2, for `publisher`.
**Where**:
- `packages/publisher/pyproject.toml` — `[build-system]`
- `packages/publisher/src/` → `packages/publisher/src/publisher/` (`git mv`)

**Depends on**: T1
**Reuses**: `packages/shared/pyproject.toml`'s `[build-system]` block; same pattern as T2
**Requirement**: PKG-01, PKG-03

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `packages/publisher/pyproject.toml` has no `setuptools`/`package-dir` reference; `[build-system]` matches `shared`'s pin
- [ ] `packages/publisher/src/publisher/__init__.py` exists; `packages/publisher/src/__init__.py` does not
- [ ] `uv sync` succeeds; no dynamic finder file remains for `publisher` in `.venv`
- [ ] Gate check passes: `uv run pytest packages/publisher -m "not integration"` (quick), then `uv run pytest` (full)
- [ ] `pyright` check: 0 `reportMissingImports` against `packages/publisher/src/publisher/consumer.py`
- [ ] `docker compose build publisher` succeeds; `docker compose up -d publisher` produces at least one consumed-message log line; `docker compose down`

**Tests**: none (build/gate only, per matrix)
**Gate**: full + static + build

**Commit**: `refactor(publisher): switch to uv_build, adopt nested src-layout matching shared`

---

### T4: `reimbursement` — switch to `uv_build`, nest to `packages/reimbursement/src/reimbursement/`

**What**: Same treatment as T2/T3, for `reimbursement`, including its `agent/` subpackage (`agent.py`, `nodes/`, `prompts/`) moving as a unit — no internal restructuring inside `agent/` needed, `prompts/` stays a PEP 420 implicit namespace package (no `__init__.py`), unchanged.
**Where**:
- `packages/reimbursement/pyproject.toml` — `[build-system]`
- `packages/reimbursement/src/` → `packages/reimbursement/src/reimbursement/` (`git mv`, including `agent/`)

**Depends on**: T1
**Reuses**: `packages/shared/pyproject.toml`'s `[build-system]` block; same pattern as T2/T3
**Requirement**: PKG-01, PKG-03, PKG-10

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `packages/reimbursement/pyproject.toml` has no `setuptools`/`package-dir` reference; `[build-system]` matches `shared`'s pin; no standalone `[tool.pytest.ini_options]` block present (already true, re-confirm it stayed that way)
- [ ] `packages/reimbursement/src/reimbursement/__init__.py` and `packages/reimbursement/src/reimbursement/agent/__init__.py` exist; `packages/reimbursement/src/__init__.py` does not
- [ ] `uv sync` succeeds; no dynamic finder file remains for `reimbursement` in `.venv`
- [ ] Gate check passes: `uv run pytest packages/reimbursement -m "not integration"` (quick), then `uv run pytest` (full)
- [ ] `pyright` check: 0 `reportMissingImports` against `packages/reimbursement/src/reimbursement/agent/agent.py` — the original failing case from the spec's Amendment section, now resolved
- [ ] `docker compose build reimbursement` succeeds; `docker compose up -d reimbursement` produces at least one consumed-message log line; `docker compose down`

**Tests**: none (build/gate only, per matrix)
**Gate**: full + static + build

**Commit**: `refactor(reimbursement): switch to uv_build, adopt nested src-layout matching shared`

---

### T5: Final verification sweep + AD-031 amendment note

**What**: Run the full verification matrix across all four packages in one pass (pytest, pyright, docker compose up for all three services simultaneously via `docker compose up -d api publisher reimbursement`); append an amendment note to AD-031 in `.specs/STATE.md` documenting the mechanism correction, root cause, and rejected alternatives (from spec's Amendment section); confirm the root `pyproject.toml`'s existing AD-031 comment citation still reads correctly (no change expected — verify only).
**Where**:
- `.specs/STATE.md` — AD-031 entry, amendment note appended in place
- `pyproject.toml` (root) — verify only, no edit expected

**Depends on**: T2, T3, T4
**Reuses**: AD-018/AD-026's existing "Amended by..." status-line convention as the style template
**Requirement**: PKG-04, PKG-11, PKG-18, PKG-19

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `.specs/STATE.md`'s AD-031 entry contains an amendment note (dated, not a new AD number) covering: `setuptools` → `uv_build` correction, the dynamic-finder-vs-plain-`.pth` root cause, and the 3 rejected alternatives from spec's Amendment table
- [ ] `uv run pytest` (full, root, no path args) — all four packages collect and pass in one session, one Postgres testcontainer
- [ ] `pyright` reports 0 `reportMissingImports` across all four packages' entry modules (`api/main.py`, `publisher/consumer.py`, `reimbursement/agent/agent.py`, `shared/testing.py`) in a single run
- [ ] `docker compose up -d api publisher reimbursement` — all three start; `api`'s `/health` returns 200; `publisher`/`reimbursement` each show a consumed-message log line; `docker compose down`
- [ ] `grep -rn "src/"` across `pyproject.toml`, `.gitignore`, `docker-compose.yml`, `packages/*/Dockerfile`, `docs/codebase/*.md` returns nothing (final confirmation, not just T1's)
- [ ] `grep -rn "package-dir\|setuptools"` across `packages/{api,publisher,reimbursement}/pyproject.toml` returns nothing

**Tests**: none (docs + final gate, per matrix)
**Gate**: full + static + build

**Commit**: `docs(specs): amend AD-031 — correct setuptools/package-dir to uv_build for Pyright resolvability`

---

## Phase Execution Map

```
Phase 1 → Phase 2 → Phase 3

Phase 1:  T1
Phase 2:  T2 ──→ T3 ──→ T4
Phase 3:  T5
```

Execution is strictly sequential — no intra-phase parallelism, even though T2/T3/T4 have no dependency on each other (each depends only on T1) — sequenced in the listed order for a single worker/session.

---

## Task Granularity Check

| Task | Scope | Status |
| ---- | ----- | ------ |
| T1: Rename `src/` → `packages/`, repoint all path references | 1 mechanical rename + config sync across a bounded, enumerated file list | ✅ Granular (bounded scope, no logic change) |
| T2: `api` build-backend switch + nesting | 1 package | ✅ Granular |
| T3: `publisher` build-backend switch + nesting | 1 package | ✅ Granular |
| T4: `reimbursement` build-backend switch + nesting | 1 package | ✅ Granular |
| T5: Final verification + decision log | 1 verification sweep + 1 doc entry | ✅ Granular |

---

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| ---- | ----------------------- | -------------- | ------ |
| T1 | None | No incoming arrow | ✅ Match |
| T2 | T1 | T1 → T2 | ✅ Match |
| T3 | T1 | T1 → T2 → T3 (sequenced after T2, depends only on T1) | ✅ Match |
| T4 | T1 | T3 → T4 (sequenced after T3, depends only on T1) | ✅ Match |
| T5 | T2, T3, T4 | T4 → T5 (last in sequence; T2/T3 already complete by the time T5 runs) | ✅ Match |

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| ---- | ---------------------------- | ----------------- | ----------- | ------ |
| T1 | Build/package config, directory relocation | none | none | ✅ OK |
| T2 | Build/package config, directory relocation | none | none | ✅ OK |
| T3 | Build/package config, directory relocation | none | none | ✅ OK |
| T4 | Build/package config, directory relocation | none | none | ✅ OK |
| T5 | Decision log (docs) | none | none | ✅ OK |

No violations — every task's layer maps to the matrix's "none, build/gate only" row, consistent with this being a pure infrastructure/layout correction.

---

## Tips

- **`git mv`, never plain `mv`** — preserves file history through the rename; verify with `git log --follow` on a sample moved file if in doubt.
- **Each per-package task is self-contained** — T2/T3/T4 don't touch each other's files; a failure in one doesn't block starting the next from a clean state (though they still execute in sequence per this project's convention).
- **`pyright` needs a `.venv`-aware invocation** — bare `pyright <file>` without pointing at the interpreter/venv will misresolve everything; use the pattern verified in the spec's Amendment section (`pyrightconfig.json` with `venvPath`/`venv`, or `--pythonpath .venv/bin/python`).
