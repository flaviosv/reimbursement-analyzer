# Agent — Consume `Reimbursement`, Resolve by UUID Validation

**Date**: 2026-08-08 (re-verified same day, post-fix commit `dba67c4`)
**Spec**: `.specs/features/agent-consume-reimbursement/spec.md`
**Diff range**: `f28627e830267c0afffddc08fb25612b28e6c6fc..HEAD` (12 commits, includes fix commit `dba67c4`)
**Verifier**: independent sub-agent (author ≠ verifier)

---

## Re-verification note (2026-08-08)

Fix commit `dba67c4` ("test(agent): close verifier-found gaps at the retry-ceiling boundary") addressed all 3 ranked gaps from the first validation pass below by strengthening `src/agent/tests/test_validation.py` only — no production code changed:

1. Added `DescribeRetryCeilingEscalation.it_does_not_escalate_at_exactly_the_retry_ceiling` — asserts `retry == MAX_RETRY` (3) stays on `RESOLVED`, not `ESCALATED`.
2. Strengthened `DescribeResolveByUuid.it_treats_a_uuid_with_no_row_as_a_ghost_and_drops_it` (AGT-03) — now asserts `producer.produced == []` and no CRITICAL-level log record.
3. Strengthened `DescribeRetryCeilingEscalation.it_escalates_a_real_row_and_returns_escalated` (AGT-17) — now asserts `producer.produced == []`.

All three were independently re-verified this pass (see updated Discrimination Sensor and Fix Plans sections). All were confirmed real. **Verdict flips to PASS.** The rest of this document is left as originally written (dated evidence from the first pass), with the Sensor, Fix Plans, Requirement Traceability, and Summary sections updated in place to reflect the re-verification.

---

## Task Completion

| Task | Status  | Notes |
| ---- | ------- | ----- |
| T1: Widen `Stage` | ✅ Done | `shared/src/shared/models.py:14` |
| T2: Add `AgentConfig` | ✅ Done | `shared/src/shared/config.py:95-119,153-155` |
| T3: `get_by_uuid`/`update_human_review` | ✅ Done | `shared/src/shared/reimbursement/repository.py:85-100` |
| T4: `escalate_existing` | ✅ Done | `shared/src/shared/reimbursement/use_cases/send_human_review.py:40-48` |
| T5: Parse/dispatch/escalate | ✅ Done | `agent/src/agent/validation.py:59-113` |
| T6: Resolve/staleness/requeue | ✅ Done | `agent/src/agent/validation.py:116-168` |
| T7: Consumer composition root | ✅ Done | `agent/src/agent/consumer.py` (full rewrite, `SampleMessage`/`sample-topic` removed) |
| T8: End-to-end proof | ✅ Done | `agent/tests/test_integration.py` |

---

## Spec-Anchored Acceptance Criteria

| Criterion (WHEN X THEN Y) | Spec-defined outcome | `file:line` + assertion | Result |
| -------------------------- | --------------------- | ------------------------ | ------ |
| AGT-01: retry≤3 → query by uuid | `get_by_uuid` invoked with the message's uuid | `agent/validation.py:81-83,119-121` (dispatch + call); `agent/tests/test_validation.py:126-143` — `assert outcome == MessageOutcome.RESOLVED` against a `FakePool` keyed by that exact uuid | ✅ PASS |
| AGT-02: row found → staleness check | proceeds to the `published_at`/`updated_at` compare | `agent/validation.py:134` reached only past a non-`None` row; `agent/tests/test_validation.py:126-143,162-176` | ✅ PASS |
| AGT-03: no matching row → ghost, log info, drop, no republish/retry/failure-log | `MessageOutcome.GHOST`, informational log, **and** no side effects | `agent/validation.py:125-132`; `agent/tests/test_validation.py:145-181` (post-`dba67c4`) — asserts `outcome == GHOST`, `all(r.levelno < logging.ERROR for r in ghost_records)`, **and now** `producer.produced == []` and `not any(r.levelno >= logging.CRITICAL for r in caplog.records)` | ✅ PASS (re-verified — see Re-verification note) |
| AGT-04: ghost/handled → offset committed | `commit()` called once, regardless of outcome | `agent/consumer.py:68-73` (unconditional commit after `handle_message`); `agent/tests/test_consumer.py:196-203` — `it_commits_the_offset_after_handling_a_message` | ✅ PASS |
| AGT-05: `updated_at` > `published_at` → ignore | `MessageOutcome.STALE`, no further action | `agent/validation.py:134-136`; `agent/tests/test_validation.py:162-176` — `assert outcome == MessageOutcome.STALE` and `pool.rows[uuid]["status"] == "pending"` | ✅ PASS |
| AGT-06: `published_at` ≥ `updated_at` (incl. exact tie) → fresh, proceed | `MessageOutcome.RESOLVED` | `agent/validation.py:138-139`; `agent/tests/test_validation.py:126-143` and the exact-tie case at `:178-187` — `assert outcome == MessageOutcome.RESOLVED` | ✅ PASS |
| AGT-07: stale → offset still committed | `commit()` called | `agent/consumer.py:73` (unconditional); `agent/tests/test_integration.py:133-148` — `_run_agent(..., expected_advance=1)` proves the committed offset advances by exactly 1 for a stale message | ✅ PASS |
| AGT-08: DB error on query → republish with `retry+1` | new `ReimbursementEnvelope`, `retry = consumed.retry + 1` | `agent/validation.py:142-152`; `agent/tests/test_validation.py:191-208` — `assert requeued["retry"] == 1` | ✅ PASS |
| AGT-09: republished `errors` = prior + one new entry (attempt, ts, stage, type, message) | one new `AttemptError.next(...)` entry appended | `agent/validation.py:149`; `agent/tests/test_validation.py:206-208` (`stage == "resolve"`, `error_type == "RuntimeError"`) + `:250-251` (attempt numbering) — message-field content not directly asserted at this layer, but `AttemptError.next` itself is separately unit-tested at `shared/tests/test_models.py:72-77` | ✅ PASS |
| AGT-10: prior entries preserved, never overwritten/truncated | every earlier `AttemptError` still present, in order | `agent/validation.py:149` (`[*envelope.errors, ...]`); `agent/tests/test_validation.py:238-251` — `assert requeued["errors"][0]["attempt"] == 1` and `[1]["attempt"] == 2` | ✅ PASS |
| AGT-11: republished message processed like any other, no special-casing beyond `retry` non-zero | identical code path taken | `agent/validation.py:81-139` has no branch on `retry` other than the `> MAX_RETRY` check; `agent/tests/test_validation.py:238-251` exercises `retry=1` through the same `_resolve`/`_requeue` path | ✅ PASS |
| AGT-12: ERROR-level stdout log, carries uuid + error type, no driver-supplied value-bearing detail | sanitized log line | `agent/validation.py:148` (`logger.error("uuid=%s resolve failed: %s", envelope.uuid, sanitize(exc))`); `agent/tests/test_validation.py:214-236` — asserts `"person@example.com"` and `"DETAIL"` absent from ERROR lines | ✅ PASS |
| AGT-13: republish itself fails → failure log, treated as handled | `MessageOutcome.LOGGED`, failure-log write | `agent/validation.py:160-167`; `agent/tests/test_validation.py:253-266` — `assert outcome == MessageOutcome.LOGGED` and `"reimbursement.resolve_failed"` present | ✅ PASS |
| AGT-14: `retry > 3` → skip resolve, attempt `UPDATE` by uuid | escalation path only | `agent/validation.py:81-83` (mutually exclusive dispatch); `agent/tests/test_validation.py:74-92` — real row's `status` becomes `"human-review"` | ✅ PASS |
| AGT-15: `decision_reason` renders every entry (attempt#, ts, stage, type, message), not a count | full per-attempt rendering | `send_human_review.py:15-31` (`render_history`); `shared/tests/reimbursement/use_cases/test_send_human_review.py:47-54` (all 5 fields per entry) + `:129-139` (`escalate_existing` reuse, 3 distinct messages/stages all present) | ✅ PASS |
| AGT-16: empty/absent `errors` + `retry>3` → non-null `decision_reason` stating the ceiling | `_NO_HISTORY` branch | `send_human_review.py:22-23`; `shared/tests/reimbursement/use_cases/test_send_human_review.py:141-150` — `assert reason is not None` and `"ceiling" in reason.lower()` | ✅ PASS |
| AGT-17: `UPDATE` affects 1 row → log ERROR, no further action, no republish | `MessageOutcome.ESCALATED` | `agent/validation.py:110-113`; `agent/tests/test_validation.py:74-95` (post-`dba67c4`) — asserts `ESCALATED` outcome, an ERROR-level `"reimbursement.escalated"` record, **and now** `producer.produced == []` | ✅ PASS (re-verified — see Re-verification note) |
| AGT-18: `UPDATE` affects 0 rows (ghost) → failure log | `MessageOutcome.LOGGED` | `agent/validation.py:101-108`; `agent/tests/test_validation.py:94-107` — `assert outcome == MessageOutcome.LOGGED` and `"reimbursement.escalation_failed"` present; repository-level proof at `shared/tests/reimbursement/test_repository.py:207-213` | ✅ PASS |
| AGT-19: `UPDATE` raises a genuine DB error → failure log | `MessageOutcome.LOGGED` | `agent/validation.py:93-99`; `agent/tests/test_validation.py:109-123` — `update_errors` injected, `assert outcome == MessageOutcome.LOGGED` | ✅ PASS |
| AGT-20: malformed/schema-invalid message → failure log, no DB/UPDATE/republish | `MessageOutcome.INVALID`, zero pool acquisitions | `agent/validation.py:62-75` (early return before any pool use); `agent/tests/test_validation.py:47-59` — `assert pool.acquisitions == 0` (explicit) | ✅ PASS |
| AGT-21: every outcome → offset committed, loop continues | commit + no crash across a bad+good pair | `agent/consumer.py:68-73` (unconditional); `agent/tests/test_consumer.py:237-248` — `it_keeps_consuming_after_a_message_it_could_not_parse`, `len(consumer.commits) == 2` | ✅ PASS |
| AGT-22: termination signal mid-processing → in-flight txn rolls back, offset not committed | offset uncommitted for the interrupted message | `agent/consumer.py:51-73` (commit only reached after `handle_message` returns); `agent/tests/test_consumer.py:278-294` — task cancelled mid-`produce()`, `assert consumer.commits == []` | ✅ PASS — ⚠️ spec-precision note: this feature wraps no literal `conn.transaction()` (design.md's own Tech Decisions explicitly say so), so "the in-flight transaction SHALL roll back" has no DB-transaction referent to test against; the testable half (offset not committed) is proven |

**Status**: ✅ All 22 ACs fully matched the spec-defined outcome as of the re-verification (AGT-03 and AGT-17's earlier gaps were closed by `dba67c4`) / 1 spec-precision note remains (AGT-22, unchanged — see its row above)

---

## Discrimination Sensor

### First pass (pre-fix, `HEAD` at the time = `04f635a`)

Scratch state: direct edits to the real file followed by `git checkout --` per mutation (no `git worktree`/stash needed since the tree started clean and each mutation was discarded before the next began). Tree confirmed clean (`git status --short` empty, `git diff --stat` empty) and `git stash list` unchanged (3 pre-existing stashes, none mine) after all three mutations.

| Mutation | File:line | Description | Killed? |
| -------- | --------- | ------------ | ------- |
| 1 | `src/agent/src/agent/validation.py:134` | Flipped staleness comparator `published_at < updated_at` → `published_at <= updated_at` | ✅ Killed — `test_validation.py::DescribeResolveByUuid::it_resolves_a_fresh_row_and_returns_resolved` and `::it_treats_an_exactly_equal_timestamp_as_fresh_not_stale` both fail (2 failures) |
| 2 | `src/agent/src/agent/validation.py:81` | Retry-ceiling boundary `envelope.retry > MAX_RETRY` → `envelope.retry >= MAX_RETRY` | ❌ **Survived** — full quick gate (`uv run pytest -q -m "not integration"`) still reports `311 passed, 10 deselected`; no test exercises `retry == MAX_RETRY` (3) exactly, so the mutant escapes detection |
| 3 | `src/agent/src/agent/validation.py:125-132` | Removed the `if row is None:` ghost branch entirely | ✅ Killed — `test_validation.py::DescribeResolveByUuid::it_treats_a_uuid_with_no_row_as_a_ghost_and_drops_it` fails with an uncaught `TypeError: 'NoneType' object is not subscriptable` |

**Sensor depth**: lightweight (3 mutations, default tier)
**Result**: 2/3 killed — ❌ **FAIL**

Mutation 2 was a genuine, reproducible test gap at the time: no test in `src/agent/tests/test_validation.py` (nor anywhere else in the diff) constructed an envelope with `retry == 3` and asserted it took the normal resolve path (`RESOLVED`/`GHOST`/`STALE`/`REQUEUED`) rather than escalation. `retry=4` was well covered (T5/T6's escalation tests); the boundary itself was not. A future refactor that silently changed `>` to `>=` (or vice versa) would have shipped undetected.

### Re-verification pass (post-fix commit `dba67c4`)

Same scratch-state method (direct edit + `git checkout --`, no code left mutated). After each mutation: `git status --short` and `git diff --stat` empty, `git stash list` unchanged (still the same 3 pre-existing stashes).

| # | File:line | Description | Killed? |
| - | --------- | ------------ | ------- |
| 2 (re-run) | `src/agent/src/agent/validation.py:81` | Same mutation: `envelope.retry > MAX_RETRY` → `envelope.retry >= MAX_RETRY` | ✅ **Now killed** — `test_validation.py::DescribeRetryCeilingEscalation::it_does_not_escalate_at_exactly_the_retry_ceiling` fails: `AssertionError: assert <MessageOutcome.ESCALATED> == <MessageOutcome.RESOLVED>` |
| Spot-check A | `src/agent/src/agent/validation.py:125-132` | Ghost branch kept correct (still returns `GHOST`, still logs informationally) but a spurious `publish(...)` + `failure_log.write(...)` call injected before the `return` — simulates a regression that gets the *outcome* right but violates AGT-03's negative clause | ✅ **Killed** — `it_treats_a_uuid_with_no_row_as_a_ghost_and_drops_it` fails: `assert producer.produced == [] ` → `AssertionError: assert [('Reimbursement', b'{}')] == []` (would also have failed on the new CRITICAL-log assertion, since `failure_log.write` logs at `CRITICAL`) |
| Spot-check B | `src/agent/src/agent/validation.py:110-113` | Escalated-success branch kept correct (still returns `ESCALATED`, still logs the ERROR line) but a spurious `publish(...)` call injected before the `return` — simulates a regression that gets the outcome right but violates AGT-17's negative clause | ✅ **Killed** — `it_escalates_a_real_row_and_returns_escalated` fails: `assert producer.produced == []` → `AssertionError: assert [('Reimbursement', b'{}')] == []` |

**Sensor depth**: lightweight (1 re-run + 2 targeted spot-checks confirming the strengthened assertions are non-tautological)
**Result**: 3/3 killed — ✅ **PASS**

The two spot-checks confirm the new assertions in `it_treats_a_uuid_with_no_row_as_a_ghost_and_drops_it` and `it_escalates_a_real_row_and_returns_escalated` are load-bearing, not tautological: each fails specifically on the injected violation of its AC's negative clause, with the rest of the branch (outcome, log level, log content) left intentionally correct so the failure is attributable to the new assertion alone.

All mutations were reverted via `git checkout --` after each check; the real tree was re-confirmed clean before proceeding to the next.

---

## Code Quality

| Principle        | Status |
| ---------------- | ------ |
| Minimum code     | ✅ |
| Surgical changes | ✅ — diff confined to `src/agent/`, the five named `shared/` files, `pyproject.toml`, and the feature's own `.specs/` docs |
| No scope creep   | ✅ — decision logic (reject/auto-approve) correctly left out; no unrequested abstractions found |
| Matches patterns | ✅ — mirrors `publisher/processing.py` (outcome-enum, never-raises decision tree) and `publisher/consumer.py` (composition root, offset-commit shape) throughout |
| Spec-anchored outcome check (asserted values match spec) | ✅ — 22/22 fully match as of `dba67c4`; AGT-03 and AGT-17 now assert both the primary outcome and the accompanying negative claims ("no republish", "no failure-log entry") |
| Per-layer Coverage Expectation met (domain 1:1 ACs; routes happy+edge+error) | ✅ — the `retry == MAX_RETRY` boundary is now covered (see Sensor re-verification) |
| Every test maps to a spec requirement — no unclaimed tests | ✅ — spot-checked `test_validation.py`, `test_consumer.py`, `test_config.py`; every `it_*` traces to an AGT- id, an edge case, or a Done-when bullet |
| Documented guidelines followed | ✅ `docs/codebase/TESTING.md`, `docs/codebase/CONVENTIONS.md`, `pyproject.toml` (`python_classes`/`python_functions`/`pythonpath`/`markers`) — `Describe*`/`it_*` naming, `agent_fakes.py` module-not-fixture pattern (with the documented name-collision rationale against `publisher/tests/fakes.py`), `pytestmark = pytest.mark.anyio` all followed as specified in `tasks.md`'s Test Coverage Matrix header |

---

## Edge Cases

- [x] Multi-instance/partition concurrency: no new coordination added (architectural, per design.md Risks & Concerns — nothing to test beyond what Kafka/Postgres already guarantee)
- [~] Crash-redelivery idempotency (already-`human-review` row `UPDATE`d again is a no-op; an already-logged resolution is logged again): relies on the read-then-conditionally-write shape being naturally idempotent by construction — no test explicitly redelivers the same message twice and asserts no corrupted state, though `update_human_review`/`get_by_uuid`'s own repository tests confirm each operation individually is side-effect-safe to repeat
- [x] `retry` past 3 via unrelated transient errors + genuine ghost → `retry > 3` `UPDATE` discovers 0 rows → failure log: this is AGT-18, covered (`test_validation.py:94-107`)
- [x] Same error recurring on all attempts → `decision_reason` shows every entry, not collapsed: `shared/tests/reimbursement/use_cases/test_send_human_review.py:56-64` — `it_renders_four_identical_failures_as_four_entries`
- [x] Broker unreachable at consume time → relies on librdkafka's reconnect: architectural, correctly out of scope for a unit/integration test

---

## Gate Check

### First pass (pre-fix)

- **Gate command (quick)**: `uv run pytest -q -m "not integration"` → **311 passed, 10 deselected**, 0 failed
- **Gate command (full)**: `uv run pytest -q` → **321 passed**, 0 failed

### Re-verification pass (post-fix commit `dba67c4`)

- **Gate command (quick)**: `uv run pytest -q -m "not integration"` → **312 passed, 10 deselected**, 0 failed
- **Gate command (full)**: `uv run pytest -q` → **322 passed**, 0 failed
- **Test count before feature** (tasks.md baseline): 268 passed, 6 deselected (quick) / 274 (full)
- **Test count after feature (final, post-fix)**: 312 passed, 10 deselected (quick) / 322 (full)
- **Delta**: +44 quick-visible tests (+1 over the first pass — the new `it_does_not_escalate_at_exactly_the_retry_ceiling` boundary test; the other two strengthened tests added assertions to existing tests, not new tests); the 4 newly `@pytest.mark.integration`-marked Kafka+Postgres round-trip tests in `test_integration.py` explain the deselected rise from 6 to 10; +48 full-suite tests total
- **Skipped tests**: none observed
- **Failures**: none in either pass — both gates are fully green on the real tree; every failing signal in this validation came from the discrimination sensor's own deliberate, reverted mutations

---

## Fix Plans

All three fix tasks from the first validation pass were closed by fix commit `dba67c4` and independently re-verified this pass. None remain open.

### Fix 1 (CLOSED — `dba67c4`): Retry-ceiling boundary (`retry == MAX_RETRY`) was untested — surviving mutant

- **Root cause** (as diagnosed in the first pass): `test_validation.py`'s retry-ceiling tests (`DescribeRetryCeilingEscalation`) all used `retry=4`; its resolve-path tests all used `retry=0` or `retry=1`. No test constructed `retry=3` and asserted it took the normal resolve path rather than escalation.
- **Fix applied**: `it_does_not_escalate_at_exactly_the_retry_ceiling` added to `DescribeRetryCeilingEscalation` (`src/agent/tests/test_validation.py`) — constructs `retry=3` against a real row, asserts `outcome == MessageOutcome.RESOLVED` and the row's `status` stays `"pending"`.
- **Re-verification**: mutation 2 re-run (`>` → `>=` at `agent/validation.py:81`) now fails this exact test. ✅ Killed.
- **Priority**: was Major (mandatory sensor gate) — now closed.

### Fix 2 (CLOSED — `dba67c4`, minor): AGT-03 ghost-drop test didn't assert "no republish, no failure-log entry"

- **Fix applied**: `it_treats_a_uuid_with_no_row_as_a_ghost_and_drops_it` now asserts `producer.produced == []` (with an explicit `FakeProducer()` passed to `_deps`) and `not any(r.levelno >= logging.CRITICAL for r in caplog.records)`.
- **Re-verification**: spot-check A (spurious `publish`/`failure_log.write` injected into the still-correct ghost branch) fails this test on the new `producer.produced == []` assertion. ✅ Confirmed real, not tautological.
- **Priority**: was Minor — now closed.

### Fix 3 (CLOSED — `dba67c4`, minor): AGT-17 escalated-success test didn't assert "no republish"

- **Fix applied**: `it_escalates_a_real_row_and_returns_escalated` now asserts `producer.produced == []` (with an explicit `FakeProducer()` passed to `_deps`).
- **Re-verification**: spot-check B (spurious `publish` injected into the still-correct escalated-success branch) fails this test on the new assertion. ✅ Confirmed real, not tautological.
- **Priority**: was Minor — now closed.

---

## Requirement Traceability Update

| Requirement ID | Previous Status | New Status |
| --------------- | ---------------- | ----------- |
| AGT-01 | Pending | ✅ Verified |
| AGT-02 | Pending | ✅ Verified |
| AGT-03 | Pending | ✅ Verified |
| AGT-04 | Pending | ✅ Verified |
| AGT-05 | Pending | ✅ Verified |
| AGT-06 | Pending | ✅ Verified |
| AGT-07 | Pending | ✅ Verified |
| AGT-08 | Pending | ✅ Verified |
| AGT-09 | Pending | ✅ Verified |
| AGT-10 | Pending | ✅ Verified |
| AGT-11 | Pending | ✅ Verified |
| AGT-12 | Pending | ✅ Verified |
| AGT-13 | Pending | ✅ Verified |
| AGT-14 | Pending | ✅ Verified (boundary now covered — see Sensor re-verification/Fix 1) |
| AGT-15 | Pending | ✅ Verified |
| AGT-16 | Pending | ✅ Verified |
| AGT-17 | Pending | ✅ Verified |
| AGT-18 | Pending | ✅ Verified |
| AGT-19 | Pending | ✅ Verified |
| AGT-20 | Pending | ✅ Verified |
| AGT-21 | Pending | ✅ Verified |
| AGT-22 | Pending | ✅ Verified |

---

## Summary

**Overall**: ✅ Ready — as of fix commit `dba67c4`, all 22 ACs are fully evidenced, the discrimination sensor kills all 3/3 mutations (including a re-run of the previously-surviving retry-boundary mutation plus two targeted spot-checks proving the strengthened AGT-03/AGT-17 assertions are real, not tautological), and both gates are green with no regressions.

**Spec-anchored check**: 22/22 ACs fully matched the spec-defined outcome; 1 spec-precision note remains (AGT-22's "transaction rollback" wording has no literal DB-transaction referent in this feature's design — the testable half, offset-not-committed, is proven; this is a wording observation, not a gap)

**Sensor**: 3/3 mutations killed (1 re-run of the previously-surviving retry-boundary mutation + 2 spot-checks confirming the strengthened negative-clause assertions are load-bearing)

**Gate**: 312 passed (quick, 0 failed, +1 test over the first pass), 322 passed (full, 0 failed, +1 test) — both green, no regressions, count increase fully explained by the fix commit's added boundary test

**What works**: Everything noted in the first pass, plus: the `retry == MAX_RETRY` boundary is now proven to stay on the resolve path rather than escalating, and the ghost-drop/escalation-success paths are now proven to have zero republish/failure-log side effects, not just the correct primary outcome.

**Issues found**: None remaining. All 3 ranked gaps from the first pass are closed and independently re-verified.

**Next steps**: None — feature is verified and ready. The one remaining spec-precision note (AGT-22's transaction-rollback wording) is informational only and does not block; no fix task is warranted since the feature's design deliberately never wraps a DB transaction here (design.md Tech Decisions).
