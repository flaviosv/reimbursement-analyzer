# Validation Report — Amendment (TRC-12, TRC-13)

**Verifier**: Independent pass (author ≠ verifier), per `tlc-spec-driven`'s Verifier contract.
**Scope**: T10 (commit `e4d6274`) and T11 (commit `3524b22`) only. TRC-01..11 were verified separately in `validation.md`.
**Branch**: `feature/17_traceability-gap-fixes`, diffed against `main`.
**Date**: 2026-08-10

---

## Task Completion

| Task | Commit | Files touched | Status |
| --- | --- | --- | --- |
| T10 | `e4d6274` | `packages/publisher/src/publisher/processing.py`, `packages/shared/src/shared/reimbursement/use_cases/publish_pending.py` | Confirmed done, matches design.md |
| T11 | `3524b22` | `packages/api/src/api/reimbursement/create/route.py` | Confirmed done, matches design.md |

Both commits are conventional-commit-formatted, scoped to exactly the files design.md's Components section names for TRC-12/TRC-13, and carry no unrelated changes.

---

## Spec-Anchored Acceptance Criteria

### TRC-12 — Publisher success-path correlation logging

Design.md (`shared/reimbursement/use_cases/publish_pending.py` and `publisher/processing.py` Components sections, lines 147–157) requires:

1. **`publish_pending()` returns the `uuid` it already mints, no new log call inside `shared`.**
   Verified — `packages/shared/src/shared/reimbursement/use_cases/publish_pending.py:34` return type is now `-> UUID`; line 73 (`return uuid`) is the only new statement in the function. The pre-existing `_compensate()` function in the same file already has a `logger.info` call (line 108-119), but `git blame` confirms this predates the amendment (commits `e6a9438`/`c63f3c4`, 2026-08-09 — one day before this amendment) and is not `publish_pending()` itself, so the "zero new log calls" claim holds exactly as scoped.

2. **`_insert_and_publish()` returns the `UUID` `publish_pending()` now returns.**
   Verified — `packages/publisher/src/publisher/processing.py:289` return type widened to `-> UUID`; line 296 changed from `await publish_pending(...)` to `return await publish_pending(...)`.

3. **`process_item()` logs `ITEM_PUBLISHED_EVENT` with `request_id`+`uuid` via `log_event`, only on the success path, before returning `ItemOutcome.PUBLISHED`.**
   Verified — `packages/publisher/src/publisher/processing.py:196` captures `uuid = await _insert_and_publish(...)`; line 208 calls `log_event(logger, logging.INFO, ITEM_PUBLISHED_EVENT, request_id=_request_id(item), uuid=str(uuid))` immediately before `return ItemOutcome.PUBLISHED` on line 209. Exact match to design.md's specified call shape.

4. **`publish_pending`'s existing failure/compensate path (`_compensate`) is untouched.**
   Verified — `_compensate()` body (lines 78-119) has zero diff in `git show e4d6274`; the new `return uuid` (line 73) sits after the `except`/`raise` block, so a failure path never reaches it. `process_item`'s `try/except PublishFailed`/`except Exception` branches (lines 197-207) are unchanged, confirming the new `log_event` call is genuinely unreachable on a failure.

5. **Event constant naming convention.**
   Verified — `ITEM_PUBLISHED_EVENT = "reimbursement.item_published"` (`processing.py:56`) matches the file's existing `reimbursement.*` naming for every sibling constant (`ITEM_FAILED_EVENT`, `DUPLICATE_DROPPED_EVENT`, etc.), alphabetically placed among the module-level constants.

**TRC-12 verdict: PASS.**

### TRC-13 — API reject-path request-id logging

Design.md (`api/reimbursement/create/route.py` Components section, lines 159–163) requires:

1. **`batch = validate_batch(raw)` wrapped in `try/except BatchInvalid as exc`, logging then re-raising.**
   Verified — `packages/api/src/api/reimbursement/create/route.py:64-74`. The `except BatchInvalid as exc:` block calls `log_event(logger, logging.INFO, BATCH_REJECTED_EVENT, request_ids=_lenient_request_ids(raw), reason=str(exc))` then `raise` (bare re-raise, preserves the original exception/traceback). `_batch_invalid_handler`'s existing 400 response path (`api/errors.py:39`, registered `api/errors.py:101`) is untouched.

2. **New module-local `_lenient_request_ids(raw: bytes) -> list[str | None]` helper — `json.loads(raw)`, list-comprehension extraction, `[]` on parse/shape failure.**
   Verified — `route.py:20-32`. `try: parsed = json.loads(raw) except json.JSONDecodeError: return []`, then `if not isinstance(parsed, list): return []`, then `[item.get("request_id") if isinstance(item, dict) else None for item in parsed]`. Exact match to design.md's specified logic and signature, and mirrors `publisher.processing._request_id()`'s `item.get("request_id") if isinstance(item, dict) else None` shape (Code Reuse Analysis row, design.md:71).

3. **Module-level `BATCH_REJECTED_EVENT = "reimbursement.batch_rejected"` constant.**
   Verified — `route.py:19`, immediately below the existing `BATCH_ACCEPTED_EVENT`, same naming convention.

4. **Raw request body never appears in the log line.**
   Verified by inspection — `log_event`'s kwargs are `request_ids` (list of strings/None) and `reason` (str). `reason=str(exc)` renders a `BatchInvalid` message; `BatchInvalid`'s own docstring (`packages/shared/src/shared/errors.py:8-10`) states it "names the offending item index and field only — never the value," and both raise sites (`packages/api/src/api/reimbursement/create/validation.py:31,33`) confirm this — no raw body or field value is ever embedded in the exception message. `raw` itself is passed only into `_lenient_request_ids(raw)`, which returns only extracted `request_id` strings, never the raw bytes.

5. **Malformed/non-list body edge case (spec.md line 342-345).**
   Verified — both failure modes (`json.JSONDecodeError` and a parsed-but-non-list value, e.g. a JSON object) are independently handled and both return `[]`, never propagating a second exception out of the logging path.

**TRC-13 verdict: PASS.**

---

## Discrimination Sensor

Per spec.md's Out of Scope table, no new tests were written for TRC-12/13 (same explicit user decision as TRC-01..10). This sensor checks whether the *existing* suite incidentally catches behavior-level regressions in the new code — informational, not a gate.

| # | Mutation | File | Result |
| --- | --- | --- | --- |
| 1 | Swapped `return ItemOutcome.PUBLISHED` to precede the new `log_event(...)` call (making the log call dead code, unreachable) | `packages/publisher/src/publisher/processing.py` | **Survived** — `uv run pytest packages/publisher/tests/test_processing.py -q` → 54 passed, 0 failed. Expected: no test asserts on `ITEM_PUBLISHED_EVENT`'s log content or on `process_item`'s side-effecting log call, only its return value. |
| 2 | Changed `BATCH_REJECTED_EVENT`'s `raise` (re-raise) to `batch = []` (swallow, falls through to success path) | `packages/api/src/api/reimbursement/create/route.py` | **Caught** — `uv run pytest packages/api/tests/reimbursement/create/ -q -m "not integration"` → 5 failed, 45 passed. `test_route.py`'s existing `DescribeCreateReimbursement::it_returns_400_and_publishes_nothing` (4 parametrized cases) and `DescribeTheRealApp::it_returns_400_through_the_actual_app_object` all assert the 400 status code and were previously protecting the pre-existing `BatchInvalid` re-raise contract, not anything new — they incidentally also protect T11's re-raise line. |

Both mutations were reverted immediately after their respective test run via `git checkout -- <file>`; `git diff <file>` confirmed byte-identical to HEAD after each revert.

**Interpretation**: Mutation 1 surviving is expected and matches the spec's explicit no-new-tests decision — the new correlation log itself has no independent test coverage, same posture as TRC-01..11. Mutation 2 being caught is a side effect of pre-existing 400-contract tests, not evidence of new coverage for the log line itself. Neither result changes the PASS verdict; this section is informational per the task's own instructions.

---

## Code Quality

- **No scope creep**: diff for both commits touches exactly the files/lines design.md's Components sections describe (`processing.py`, `publish_pending.py` for T10; `create/route.py` for T11). No unrelated refactors, no drive-by changes.
- **`shared` stays log-free (per Out of Scope table)**: `git show e4d6274 -- packages/shared/` shows only a docstring addition and `return uuid` — confirmed via `grep -n "log_event\|logger\." packages/shared/src/shared/reimbursement/use_cases/publish_pending.py`, which returns only the pre-existing `_compensate()` call (predates this amendment by one day, per `git blame`). Zero new log/logger calls added to `shared` by this amendment.
- **No leftover debug code**: none found in either diff (no `print`, no commented-out code, no `TODO`/`FIXME`).
- **Convention match**:
  - Event constants (`ITEM_PUBLISHED_EVENT`, `BATCH_REJECTED_EVENT`) follow the existing `"<domain>.<event>"` string convention and are placed alphabetically/adjacently among sibling constants in both files.
  - `log_event(logger, logging.INFO, EVENT, **fields)` call shape matches every other TRC-01..11 call site exactly (positional `logger`, `level`, `event`, then kwargs).
  - `_lenient_request_ids()` reuses the identical `item.get("request_id") if isinstance(item, dict) else None` defensive idiom already established by `publisher.processing._request_id()` — no new pattern introduced, as design.md's Code Reuse Analysis requires.
  - The T11 `try/except ... raise` narrow-wrap shape mirrors `update/route.py`'s TRC-03 pattern (bare re-raise after a side-effecting log call), consistent with the codebase's existing narrow-wrap convention.
- **Type hints**: `UUID` import added to `processing.py` (`from uuid import UUID`) — required and used; no unused imports introduced in either diff.
- **PII**: confirmed above (Acceptance Criteria point 4) — raw request bodies are never logged in either new call site.

---

## Gate Check

```
uv run pytest packages/api packages/publisher packages/shared -m "not integration" -q
```

**Result**: `447 passed, 6 deselected, 1 warning in 13.10s` — **0 failures.**

(The `rdkafka` connection-refused line in the output is expected background noise from a producer test exercising an unreachable broker on purpose, not a test failure — it does not appear in the pass/fail summary.)

No test-count regression: this figure is consistent with a full, unmodified suite (T10/T11's own Done-when checklists both required "existing suite passes unmodified," and no test file appears in either commit's diff — confirmed via `git show --stat` for both commits above).

---

## Summary

| Check | Result |
| --- | --- |
| TRC-12 acceptance criteria | PASS — all 5 sub-criteria verified against code, file:line cited |
| TRC-13 acceptance criteria | PASS — all 5 sub-criteria verified against code, file:line cited |
| Regression gate | PASS — 447 passed, 0 failed, 6 deselected (integration) |
| Discrimination sensor | Informational — 1 survived (expected, no new tests per spec), 1 caught (incidental, pre-existing test) |
| Code quality | PASS — no scope creep, `shared` remains log-free for `publish_pending()` itself, conventions matched, no PII leakage, no debug code |
| Working tree clean at end | PASS — `git status --short` shows only the three pre-existing uncommitted spec-doc edits (`design.md`, `spec.md`, `tasks.md`) that were already present before this verification pass began; no residual mutation artifacts |

## Overall Verdict: **PASS**

T10 (TRC-12) and T11 (TRC-13) are both implemented exactly as design.md specifies, introduce no regressions, and each independently pulls its weight against the correlation-chain gaps documented in spec.md's amendment. No blocking issues found. Recommend proceeding to merge.
