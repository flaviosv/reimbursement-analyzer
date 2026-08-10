# Publisher Compensating-Delete — Validation Report

**Verifier:** independent sub-agent (author != verifier, evidence-or-zero)
**Diff range:** `origin/main..HEAD` (`c5135b2`, `e6a9438`, `56a9a1c`, `d951089`, `b145a62`, `2ffc319`)
**Date:** 2026-08-09

---

## Task Completion

| Task | Commit | Status |
| --- | --- | --- |
| T1: `delete_pending` repository primitive | `c5135b2` | Complete |
| T2: `publish_pending` restructuring (compensating delete) | `e6a9438` | Complete |
| T3: `_insert_and_publish` caller wiring | `56a9a1c` | Complete |
| T4: `docs/SCOPE.md` amendment | `d951089` | Complete |
| T5: `docs/RISKS.md` amendment | `b145a62` | Complete |
| Task-status catch-up (`spec.md`, `tasks.md`) | `2ffc319` | Complete |

All 5 implementation tasks present in the diff range; nothing left uncommitted.

---

## Spec-Anchored Acceptance Criteria

| AC | Requirement | Test file:line | Assertion proves | Status |
| --- | --- | --- | --- | --- |
| AC1 | Insert commits before publish; no `conn.transaction()` spans the publish call | `publish_pending.py:54-65` (structural — `async with conn.transaction():` closes at line 55, before the publish call at lines 59-62); `processing.py` diff (`_insert_and_publish`) confirms the outer wrapper was removed | `insert_pending` is inside its own transaction that fully closes before `publish()` is invoked — matches AD-034's binding requirement ("no transaction spans the Kafka publish call") exactly | Verified |
| AC2 | Publish succeeds → row stands, `ItemOutcome.PUBLISHED` | `test_processing.py:236` `DescribeInsertThenPublish::it_commits_exactly_one_pending_row_when_both_steps_succeed` | `outcome is ItemOutcome.PUBLISHED`, row count == 1, `row["status"] == "pending"` | Verified |
| AC3 | Publish raises `PublishFailed` → delete just-inserted row (`WHERE uuid=$1 AND status='pending'`) before requeue | `test_publish_pending.py:120` `DescribeTheCompensatingDelete::it_deletes_the_row_when_the_publish_fails`; WHERE-clause guard proven by `test_repository.py:606-651` `DescribeDeletePending` (3 cases) | Row is `None` after a forced `PublishFailed`; `delete_pending` removes exactly the matching pending row and leaves a non-pending row untouched | Verified |
| AC4 | Delete removes 1 row → `reimbursement.compensating_delete` logged with `uuid`, `request_id`, `retry`, publish error type | `test_publish_pending.py:140` `it_logs_the_compensating_delete_with_uuid_request_id_retry_and_error_type` | `events[0] == {"event": COMPENSATING_DELETE_EVENT, "uuid": ..., "request_id": "REQ-DELETE-LOG", "retry": 3, "publish_error_type": "PublishFailed"}` (exact dict match) | Verified |
| AC5 | Delete affects 0 rows → distinct `reimbursement.compensating_delete_noop` anomaly log, requeue proceeds unchanged (no raise/block) | `test_publish_pending.py:169` `it_logs_a_distinct_anomaly_and_still_raises_when_the_delete_affects_no_rows` | `events[0]["event"] == COMPENSATING_DELETE_NOOP_EVENT`; wrapped in `pytest.raises(PublishFailed)` — proves `PublishFailed` still propagates so requeue is not blocked | Verified |
| AC6 | Delete itself raises → durable `failure_log` record (`compensating_delete_failed`) with `uuid`, item, both errors, no further raise | `test_publish_pending.py:201` `it_writes_a_failure_log_record_and_still_raises_when_the_delete_itself_fails` | `record["event"] == COMPENSATING_DELETE_FAILED_EVENT`, `record["item"] == item`, `record["retry"] == 2`, `publish_error_type == "PublishFailed"`, `delete_error_type == "PostgresConnectionError"`; still wrapped in `pytest.raises(PublishFailed)` — the delete's own exception is not what propagates | Verified |
| AC7 | Any publish-failure outcome → requeue exactly as today (`Request`, `retry+1`, `AttemptError` appended) | `test_processing.py:379` `DescribeTheRequeue::it_appends_one_entry_naming_the_publish_stage_when_the_publish_fails` + `test_processing.py:341` `it_republishes_only_the_failed_item_with_the_retry_incremented` | `errors[0]["stage"] == "publish"`, `errors[0]["error_type"] == "PublishFailed"`; `requeued["retry"] == 2` (increment proven on the shared, untouched `_requeue` code path) | Verified |
| AC8 | DB insert itself fails → unchanged behavior (`is_duplicate` / generic `db-insert` requeue) | `test_processing.py:277` `DescribeADuplicateItem::it_drops_the_item_without_storing_a_second_row`; `test_processing.py:364` `it_appends_one_entry_naming_the_db_insert_stage_when_the_insert_fails` | Duplicate insert still dropped without a second row (also the exact case AD-034 caught and fixed — a real regression during Execute, corrected before commit); db-insert failure still requeues with `stage == "db-insert"` | Verified |
| AC9 | `docs/SCOPE.md:241-252` carries inline dated note replacing "single transaction" language | `docs/SCOPE.md:244-249` and `:259-264` | Two inline `(amended — ...)` notes added beside the original bullets, citing AD-033; original bullets not deleted (manual diff review — prose, no automated gate applies) | Verified |
| AC10 | `docs/RISKS.md` R-001 records closed vs. opened consequences distinctly | `docs/RISKS.md:17` (Status line) and `:81-114` (new `### AD-033 amendment` subsection) | Closed consequences (ghost message, duplicate-via-commit-failure) stated in one paragraph (`:87-93`); the new narrower residual risk (orphaned row) stated in a separate paragraph (`:95-107`) — explicitly not merged, per spec's requirement | Verified |

**10/10 verified.**

---

## Edge Cases

| Edge Case | Evidence | Status |
| --- | --- | --- |
| False-negative publish timeout (broker delivered, client sees `PublishFailed`) → same pre-existing ghost-message behavior, not a regression | Documented only (spec.md Assumptions table, traced during Specify; `docs/RISKS.md:109-114` restates it). No dedicated test — this is an assertion about an existing, unmodified downstream tolerance (`GHOST_DROPPED_EVENT`, in the `reimbursement` package, out of this feature's scope), not new behavior this feature introduces | Documented, not independently tested (appropriately — nothing in this feature's diff changes this path) |
| Process crash between delete dispatch and completion → orphaned row, accepted residual risk, not auto-healed | Documented in spec.md Edge Cases, `docs/RISKS.md:95-107` (AD-033 amendment), `docs/STATE.md` AD-033. Inherently untestable (a mid-flight process crash) — correctly left as a documented, accepted gap rather than a fabricated test | Documented, correctly not tested |
| `envelope.retry > MAX_RETRY` → `escalate_item` path untouched | `test_processing.py:564` `DescribeAMessagePastTheRetryCeiling` (unmodified by this diff — 0 lines touched in that class) | Verified (unchanged, confirmed by diff absence + passing suite) |
| Delete races with concurrent write (status != pending) → `AND status='pending'` guard makes it a no-op, AC5 fires | `test_repository.py:620` `DescribeDeletePending::it_returns_false_and_leaves_the_row_when_status_is_not_pending` | Verified |

---

## Discrimination Sensor

Lightweight tier, 3 mutations, mutate → run targeted tests → `git checkout --` revert. Tree confirmed clean before, between, and after.

| # | Mutation | File | Targeted run | Result |
| --- | --- | --- | --- | --- |
| 1 | `_DELETE_PENDING` WHERE clause: `status = 'pending'` → `status = 'human-review'` | `packages/shared/src/shared/reimbursement/repository.py` | `test_repository.py` + `test_publish_pending.py` | **Killed** — 4 failed (`DescribeDeletePending` x2, `DescribeTheCompensatingDelete` x2) |
| 2 | `_compensate`'s event ternary flipped: `COMPENSATING_DELETE_NOOP_EVENT if deleted else COMPENSATING_DELETE_EVENT` (swapped) | `packages/shared/src/shared/reimbursement/use_cases/publish_pending.py` | `test_publish_pending.py` | **Killed** — 2 failed (both traceability-log assertions) |
| 3 | Removed the `raise` after `_compensate(...)` in `publish_pending` (swallows `PublishFailed`) | `packages/shared/src/shared/reimbursement/use_cases/publish_pending.py` | `test_publish_pending.py` + `test_processing.py` | **Killed** — 6 failed (all 4 `DescribeTheCompensatingDelete` tests, plus `DescribeInsertThenPublish::it_leaves_no_row_behind_when_the_publish_fails` and `DescribeTheRequeue::it_appends_one_entry_naming_the_publish_stage_when_the_publish_fails` in `packages/publisher`, confirming the cross-package dependency on the re-raise) |

**3/3 mutants killed.** `git status --short` clean after each revert and at final check.

---

## Code Quality

| Check | Finding |
| --- | --- |
| `delete_pending` shape vs. `update_decision` | Matches precedent exactly: same `DELETE n`/`UPDATE n` status-string parsing pattern, same `conn.execute(...)` + `== "DELETE 1"` boolean-return shape, same docstring style naming the reused pattern explicitly |
| `_compensate`'s structured logging vs. `DUPLICATE_DROPPED_EVENT` style | Event naming (`reimbursement.<snake_case>`) and dict-shaped `logger.info(json.dumps({...}))` call match the existing convention. One minor divergence: `processing.py` already uses a `_LazyJSON` wrapper (pre-existing on `main`, `processing.py:33-46`) to defer `json.dumps` until the log level is actually enabled; `_compensate` uses eager `json.dumps(...)` instead. Not a defect — this path only fires on a publish failure (not a hot per-item path), and `CONVENTIONS.md` documents `_LazyJSON` as observed specifically in `publisher.processing`, not as a cross-module requirement — but it is the same optimization opportunity if this module's log volume ever grows |
| Layering | Compensation logic lives inside `publish_pending` (shared/use_cases), not leaked into `publisher.processing` — matches design.md's stated AD-025 rationale; `process_item`'s `except PublishFailed` branch is untouched |
| Scope creep | None found — diff touches exactly the 9 files enumerated in the task brief plus `.specs/STATE.md` (AD-033/AD-034, expected) and `.specs/features/publisher-compensating-delete/{spec.md,tasks.md}` (status-tracking updates only, no requirement text changed) |
| Comment sparsity | Consistent with `CONVENTIONS.md`: comments explain non-obvious *why* (the `conn.transaction()`-around-insert-only rationale, the stale-comment rewrite in `processing.py`), no narrational *what* comments added |
| AD-034 (mid-Execute correction) | A real defect was caught and fixed during Execute, not left in: a bare `insert_pending` call (no savepoint) was found to poison the connection's enclosing transaction on a real `UniqueViolationError`, reproduced directly by the pre-existing `DescribeADuplicateItem::it_drops_the_item_without_storing_a_second_row` test. The fix (wrap only the insert in its own `conn.transaction()`, matching `escalate_item.send_human_review`'s established precedent) is recorded as AD-034 and does not violate AC1's binding guarantee (no transaction spans the *publish* call) — verified independently above |

---

## Gate Check Results

| Command | Result |
| --- | --- |
| `uv run pytest packages/shared packages/publisher` (feature-scoped, full) | **234 passed** (matches tasks.md's recorded count) |
| `uv run pytest -m "not integration" --ignore=packages/reimbursement/tests` (repo-wide baseline) | **435 passed, 6 deselected** — 0 failures |

The repo-wide command's stdout `rdkafka` connection-refused lines are noise from a producer object's background thread attempting a real broker connection during teardown (no Docker Kafka running in this session) — they do not correspond to a failed or erroring test; the summary line confirms 0 failures.

`packages/reimbursement/tests` was excluded per instructions — confirmed via `docs/codebase/CONCERNS.md`'s Known Bugs section (`PLACEHOLDER_PROMPT` import error in `reimbursement.agent.agent`, described as "uncommitted, in-progress work" predating this feature and unrelated to the publisher's insert+publish path). Not this feature's regression.

---

## Requirement Traceability Update

| Requirement ID | Story | Verifier Status |
| --- | --- | --- |
| PCD-01 | Insert commits before publish is attempted | Verified |
| PCD-02 | Publish success unchanged | Verified |
| PCD-03 | Publish failure triggers compensating delete | Verified |
| PCD-04 | Delete-succeeded traceability | Verified |
| PCD-05 | Delete-no-op traceability | Verified |
| PCD-06 | Delete-failed traceability (`failure_log`) | Verified |
| PCD-07 | Requeue path unchanged | Verified |
| PCD-08 | DB-insert-failure path unchanged | Verified |
| PCD-09 | `docs/SCOPE.md` amendment | Verified |
| PCD-10 | R-001 amendment | Verified |

**10/10 Verified, 0 Needs Fix.**

---

## Summary

**PASS.** All 10 P1 acceptance criteria (PCD-01..10) have a spec-anchored test or documentation citation with an exact file:line and an assertion that matches the spec-defined expected outcome — none by inference alone. All 4 listed Edge Cases are either verified by an existing/unmodified test or correctly left as documented-only (the two that are inherently untestable: a false-negative publish timeout's downstream tolerance, out of this feature's scope; a mid-flight process crash, this feature's own accepted residual risk).

The full feature-scoped gate (`packages/shared packages/publisher`) is green at 234/234, and the repo-wide baseline minus the pre-existing, unrelated `packages/reimbursement/tests` collection failure is green at 435/435 with 0 regressions. All 3 discrimination-sensor mutations (WHERE-clause status literal, event-ternary flip, swallowed re-raise) were killed by the existing suite, spanning both the `packages/shared` and `packages/publisher` test files — confirming the tests assert real behavior, not just call the code.

Git tree is clean (`git status --short` empty) — no mutation artifacts left behind.

One implementation-level self-correction is worth noting positively rather than as a gap: AD-034, discovered and fixed during this feature's own Execute phase, caught a real defect (a bare insert call poisoning the connection's transaction state on a duplicate-key error) via an existing test, and the fix was verified independently above to still satisfy AC1's actual binding requirement. This is exactly the kind of design-vs-implementation drift the spec's own Assumptions/Design conflict-check process is meant to surface — and it was caught before this validation pass, not by it.

No clean-PASS gaps, surviving mutants, or spec-precision issues were found. No lessons recorded (per Step 7 — nothing had signal to distill).
