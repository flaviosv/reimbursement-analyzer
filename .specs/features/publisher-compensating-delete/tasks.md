# Publisher Compensating-Delete Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user — do not proceed without it.**

---

**Design**: `.specs/features/publisher-compensating-delete/design.md`
**Status**: Draft (direct transcription of AD-033, already confirmed in `.specs/STATE.md` — treated as user-confirmed for Tasks/Execute)

---

## Test Coverage Matrix

> Generated from `docs/codebase/TESTING.md` (existing, documented project guidelines — cited directly, no strong-default fallback needed) plus this feature's spec ACs. Guidelines found: `docs/codebase/TESTING.md`, `docs/codebase/CONVENTIONS.md`.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| --- | --- | --- | --- | --- |
| `delete_pending` repository primitive | Integration (real Postgres) | Every branch: removes exactly one row when uuid+status='pending' match, returns `False` for an unmatched uuid, returns `False` and leaves the row untouched when its status isn't `pending` — 1:1 to PCD-03 | `packages/shared/tests/reimbursement/test_repository.py` | `uv run pytest` |
| `publish_pending` use case (insert commits, publish, compensate) | Unit + integration (real Postgres) | All branches: publish succeeds (row stands, unchanged) + publish fails → delete removes the row (`compensating_delete` logged with uuid/request_id/retry/error type) + delete affects zero rows (`compensating_delete_noop` logged, still raises) + delete itself raises (`failure_log` record with uuid/item/both errors, still raises) — 1:1 to PCD-01, PCD-02, PCD-03, PCD-04, PCD-05, PCD-06 | `packages/shared/tests/reimbursement/use_cases/test_publish_pending.py` | mixed — none of these are `@pytest.mark.integration` (Postgres-only, no Kafka container), so all run under `uv run pytest -m "not integration"` |
| Publisher decision tree — `_insert_and_publish` (drops the transaction wrapper, threads `failure_log_config`/`retry` through) | Unit (fakes) + Postgres-backed (`RealPool`) | Existing PUB-* coverage continues passing: insert-then-publish commit unchanged, publish-failure leaves no row behind (retargeted from rollback to deletion, same observable outcome), requeue/duplicate/db-insert-failure paths unchanged — 1:1 to PCD-01, PCD-02, PCD-07, PCD-08 | `packages/publisher/tests/test_processing.py` | `uv run pytest -m "not integration"` |
| `docs/SCOPE.md` — Reimbursement Publisher / Error Handling amendment | none (documentation) | Inline dated note replacing the "single transaction" / "rollback the DB transaction" language, matching the AD-020/AD-027/AD-028 precedent — 1:1 to PCD-09 | `docs/SCOPE.md:241-252` | manual diff review — no automated gate applies to prose |
| `docs/RISKS.md` — R-001 amendment | none (documentation) | The two closed consequences (ghost message, duplicate-via-commit-failure) and the new narrower residual risk (orphaned row) recorded in explicitly distinct paragraphs, not merged — 1:1 to PCD-10 | `docs/RISKS.md` (R-001 section) | manual diff review — no automated gate applies to prose |

**Coverage Expectation values** — set from `docs/codebase/TESTING.md`'s own documented conventions (`Describe*`/`it_*`, real-Postgres-via-`db`-fixture for repository/use-case layers, fakes-plus-`RealPool` for the publisher decision tree), not the skill's strong defaults.

**Note on `.specs/RISKS.md` vs `docs/RISKS.md`:** `spec.md`/`design.md`/`STATE.md`'s AD-033 entry all say `.specs/RISKS.md`, but the file actually lives at `docs/RISKS.md` (confirmed by search — no `.specs/RISKS.md` exists anywhere in the repo). Tasks below edit the real path, `docs/RISKS.md`.

## Gate Check Commands

> Sourced from `docs/codebase/TESTING.md`'s own Gate Check Commands table, scoped to the two packages this feature touches for per-task gates (T1-T3); the repo-wide commands are reserved for final feature-level verification.

| Gate Level | When to Use | Command |
| --- | --- | --- |
| Quick (feature-scoped) | After T1 and T3 (unit + Postgres-backed, no Kafka container) | `uv run pytest packages/shared packages/publisher -m "not integration"` |
| Quick (shared-only) | After T2 specifically — `publish_pending`'s signature changes here, but its one caller (`publisher.processing`) isn't updated until T3, so `packages/publisher` is expected to transiently fail in between (see T2's Done-when note) | `uv run pytest packages/shared -m "not integration"` |
| Full (feature-scoped) | Before considering the feature done | `uv run pytest packages/shared packages/publisher` |
| Full (repo-wide, final verification only) | Once, after the last code task, per `docs/codebase/TESTING.md` | `uv run pytest -m "not integration"` then `uv run pytest` — **note:** `packages/reimbursement/tests` has a pre-existing, unrelated collection failure (`PLACEHOLDER_PROMPT` import error, documented in `docs/codebase/CONCERNS.md`'s Known Bugs) already present on `main` before this feature; it is orthogonal to the publisher's insert+publish path and out of scope to fix here per Minimal Impact — verified present on `main` pre-change (428 passed outside `packages/reimbursement`, 4 pre-existing collection errors inside it) |
| Docs | T4, T5 | No automated gate — diff reviewed for wording accuracy against AC9/AC10 |

---

## Execution Plan

Phases are ordered and run sequentially — each phase completes before the next begins, and tasks within a phase execute in order.

### Phase 1: Repository primitive

```
T1
```

### Phase 2: Use-case restructuring

```
T2
```

### Phase 3: Caller wiring

```
T3
```

### Phase 4: Spec documentation catch-up

```
T4 → T5
```

---

## Task Breakdown

### T1: `delete_pending` — compensating-delete repository primitive

**What**: Add `delete_pending(conn: asyncpg.Connection, uuid: UUID) -> bool` to `shared.reimbursement.repository`, issuing `DELETE FROM reimbursement WHERE uuid = $1 AND status = 'pending'` and returning whether exactly one row was removed (parsed from asyncpg's `DELETE n` status string), following `update_decision`'s exact `UPDATE n`-parsing pattern.
**Where**: `packages/shared/src/shared/reimbursement/repository.py`
**Depends on**: None
**Reuses**: The `DELETE n` / `UPDATE n` status-string parsing pattern from `update_decision` (`repository.py:182-185`)
**Requirement**: PCD-03

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `delete_pending(conn, uuid)` issues `DELETE FROM reimbursement WHERE uuid = $1 AND status = 'pending'` and returns `True` iff exactly one row was removed
- [ ] `DescribeDeletePending` in `test_repository.py`: removes-one-row case, unmatched-uuid case, and status-not-`pending` case (row left untouched) — matching `DescribeUpdateDecision`'s existing shape
- [ ] Gate check passes: `uv run pytest packages/shared packages/publisher -m "not integration"`
- [ ] Test count recorded (no silent deletions vs. pre-task count)

**Tests**: integration
**Gate**: quick

**Commit**: `feat(shared): add delete_pending compensating-delete repository primitive`

---

### T2: `publish_pending` — drop the transaction, compensate on publish failure

**What**: Restructure `shared.reimbursement.use_cases.publish_pending.publish_pending` to run `insert_pending` uninstrumented by any transaction, attempt the publish, and on `PublishFailed` call `delete_pending` and durably log every outcome (removed → `reimbursement.compensating_delete` info log; zero rows → `reimbursement.compensating_delete_noop` info log; delete itself raises → `reimbursement.compensating_delete_failed` via `failure_log.write`), then always re-raise the original `PublishFailed`. Adds two new required parameters: `failure_log_config: FailureLogConfig`, `retry: int`.
**Where**: `packages/shared/src/shared/reimbursement/use_cases/publish_pending.py`
**Depends on**: T1
**Reuses**: `shared.failure_log.write` (last-resort sink, same call shape as every other site); `shared.errors.PublishFailed`; the `reimbursement.<snake_case>` event-naming convention (`DUPLICATE_DROPPED_EVENT`-style constants); `item.get("request_id")` (mirrors `publisher.processing._request_id`'s null-safe read, without importing across the shared→publisher boundary)
**Requirement**: PCD-01, PCD-02, PCD-03, PCD-04, PCD-05, PCD-06

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `publish_pending`'s signature is `(conn, producer, item, errors, publish_timeout_seconds, failure_log_config, retry) -> None`, no `async with conn.transaction():` anywhere in its body
- [ ] Publish success path unchanged: row stands, `insert_pending`'s uuid is what gets published
- [ ] Publish failure → `delete_pending` removes the row → `reimbursement.compensating_delete` logged at INFO carrying `uuid`, `request_id`, `retry`, and the publish error's type (`type(exc).__name__`, matching `AttemptError.from_exception`'s existing `error_type` convention) → `PublishFailed` still raised
- [ ] Publish failure → `delete_pending` affects zero rows → `reimbursement.compensating_delete_noop` logged at INFO (same fields) → `PublishFailed` still raised, not swallowed
- [ ] Publish failure → `delete_pending` itself raises → `failure_log.write` carries `uuid`, `item`, `retry`, the publish error's type/message, and the delete error's type/message → `PublishFailed` still raised (not the delete's own exception), and nothing raises out of the logging path itself
- [ ] `test_publish_pending.py`'s existing 3 tests updated for the two new required params (`failure_log_config=load_config().failure_log`, following `test_send_human_review.py`'s existing `load_config()` pattern)
- [ ] New `DescribeTheCompensatingDelete` class: delete-removes-the-row + its traceability log (real Postgres via `db` fixture, `FakeProducer(errors={...})` to force `PublishFailed`), delete-affects-zero-rows (via `monkeypatch.setattr("shared.reimbursement.use_cases.publish_pending.delete_pending", ...)`, matching the repo's existing `monkeypatch.setattr(module, "name", stub)` convention — no `unittest.mock`, none used anywhere in this repo), delete-itself-raises (same monkeypatch technique, asserting the `failure_log` record and that `PublishFailed` — not the delete exception — is what propagates)
- [ ] Gate check passes: `uv run pytest packages/shared -m "not integration"` — **scoped to `packages/shared` only, not the combined command**: this task changes `publish_pending`'s signature, and its one production caller (`publisher.processing._insert_and_publish`) is not updated until T3, so `packages/publisher`'s suite is expected to fail on a `TypeError` (missing args) between this commit and T3's — a normal transient state for a signature change split across two atomic commits, not a regression. The combined `packages/shared packages/publisher` gate is deferred to T3, where it must pass clean.
- [ ] Test count recorded (no silent deletions vs. pre-task count)

**Tests**: unit + integration
**Gate**: quick

**Commit**: `feat(shared): publish_pending drops its transaction, compensates a publish failure with a traceable delete`

---

### T3: `_insert_and_publish` — drop the transaction wrapper, thread the new params through

**What**: Remove `_insert_and_publish`'s `async with conn.transaction():` wrapper and its now-stale "rolls back the insert" comment; pass `deps.config.failure_log` and `envelope.retry` into `publish_pending`. Rewrite `process_item`'s stale comment about "a COMMIT that fails after a successful publish" (that scenario cannot occur anymore — there is no commit step after publish to fail). Retarget the existing rollback-flavored test (still valid behaviorally — "no row survives a publish failure" — only the mechanism changed) and add `FakeConnection.execute()` so `FakePool`-based publish-failure tests (which now transitively exercise `delete_pending`) don't crash.
**Where**: `packages/publisher/src/publisher/processing.py`, `packages/publisher/tests/fakes.py`, `packages/publisher/tests/test_processing.py`
**Depends on**: T2
**Reuses**: `deps.pool.acquire(...)` (unchanged), `update_decision`'s `"UPDATE n"`-string convention mirrored by `FakeConnection.execute`'s `"DELETE 1"` stub
**Requirement**: PCD-01, PCD-02, PCD-07, PCD-08

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `_insert_and_publish` no longer opens `conn.transaction()`; calls `publish_pending(conn, deps.producer, item, envelope.errors, deps.config.kafka.publish_timeout_seconds, deps.config.failure_log, envelope.retry)`
- [ ] `process_item`'s `except Exception` branch comment rewritten to describe its actual scope post-change (catches only the insert's own failure — `is_duplicate` or a generic `db-insert` requeue — since a `PublishFailed` is now always caught by the `except PublishFailed` branch above it and no commit step can fail after a successful publish anymore)
- [ ] `FakeConnection.execute()` added to `fakes.py`, returning `"DELETE 1"` (default success stub — no existing test asserts on delete outcomes at this layer, only that the decision tree doesn't crash when the compensating delete fires)
- [ ] `DescribeTheItemTransaction` renamed to `DescribeInsertThenPublish` (no longer describes a transaction); `it_leaves_no_row_behind_when_the_publish_fails` gets a short comment noting the row is now gone via an explicit compensating delete, not a rollback — assertion itself (`row count == 0`) is unchanged, since the observable outcome (PUB-09) still holds
- [ ] Full existing `test_processing.py` suite (813-line file, every `ItemOutcome` branch) still passes unmodified in assertions beyond the rename/comment above — no regression in requeue, duplicate, escalation, or db-insert-failure coverage
- [ ] `grep -n "conn.transaction()" packages/publisher/src/publisher/processing.py` returns no match inside `_insert_and_publish` (structural confirmation of PCD-01, verified by inspection rather than a dedicated runtime test — matches design.md's framing of this as a transaction-boundary/structural requirement)
- [ ] Gate check passes: `uv run pytest packages/shared packages/publisher -m "not integration"`
- [ ] Test count recorded (no silent deletions vs. pre-task count)

**Tests**: unit + integration
**Gate**: quick

**Commit**: `refactor(publisher): insert_and_publish drops its transaction wrapper (AD-033)`

---

### T4: `docs/SCOPE.md` — amend the Reimbursement Publisher / Error Handling section in place

**What**: Add two inline, dated `(amended — ...)` notes (AD-020/AD-027/AD-028 precedent) beside `docs/SCOPE.md:241` ("single transaction") and `:252` ("rollback the DB transaction"), describing the insert-commits-immediately + compensating-delete design and pointing to AD-033 in `.specs/STATE.md`. Original bullets are not deleted or rewritten, only annotated.
**Where**: `docs/SCOPE.md` (lines 241, 252)
**Depends on**: T3
**Reuses**: The exact `(amended — ... — see AD-NNN in .specs/STATE.md)` inline-note style already used at `docs/SCOPE.md`'s AD-020/AD-027/AD-028/AD-030 sites
**Requirement**: PCD-09

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] Line 241's "in a single transaction" bullet carries an inline amended note: no longer a single transaction — insert commits immediately, publish attempted after, a publish failure compensated by an explicit delete rather than a rollback — cites AD-033
- [ ] Line 252's "rollback the DB transaction" bullet carries an inline amended note: no DB transaction to roll back — a publish failure triggers a compensating `DELETE` gated `WHERE uuid = $1 AND status = 'pending'`, durably logged on every outcome — cites AD-033
- [ ] Neither original bullet's text is deleted or rewritten — amendment only, matching precedent
- [ ] Gate: N/A (prose-only) — diff manually reviewed against AC9's wording

**Tests**: none
**Gate**: docs

**Commit**: `docs(scope): amend Reimbursement Publisher error handling for AD-033`

---

### T5: `docs/RISKS.md` — amend R-001 with the closed/opened consequences

**What**: Add a distinct new subsection to R-001 recording that AD-033 closes the ghost-message and duplicate-via-commit-failure consequences outright (insert now commits before the publish is even attempted), and separately — not merged into the same paragraph — that a narrower residual risk is accepted in their place: an un-recoverable orphaned `pending` row, possible only if the process crashes between a publish failure and the compensating delete completing.
**Where**: `docs/RISKS.md` (R-001 section)
**Depends on**: T3
**Reuses**: R-001's existing subsection structure (`### What breaks`, `### Why it cannot be ordered away`, etc.) — new subsection follows the same heading style
**Requirement**: PCD-10

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] New subsection (e.g. `### AD-033 amendment (2026-08-09)`) states, in its own paragraph, which two consequences are closed and why (insert-before-publish removes the window both depended on)
- [ ] A second, explicitly separate paragraph in the same subsection states the narrower residual risk this design opens (orphaned row, crash-between-delete-dispatch-and-completion only) and that it is not auto-healed
- [ ] R-001's `**Status:**` line updated to reflect the partial closure (e.g. "two consequences closed by AD-033; a narrower residual risk accepted in their place") without altering the rest of the existing content
- [ ] Gate: N/A (prose-only) — diff manually reviewed against AC10's wording (closed vs. opened distinguished explicitly, not merged)

**Tests**: none
**Gate**: docs

**Commit**: `docs(risks): record AD-033's closed and residual R-001 consequences`

---

## Phase Execution Map

```
Phase 1 → Phase 2 → Phase 3 → Phase 4

Phase 1:  T1
Phase 2:  T2
Phase 3:  T3
Phase 4:  T4 ──→ T5
```

Execution is strictly sequential — 5 tasks total fits a single batch (≤ ~8), so this runs inline with no sub-agent dispatch.

---

## Task Granularity Check

| Task | Scope | Status |
| --- | --- | --- |
| T1: `delete_pending` repository primitive | 1 file (+ its co-located test file) | ✅ Granular |
| T2: `publish_pending` restructuring | 1 file (+ its co-located test file) | ✅ Granular |
| T3: `_insert_and_publish` caller wiring | 2 source-adjacent files (processing.py + its test double) + 1 test file, one cohesive concern: retiring the transaction at the call site | ✅ Granular |
| T4: `docs/SCOPE.md` amendment | 1 file, 2 inline notes, one cohesive concern | ✅ Granular |
| T5: `docs/RISKS.md` amendment | 1 file, one cohesive concern | ✅ Granular |

---

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| --- | --- | --- | --- |
| T1 | None | None (Phase 1 start) | ✅ Match |
| T2 | T1 | Phase 1 → Phase 2 (T1 → T2) | ✅ Match |
| T3 | T2 | Phase 2 → Phase 3 (T2 → T3) | ✅ Match |
| T4 | T3 | Phase 3 → Phase 4 (T3 → T4) | ✅ Match |
| T5 | T3 | T4 → T5 (diagram shows sequential order within Phase 4; body's real dependency is T3, already satisfied earlier — phase-order arrow, not a data dependency, same pattern as `agent-model-config/tasks.md`'s T7→T8 note) | ✅ Match |

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| --- | --- | --- | --- | --- |
| T1: repository.py | `delete_pending` repository primitive | Integration | integration | ✅ OK |
| T2: publish_pending.py | `publish_pending` use case | Unit + integration | unit + integration | ✅ OK |
| T3: processing.py, fakes.py, test_processing.py | Publisher decision tree — `_insert_and_publish` | Unit (fakes) + Postgres-backed | unit + integration | ✅ OK |
| T4: docs/SCOPE.md | `docs/SCOPE.md` amendment | none | none | ✅ OK |
| T5: docs/RISKS.md | `docs/RISKS.md` amendment | none | none | ✅ OK |

All ✅ — no restructuring needed.

---

## Tools for Execution

No MCP needed for any task (this is direct Python/Markdown editing against an already-confirmed design, no external library API surface to re-verify). No skill needed beyond `tlc-spec-driven` itself (not `security-review`, `docs-writer`, etc. — the doc edits are small, precedent-matching inline amendments, not authored documentation). Executing inline, no sub-agent batching (5 tasks, well under the ~8 threshold).
