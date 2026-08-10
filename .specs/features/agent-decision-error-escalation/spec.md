# Agent Decision Error Escalation Specification

## Problem Statement

When the Reimbursement Agent's LangGraph decision graph (`agent.decide()`)
fails for any reason — a Groq/LLM call error, a malformed structured-output
response (e.g. the model returning a date string where `ExtractedFieldsSchema`
requires `null`), or a DB write failure inside a node — the row is only
written to `failure_log` today (a CRITICAL log line) and left exactly where
it was, indefinitely. Nothing routes it anywhere a human can act on it. This
is `agent-decide-reimbursement`'s own deferred gap (AGD-25 / **R-011** in
`.specs/STATE.md`): the invariant ("never silently lost") was asserted, but
the mechanism was explicitly left unselected pending a cost evaluation. This
spec selects and implements that mechanism: **immediate escalation to
`human-review`**, with the error captured in `decision_reason` so a reviewer
can act on it without re-running anything.

## Goals

- [ ] Every decision-stage failure (any exception raised during
      `agent.decide()`, including errors from the LLM-calling nodes) moves
      the affected row to `human-review` instead of leaving it `pending`
      with only a log line.
- [ ] `decision_reason` on that escalation is human-actionable: it states
      what failed, using the project's existing attempt-history rendering,
      not a bare stack trace or a redacted stub.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| ------- | ------ |
| Retrying the LLM call before escalating | R-011's mechanism choice (this session, via Discuss): immediate escalation, not retry-then-escalate — LLM calls are billed, unlike the resolve stage's free DB retries |
| Cause-differentiated handling (LLM failure vs. DB failure vs. malformed output treated differently) | Rejected in Discuss — every decision-stage exception escalates the same way, regardless of cause |
| New redaction/PII-stripping logic for the error message | Declined in Discuss — reuses the existing AD-014 precedent (full detail confined to the DB row / failure_log, sanitized only on stdout) rather than inventing new rules |
| Changing `_escalate`'s (retry-ceiling, `retry > 3`) own logic or its `ESCALATION_FAILED_EVENT` fallback | Unrelated code path, untouched by this feature |
| New database columns or schema changes | `human_review.reason` / `reimbursement.decision_reason` already exist and are already written by `apply_decision`/`escalate_existing` |
| Rate-limiting or cost controls on LLM calls | Moot — no retry is introduced |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here — nothing is left silently unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | --------------- | --------- | ---------- |
| `failure_log` write stays, escalation is additive | Both fire on every decision-stage failure; escalation does not replace the CRITICAL log line | User: keep both — failure_log remains the ops-alerting signal, independent of the review workflow | y |
| `decision_reason` combines prior resolve-stage retry history with the new decision-stage error | Reuse `render_history`/`AttemptError`, appending one new entry tagged with a new `Stage = "decide"` value, to the existing `envelope.errors` | User: full attempt history, not just the last error — mirrors why `AttemptError` travels on the envelope at all (AD-014) | y |
| Raw error detail in `decision_reason` | Full, unsanitized `str(exc)` — same trust-boundary treatment AD-014 already gives `AttemptError.message`. stdout logging (`sanitize(exc)`) is unchanged | User: reuse the AD-014 precedent rather than build new redaction | y |
| Scope of "any processing error" | Escalates uniformly, including failures after a decision was computed in-memory but before it was persisted (e.g. `apply_policies`/`apply_agent_decision`'s own write throwing) — the escalation overwrites the unpersisted decision | User: a write that never durably completed is never trusted as final, regardless of what was computed | y |
| New `Stage` literal value name (`"decide"`) | Agent's choice, matches existing `resolve`/`publish`/`db-insert` naming | Naming only, no behavioral ambiguity — see context.md Agent's Discretion | n |
| Escalation-write-itself-fails / ghost-row handling | Mirrors `_escalate`'s existing behavior exactly: `escalate_existing` returning `None` (ghost) or raising falls back to `failure_log` only, outcome `LOGGED` — no new retry of the escalation itself | Consistency with the only other place this codebase already escalates to human-review; inventing a different fallback here would be an unjustified second pattern | n |

**Open questions:** none — all resolved above (four via Discuss, two as
low-risk agent defaults).

---

## User Stories

### P1: Decision-stage failures escalate to human-review with full error context ⭐ MVP

**User Story**: As the system (on behalf of a human reviewer), when the
decision graph fails to reach or persist a decision for a reimbursement, I
want the row moved to `human-review` with the error captured in
`decision_reason`, so a mission-critical financial decision is never stuck
in `pending` with nothing but a log line to explain why.

**Why P1**: Closes R-011 — the last unresolved invariant gap
`agent-decide-reimbursement` (AGD-25) explicitly deferred. Without this, a
decision-stage failure is invisible to the reviewer workflow entirely.

**Acceptance Criteria**:

1. WHEN `agent.decide()` raises any exception for a resolved, non-stale
   reimbursement THEN the system SHALL escalate that row to `human-review`
   status using the same write path the retry-ceiling escalation already
   uses (`apply_decision` via `escalate_existing`).
2. WHEN the escalation in AC1 succeeds THEN `decision_reason` SHALL be
   `render_history`'s rendering of `[*envelope.errors, <new entry>]`, where
   `<new entry>` is an `AttemptError` for this failure with `stage="decide"`
   and `message=str(exc)` (unsanitized) — the same "attempt N at TIMESTAMP
   [stage] error_type: message" format `_escalate` already produces, never a
   bare exception string and never a placeholder/summary.
3. WHEN the escalation in AC1 succeeds THEN the existing `failure_log`
   CRITICAL write (`DECISION_FAILED_EVENT`) SHALL still occur, unchanged —
   both records exist independently; neither write is conditional on the
   other's success.
4. WHEN the row targeted by escalation no longer exists (a ghost — deleted
   between `_resolve`'s read and `_decide`'s write) THEN escalation SHALL
   affect zero rows, no further escalation attempt SHALL be made, and the
   outcome SHALL fall back to the existing `failure_log`-only path — mirrors
   `_escalate`'s own ghost handling exactly.
5. WHEN the escalation write itself fails (e.g. the database is unreachable)
   THEN that failure SHALL also be recorded to `failure_log`, and the
   outcome SHALL be the existing `LOGGED` terminal state — no exception
   propagates out of `_decide`.
6. WHEN a decision (`status`/`decision_reason`) was already computed in
   graph state but the exception occurred during the node's own persist
   call THEN escalation SHALL still overwrite with `human-review` and the
   error-based reason from AC2 — the computed-but-unpersisted decision is
   discarded, never trusted as final.
7. WHEN escalation succeeds (AC1-3) THEN `_decide` SHALL return
   `MessageOutcome.ESCALATED` (the same enum value `_escalate` already uses
   for the retry-ceiling path); WHEN it does not (AC4 or AC5) THEN `_decide`
   SHALL return `MessageOutcome.LOGGED`, exactly as it does today.
8. WHEN this escalation path logs to stdout THEN the log line SHALL
   continue using `sanitize(exc)` (type + safe diagnostic only) — never the
   raw `str(exc)` that AC2 permits inside `decision_reason`.

**Independent Test**: Force `agent.decide()` to raise inside `_decide`
(e.g. a fake graph that raises on `ainvoke`), then assert: the row's
`status` becomes `human-review` and `decision_reason` contains the
exception's message; `failure_log.write` was still called with
`DECISION_FAILED_EVENT`; the returned `MessageOutcome` is `ESCALATED`. A
second case with a ghost uuid asserts `MessageOutcome.LOGGED` and no row
mutation.

---

## Edge Cases

- WHEN `envelope.errors` is empty (no prior resolve-stage retries — the
  common case) THEN the rendered `decision_reason` SHALL still contain
  exactly one attempt entry (this decision-stage failure) — `render_history`'s
  empty-list placeholder text (`_NO_HISTORY`) SHALL never appear on this
  path, since the list passed to it always has at least the new entry.
- WHEN the exception's `str(exc)` contains embedded newlines (e.g. a
  multi-line error) THEN it SHALL be collapsed to a single line by the
  existing `_one_line()` step inside `render_history` — prevents a forged
  extra "attempt N ..." line, exactly as it already does for resolve-stage
  errors.
- WHEN `str(exc)` exceeds `max_message_chars` THEN it SHALL be truncated
  exactly as every other `AttemptError.message` already is via
  `render_history`'s existing truncation.

---

## Requirement Traceability

Each requirement gets a unique ID for tracking across design, tasks, and validation.

| Requirement ID | Story | Phase | Status |
| -------------- | ----------- | ------ | ------- |
| ADE-01 | P1: Decision-stage failure escalates to human-review (AC1) | Implement | Pending |
| ADE-02 | P1: `decision_reason` is full attempt history, unsanitized, `stage="decide"` (AC2) | Implement | Pending |
| ADE-03 | P1: `failure_log` write still occurs, independently (AC3) | Implement | Pending |
| ADE-04 | P1: Ghost row → no escalation, falls back to `failure_log` (AC4) | Implement | Pending |
| ADE-05 | P1: Escalation write failure → `failure_log`, outcome `LOGGED` (AC5) | Implement | Pending |
| ADE-06 | P1: Post-decision persist failure still escalates, overwrites (AC6) | Implement | Pending |
| ADE-07 | P1: `MessageOutcome.ESCALATED` on success, `LOGGED` otherwise (AC7) | Implement | Pending |
| ADE-08 | P1: stdout logging stays `sanitize(exc)`, never raw (AC8) | Implement | Pending |

**ID format:** `ADE-[NUMBER]`

**Status values:** Pending → In Design → In Tasks → Implementing → Verified

**Coverage:** 8 total, 0 mapped to tasks, 8 unmapped ⚠️ (Medium scope — Design
and Tasks fold inline into Execute per the auto-sizing table; no separate
`design.md`/`tasks.md` planned)

---

## Success Criteria

How we know the feature is successful:

- [ ] A decision-stage exception (LLM failure, malformed structured output,
      or a node's own DB write failure) always leaves the row at
      `human-review`, never stuck at `pending` with no actionable trace.
- [ ] A reviewer reading `decision_reason` on such a row can tell, without
      re-running anything, what failed and (if applicable) whether it was a
      repeat of an earlier resolve-stage failure.
- [ ] Zero regressions: the retry-ceiling escalation path (`_escalate`),
      `failure_log`'s existing behavior, and stdout log sanitization are
      byte-for-byte unchanged for every case this feature doesn't touch.
