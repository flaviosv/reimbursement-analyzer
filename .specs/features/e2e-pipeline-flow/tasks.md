# E2E Pipeline Flow & Dotenv-First Runtime Configuration Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user — do not proceed without it.**

---

**Design**: `.specs/features/e2e-pipeline-flow/design.md`
**Status**: Draft

---

## Test Coverage Matrix

> Generated from codebase, project guidelines, and spec — confirm before Execute. Guidelines found: `docs/codebase/TESTING.md` (test organization, marker conventions, gate commands), `CLAUDE.md` (traceability requirement — informs `E2E-02`'s LangFuse assertion).

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------- | --------------------- | ----------------- | ------------ |
| Docker-compose / `.env.sample` config parity | Unit | Exact topology-allowlist match per service + full required-var coverage in `.env.sample`, mirroring `test_compose_parity.py`'s existing precedent | `packages/api/tests/test_dotenv_config_parity.py` | `uv run pytest -m "not integration"` |
| E2E test infrastructure (fixtures/helpers) | none | Exercised indirectly via the e2e scenario tests that consume them — matches this repo's own convention for `fakes.py`/`agent_fakes.py`/`shared.testing` (no direct unit tests) | `tests/e2e/{conftest,polling,payload_builders,langfuse_helper}.py` | exercised via `uv run pytest -m e2e` |
| E2E cross-service pipeline scenarios | e2e | Every branch in scope: happy path (auto-approve) + traceability, auto-reject, human-review + both PUT resolutions, retry-ceiling, ghost, stale — 1:1 with spec's `E2E-01..09` | `tests/e2e/test_*.py` | `uv run pytest -m e2e` |
| Documentation | none | — (no test; reviewed for accuracy) | `README.md`, `docs/codebase/{TESTING,INTEGRATIONS}.md` | build gate only |

## Gate Check Commands

> Generated from codebase — confirm before Execute.

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | After Phase 1 (ENV-group) tasks and any task touching only file-parsing tests | `uv run pytest -m "not integration"` |
| Full | Before considering any task in this feature done | `uv run pytest` |
| E2E | After any task adding/modifying `tests/e2e/*` — requires `docker compose up -d` and a real `GROQ_API_KEY` in `.env` | `uv run pytest -m e2e` |
| Lint (ad hoc) | Optional sanity check, not gated | `uv run ruff check <path>` |

---

## Execution Plan

Phases are ordered and run sequentially — each phase completes before the next begins, and tasks within a phase execute in order.

### Phase 1: Dotenv-first config foundation

Must land first — every later phase (the e2e suite) depends on the real compose stack actually booting `reimbursement` with `GROQ_API_KEY` etc.

```
T1 → T2 → T3
```

### Phase 2: E2E test infrastructure

Shared fixtures/helpers the scenario tests (Phase 3) will import — no scenario assertions yet.

```
T4 → T5 → T6 → T7 → T8
```

### Phase 3: E2E scenario tests

The actual cross-service flow proofs, one file per story.

```
T9 → T10 → T11 → T12
```

### Phase 4: Documentation

```
T13 → T14
```

---

## Task Breakdown

### T1: Add `LANGFUSE_SECRET_KEY` to `.env.sample`

**What**: Add a `LANGFUSE_SECRET_KEY=sk-lf-local-dev` line to `.env.sample`, mirroring `LANGFUSE_INIT_PROJECT_SECRET_KEY`'s existing placeholder value, with a one-line comment explaining the two names serve two different consumers (LangFuse's own init bootstrap vs. `reimbursement`'s own dotenv-loaded config).
**Where**: `.env.sample`
**Depends on**: None
**Reuses**: N/A
**Requirement**: ENV-03

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `.env.sample` defines `LANGFUSE_SECRET_KEY=sk-lf-local-dev`
- [ ] `LANGFUSE_INIT_PROJECT_SECRET_KEY` still present, unchanged
- [ ] `uv run pytest -m "not integration"` gate passes (no regression)

**Tests**: none (config file; covered by T3's parity test once it lands)
**Gate**: quick

---

### T2: Make `docker-compose.yml` dotenv-first for the four uv-workspace services

**What**: For `api`, `publisher`, `reimbursement`, and `migrate`: add a read-only runtime volume mount (`./.env:/app/.env:ro`) and prune each service's `environment:` block to its topology-only allowlist (`api`: `KAFKA_BOOTSTRAP_SERVERS`, `DATABASE_URL`; `publisher`: same two; `reimbursement`: those two plus `LANGFUSE_HOST`; `migrate`: `DATABASE_URL` only) — removing `GROQ_API_KEY`, all model-name/temperature/timeout vars, the 5 Kafka-security vars (wherever present), and the `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` entries (the public key stays only as the existing `x-langfuse-public-key` anchor used elsewhere in the file, not under `reimbursement`'s own block).
**Where**: `docker-compose.yml`
**Depends on**: T1
**Reuses**: N/A
**Requirement**: ENV-01, ENV-02

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] All 4 services have the `.env` bind mount
- [ ] Each of the 3 app services' `environment:` block contains only its documented topology allowlist (verify by reading the diff, not just running compose)
- [ ] `cp .env.sample .env && docker compose up -d reimbursement && docker compose ps` shows it `Up` and staying up for ≥10s (manual verification, logged in the commit message or PR description)
- [ ] `docker build` (no compose) of each service's image, inspected, contains no `.env` file — the mount is compose-only
- [ ] `uv run pytest -m "not integration"` gate passes (no regression)

**Tests**: none (infra config; covered by T3's automated parity test)
**Gate**: quick + manual compose boot check

---

### T3: Add the dotenv-config-parity regression guard test

**What**: Create `packages/api/tests/test_dotenv_config_parity.py`, parsing `docker-compose.yml` once at import time (mirroring `test_compose_parity.py`'s pattern) and asserting: (a) each of `api`/`publisher`/`reimbursement`'s `environment:` block contains **only** its maintained topology allowlist; (b) `.env.sample` defines every var in a maintained required/optional list per service (`GROQ_API_KEY`, `EXTRACT_FIELDS_MODEL_NAME`, `ANALYSIS_MODEL_NAME`, `AI_TIMEOUT_SECONDS`, `EXTRACT_FIELDS_TEMPERATURE`, `ANALYSIS_TEMPERATURE`, `LANGFUSE_SECRET_KEY`, and the 5 Kafka-security names).
**Where**: `packages/api/tests/test_dotenv_config_parity.py`
**Depends on**: T2
**Reuses**: `packages/api/tests/test_compose_parity.py`'s YAML-parse-once-at-import pattern
**Requirement**: ENV-04

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] Test asserts each app service's `environment:` block has no unexpected non-topology key
- [ ] Test asserts `.env.sample` defines every var in the maintained list
- [ ] Deliberately reintroducing `GROQ_API_KEY: ${GROQ_API_KEY}` into `reimbursement`'s compose block locally makes the test fail, naming it — then revert and confirm green again
- [ ] `uv run pytest -m "not integration"` gate passes, collecting this new test, no Docker daemon required

**Tests**: unit (per Test Coverage Matrix)
**Gate**: quick

**Commit**: `feat(config): make .env the runtime source of truth inside containers`

---

### T4: Register the `e2e` pytest marker and gate exclusion

**What**: In root `pyproject.toml`'s `[tool.pytest.ini_options]`: add `"tests/e2e"` to `testpaths`; add `"e2e: requires a live docker-compose stack and a real GROQ_API_KEY"` to `markers`; append `-m "not e2e"` to `addopts` (so plain `uv run pytest` and `-m "not integration"` never collect `e2e`-marked tests, while `uv run pytest -m e2e` on the command line overrides the default exclusion).
**Where**: `pyproject.toml`
**Depends on**: None
**Reuses**: The existing `integration` marker's registration as a template
**Requirement**: E2E-10

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `markers` includes the new `e2e` entry
- [ ] `addopts` includes `-m "not e2e"` alongside the existing `--import-mode=importlib`
- [ ] `testpaths` includes `"tests/e2e"`
- [ ] `uv run pytest --collect-only -m e2e` (with a placeholder `tests/e2e/test_placeholder.py::it_placeholder` if T5 hasn't landed yet, else the real suite) confirms the marker/exclusion mechanics work as designed
- [ ] `uv run pytest -m "not integration"` gate passes with the same collected-test count as before (no e2e tests leak into the default run)

**Tests**: none (config)
**Gate**: quick

---

### T5: `tests/e2e/conftest.py` — env, stack-readiness, and client fixtures

**What**: Create `tests/e2e/conftest.py` with: `e2e_env()` (session-scoped) calling `load_dotenv()` against the repo-root `.env` and returning a frozen dataclass (`api_base_url="http://localhost:8000"`, `kafka_bootstrap="localhost:9092"`, `langfuse_public_key`/`secret_key`/`host="http://localhost:3000"`), failing immediately with a clear message if `GROQ_API_KEY` is unset; `stack_ready(e2e_env)` (session-scoped, autouse) polling `api`'s `/health`, a Kafka broker-metadata request, and LangFuse reachability, each within a bounded timeout, `pytest.fail`-ing with a named diagnostic if any dependency never becomes ready (Approach 1 — no `docker compose up`/`down` calls); `api_client(e2e_env)` yielding an `httpx.Client`; `kafka_producer_config(e2e_env)` returning `dataclasses.replace(load_config().kafka, bootstrap_servers=e2e_env.kafka_bootstrap)`.
**Where**: `tests/e2e/conftest.py`
**Depends on**: T4
**Reuses**: `shared.config.load_config`/`KafkaConfig`
**Requirement**: (infrastructure for E2E-01..09)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] All 4 fixtures defined and importable
- [ ] `e2e_env` fails fast (clear message) when `GROQ_API_KEY` is unset — verified by unsetting it locally and running one placeholder e2e test
- [ ] `stack_ready` fails with a named diagnostic when a dependency is down — verified by stopping `kafka` locally and confirming the named failure, then restarting it
- [ ] `uv run pytest -m "not integration"` gate passes (fixtures don't leak into the default collection)

**Tests**: none (test infrastructure; exercised indirectly by Phase 3)
**Gate**: quick (structural); manual verification against the real stack per the two bullets above

---

### T6: `tests/e2e/polling.py` — `wait_for_status` helper

**What**: Create `tests/e2e/polling.py` with `wait_for_status(api_client: httpx.Client, uuid: UUID, expected_terminal_statuses: set[str], timeout: float) -> dict` — polls `GET /api/v1/reimbursement/:uuid` until the row's `status` is in the terminal set or the timeout elapses, raising `AssertionError` naming the last-seen status on timeout, mirroring `_run_agent`'s bounded-deadline style from `reimbursement/tests/test_integration.py` adapted to poll HTTP instead of a Kafka offset.
**Where**: `tests/e2e/polling.py`
**Depends on**: T4
**Reuses**: `_run_agent`'s bounded-poll-with-deadline style (`packages/reimbursement/tests/test_integration.py`) as a structural template, not imported code
**Requirement**: (infrastructure for E2E-01, E2E-03, E2E-04, E2E-05, E2E-06, E2E-07, E2E-09)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `wait_for_status` implemented with a bounded deadline, not an unbounded loop
- [ ] Timeout path raises `AssertionError` naming the last-seen status (verified with a unit-level fake `httpx.Client` returning a non-terminal status forever, with a short timeout)
- [ ] `uv run pytest -m "not integration"` gate passes

**Tests**: none (test infrastructure) — but its own timeout-behavior is validated once, inline, per the bullet above (not a separate persisted test file, since the helper's only real consumer is Phase 3's e2e suite)
**Gate**: quick

---

### T7: `tests/e2e/payload_builders.py` — per-bucket payload fixtures

**What**: Create `tests/e2e/payload_builders.py` with one builder function per target bucket, layered on `shared.testing.valid_reimbursement_item`: `approve_bucket_payload(request_id)` (fresh, low-value, unambiguous `raw_ocr_text`), `reject_bucket_payload(request_id)` (receipt date >90 days before `submitted_at`), `human_review_bucket_payload(request_id)` (200–2000 BRL range with a deliberate claimed-amount/OCR inconsistency for the guardrail to catch).
**Where**: `tests/e2e/payload_builders.py`
**Depends on**: T4
**Reuses**: `shared.testing.valid_reimbursement_item`
**Requirement**: (infrastructure for E2E-01, E2E-03, E2E-04, E2E-05, E2E-06)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] All 3 builder functions implemented, each returning a payload dict `api_client` can POST directly
- [ ] Values are maximally unambiguous (round numbers, explicit far-past/near dates) per the design's live-model-steering mitigation
- [ ] `uv run pytest -m "not integration"` gate passes

**Tests**: none (test infrastructure; correctness proven by Phase 3's tests actually landing in the intended bucket against real Groq)
**Gate**: quick

---

### T8: `tests/e2e/langfuse_helper.py` — trace-by-session-id lookup

**What**: Create `tests/e2e/langfuse_helper.py` with `trace_exists_for_session(client, session_id: str, timeout: float) -> bool`, polling the LangFuse client's trace-list-by-`session_id` call until a result appears or the timeout elapses. **First confirm the exact accessor chain off the top-level `Langfuse(...)` client against the version pinned in `packages/reimbursement/pyproject.toml`** (Context7-verified to exist as `trace.list(session_id=...)` on the SDK's low-level API client during Design; confirm the precise attribute path, e.g. `Langfuse(...).api.trace.list(...)`, for that exact version before writing the real call).
**Where**: `tests/e2e/langfuse_helper.py`
**Depends on**: T4
**Reuses**: N/A (new integration point)
**Requirement**: (infrastructure for E2E-02)

**Tools**:
- MCP: `context7` (confirm the exact SDK accessor for the pinned `langfuse` version before writing the call)
- Skill: NONE

**Done when**:
- [ ] Accessor chain confirmed against the pinned version (cite the Context7 query result or SDK source line in the commit message)
- [ ] `trace_exists_for_session` implemented with a bounded polling loop
- [ ] Manually verified once against the real running LangFuse stack with a trace produced by any existing `reimbursement` decision (e.g. from a prior manual smoke test), confirming a `True` result
- [ ] `uv run pytest -m "not integration"` gate passes

**Tests**: none (test infrastructure; exercised by T9)
**Gate**: quick (structural); manual verification against the real stack per the bullet above

---

### T9: `tests/e2e/test_happy_path.py` — auto-approve + traceability

**What**: Implement the happy-path e2e test: POST via `approve_bucket_payload`, `wait_for_status` for `auto-approved`, assert `decision_reason` non-null, then assert `trace_exists_for_session` for the returned `uuid`. Marked `@pytest.mark.e2e`.
**Where**: `tests/e2e/test_happy_path.py`
**Depends on**: T5, T6, T7, T8
**Reuses**: `tests/e2e/{conftest,polling,payload_builders,langfuse_helper}.py`
**Requirement**: E2E-01, E2E-02

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `uv run pytest -m e2e -k auto_approved` passes against the real stack with a real `GROQ_API_KEY`
- [ ] Unsetting `GROQ_API_KEY` and rerunning shows the fail-fast behavior from `e2e_env` (per T5), not a hang
- [ ] `uv run pytest -m "not integration"` gate still passes (this file is excluded from that run)

**Tests**: e2e (per Test Coverage Matrix)
**Gate**: e2e

---

### T10: `tests/e2e/test_auto_reject.py`

**What**: Implement the auto-reject e2e test: POST via `reject_bucket_payload`, `wait_for_status` for `auto-rejected`, assert `decision_reason` non-null. Marked `@pytest.mark.e2e`.
**Where**: `tests/e2e/test_auto_reject.py`
**Depends on**: T5, T6, T7
**Reuses**: `tests/e2e/{conftest,polling,payload_builders}.py`
**Requirement**: E2E-03

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `uv run pytest -m e2e -k auto_rejected` passes against the real stack
- [ ] `uv run pytest -m "not integration"` gate still passes (excluded from that run)

**Tests**: e2e
**Gate**: e2e

---

### T11: `tests/e2e/test_human_review.py` — landing + both PUT resolutions

**What**: Implement three e2e tests: (1) POST via `human_review_bucket_payload`, `wait_for_status` for `human-review`; (2) on a fresh item driven the same way, `PUT` an approval payload and assert the row becomes `human-approved`; (3) on a second fresh item, `PUT` a rejection payload and assert `human-rejected`. Marked `@pytest.mark.e2e`.
**Where**: `tests/e2e/test_human_review.py`
**Depends on**: T5, T6, T7
**Reuses**: `tests/e2e/{conftest,polling,payload_builders}.py`
**Requirement**: E2E-04, E2E-05, E2E-06

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `uv run pytest -m e2e -k human_review` passes against the real stack, covering all 3 cases
- [ ] `uv run pytest -m "not integration"` gate still passes (excluded from that run)

**Tests**: e2e
**Gate**: e2e

---

### T12: `tests/e2e/test_retry_ghost_stale.py`

**What**: Implement three e2e tests, each producing a hand-crafted `Reimbursement` envelope directly to Kafka (bypassing the publisher) against a row created via a real `POST`: (1) `retry=4` → `wait_for_status` for `human-review` with a non-null `decision_reason`, and confirm via test structure that no Groq call was required for this branch (the retry-ceiling short-circuit, matching the existing single-hop test's own approach — assert on timing/behavior, not by mocking Groq, since this suite makes no fakes); (2) a `uuid` with no matching row → confirm the container stays healthy and a subsequent test in the file still passes (proxy for "no crash"); (3) a `published_at` older than the row's `updated_at` → confirm the row stays `pending` with a null `decision_reason`. Marked `@pytest.mark.e2e`.
**Where**: `tests/e2e/test_retry_ghost_stale.py`
**Depends on**: T5, T6
**Reuses**: `tests/e2e/{conftest,polling}.py`, `shared.producer.managed_producer`/`publish`, `shared.models.ReimbursementEnvelope`
**Requirement**: E2E-07, E2E-08, E2E-09

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `uv run pytest -m e2e -k "retry_ceiling or ghost or stale"` passes against the real stack, covering all 3 cases
- [ ] `uv run pytest -m "not integration"` gate still passes (excluded from that run)

**Tests**: e2e
**Gate**: e2e

**Commit**: `test(e2e): add real-stack pipeline flow suite (auto-approve, auto-reject, human-review, retry/ghost/stale)`

---

### T13: Update `README.md`

**What**: Document the dotenv-first config model (`.env` mounted into every uv-workspace container; `docker-compose.yml`'s `environment:` blocks now topology-only) superseding the current "compose duplicates `.env` values" wording, and document the `e2e` marker (prerequisites: `docker compose up -d`, a real `GROQ_API_KEY` in `.env`; command: `uv run pytest -m e2e`).
**Where**: `README.md`
**Depends on**: T3, T12
**Reuses**: N/A
**Requirement**: E2E-11

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] The existing "Every service loads `.env` through python-dotenv..." paragraph is corrected to describe the mount, not passive host-only loading
- [ ] A new section/paragraph documents the `e2e` marker, its prerequisites, and its command
- [ ] Read through once for accuracy against the actual shipped `docker-compose.yml`/`pyproject.toml`

**Tests**: none (docs)
**Gate**: build (read-through only)

---

### T14: Update `docs/codebase/TESTING.md` and `docs/codebase/INTEGRATIONS.md`

**What**: Add an `e2e` row to `TESTING.md`'s Test Execution / Gate Check Commands tables and its Test Coverage Matrix (mirroring this feature's own matrix above); note the dotenv-mount convention in `INTEGRATIONS.md` wherever it currently describes how `api`/`publisher`/`reimbursement` receive their config.
**Where**: `docs/codebase/TESTING.md`, `docs/codebase/INTEGRATIONS.md`
**Depends on**: T3, T12
**Reuses**: N/A
**Requirement**: E2E-11

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `TESTING.md`'s tables include the new `e2e` marker/gate row and the `test_dotenv_config_parity.py` coverage row
- [ ] `INTEGRATIONS.md` reflects the `.env`-mount mechanism, not the old compose-passthrough description
- [ ] Read through once for accuracy

**Tests**: none (docs)
**Gate**: build (read-through only)

**Commit**: `docs: document dotenv-first config model and the e2e test suite`

---

## Phase Execution Map

```
Phase 1 → Phase 2 → Phase 3 → Phase 4

Phase 1:  T1 ──→ T2 ──→ T3
Phase 2:  T4 ──→ T5 ──→ T6 ──→ T7 ──→ T8
Phase 3:  T9 ──→ T10 ──→ T11 ──→ T12
Phase 4:  T13 ──→ T14
```

Execution is strictly sequential — there is no intra-phase parallelism. A single agent (or batch worker) works one task at a time, in order.

**Batch packing (14 tasks total, > ~8 → sub-agent offer applies):** Phase 1 (3) + Phase 2 (5) = 8 tasks → batch 1. Phase 3 (4) + Phase 4 (2) = 6 tasks → batch 2. Both batches stay at or under the ~7-task budget's neighborhood, cut exactly on phase boundaries.

---

## Task Granularity Check

| Task | Scope | Status |
| ---- | ----- | ------ |
| T1: Add `LANGFUSE_SECRET_KEY` to `.env.sample` | 1 file, 1 line | ✅ Granular |
| T2: Make `docker-compose.yml` dotenv-first | 1 file, 1 cohesive concept (mount + prune, 4 services) | ✅ Granular (2-3 related changes, same file, same concept — OK per cohesion rule) |
| T3: Add dotenv-config-parity test | 1 file | ✅ Granular |
| T4: Register `e2e` marker | 1 file | ✅ Granular |
| T5: `conftest.py` fixtures | 1 file, 1 cohesive fixture set | ✅ Granular |
| T6: `polling.py` helper | 1 file, 1 function | ✅ Granular |
| T7: `payload_builders.py` | 1 file, 3 cohesive builder functions (one concept: bucket payloads) | ✅ Granular |
| T8: `langfuse_helper.py` | 1 file, 1 function | ✅ Granular |
| T9: `test_happy_path.py` | 1 file, 1 story (E2E-01/02) | ✅ Granular |
| T10: `test_auto_reject.py` | 1 file, 1 story (E2E-03) | ✅ Granular |
| T11: `test_human_review.py` | 1 file, 1 story (E2E-04/05/06) | ✅ Granular |
| T12: `test_retry_ghost_stale.py` | 1 file, 1 story (E2E-07/08/09) | ✅ Granular |
| T13: `README.md` | 1 file | ✅ Granular |
| T14: `TESTING.md` + `INTEGRATIONS.md` | 2 files, 1 cohesive concept (docs/codebase context sync) | ✅ Granular (cohesive pair, same requirement) |

---

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| ---- | ----------------------- | -------------- | ------ |
| T1 | None | (start of Phase 1) | ✅ Match |
| T2 | T1 | T1 → T2 | ✅ Match |
| T3 | T2 | T2 → T3 | ✅ Match |
| T4 | None | (start of Phase 2) | ✅ Match |
| T5 | T4 | T4 → T5 | ✅ Match |
| T6 | T4 | T4 → T5 → T6 (chained visually; T6's real dependency is T4 only) | ✅ Match — diagram shows a valid superset ordering, no arrow contradicts a stated dependency |
| T7 | T4 | same as T6 | ✅ Match |
| T8 | T4 | same as T6 | ✅ Match |
| T9 | T5, T6, T7, T8 | Phase 2 fully precedes Phase 3; T9 first in Phase 3 | ✅ Match |
| T10 | T5, T6, T7 | Phase 2 fully precedes Phase 3 | ✅ Match |
| T11 | T5, T6, T7 | Phase 2 fully precedes Phase 3 | ✅ Match |
| T12 | T5, T6 | Phase 2 fully precedes Phase 3 | ✅ Match |
| T13 | T3, T12 | Phase 1 + Phase 3 fully precede Phase 4 | ✅ Match |
| T14 | T3, T12 | Phase 1 + Phase 3 fully precede Phase 4 | ✅ Match |

No task depends on a task in a later phase. Every arrow in the phase diagrams corresponds to a real dependency chain (Phase 2's internal T4→T5→T6→T7→T8 chain is a valid sequential-execution ordering even though T6/T7/T8's *only real* dependency is T4 — sequential execution means they'd run in this order regardless).

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| ---- | ---------------------------- | ---------------- | ---------- | ------ |
| T1 | Config (`.env.sample`) | none | none | ✅ OK |
| T2 | Config (`docker-compose.yml`) | none | none | ✅ OK |
| T3 | Docker-compose/env config parity | unit | unit | ✅ OK |
| T4 | Config (`pyproject.toml`) | none | none | ✅ OK |
| T5 | E2E test infrastructure | none | none | ✅ OK |
| T6 | E2E test infrastructure | none | none | ✅ OK |
| T7 | E2E test infrastructure | none | none | ✅ OK |
| T8 | E2E test infrastructure | none | none | ✅ OK |
| T9 | E2E cross-service pipeline scenario | e2e | e2e | ✅ OK |
| T10 | E2E cross-service pipeline scenario | e2e | e2e | ✅ OK |
| T11 | E2E cross-service pipeline scenario | e2e | e2e | ✅ OK |
| T12 | E2E cross-service pipeline scenario | e2e | e2e | ✅ OK |
| T13 | Documentation | none | none | ✅ OK |
| T14 | Documentation | none | none | ✅ OK |

No violations — every task's `Tests` field matches the Test Coverage Matrix's requirement for the layer it touches.

---

## Sub-Agent Delegation

14 tasks total — exceeds the ~8-task inline-execution threshold. Per the skill's offer-then-confirm rule, this will be offered as a 2-batch sub-agent delegation (batch 1 = Phase 1 + Phase 2 = 8 tasks; batch 2 = Phase 3 + Phase 4 = 6 tasks) at the start of Execute — not auto-spawned.

## Tools Note

Every task defaults to `MCP: NONE, Skill: NONE` except **T8**, which uses Context7 once to confirm the exact LangFuse SDK accessor before writing the real call (per the Risks & Concerns entry in `design.md`). Flagged here instead of a separate question round, consistent with this session's pace — override any task's tools before Execute if a different choice is wanted.
