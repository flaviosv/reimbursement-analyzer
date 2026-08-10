# Agent Decision Error Escalation Context

**Gathered:** 2026-08-10
**Spec:** `.specs/features/agent-decision-error-escalation/spec.md`
**Status:** Ready for implementation (Medium scope — design folded inline)

---

## Feature Boundary

When the reimbursement decision graph (`agent.decide()`) raises for any
reason — an LLM call failure, a malformed structured-output response (the
`ExtractedFieldsSchema` validation error that triggered this feature), a DB
write failure inside a node — the affected row is escalated to
`human-review` immediately, with `decision_reason` carrying the error detail.
This resolves **R-011** (`agent-decide-reimbursement`'s AGD-25, deferred
decision-stage error-handling mechanism): the mechanism selected is
**immediate escalation**, not retry-then-escalate and not a
cause-differentiated policy.

---

## Implementation Decisions

### Escalation vs. failure_log

- Both fire, unconditionally. The existing `failure_log` CRITICAL write is
  unchanged — it stays the ops-alerting signal. The new `human-review`
  escalation is added alongside it, not instead of it.

### `decision_reason` content

- Full attempt history, not just the new error. Any pre-existing
  `envelope.errors` (resolve-stage retries already carried on the message,
  AD-014) are combined with a new entry for this decision-stage failure and
  rendered through the existing `render_history`/`AttemptError` machinery —
  same "attempt N at TIMESTAMP [stage] error_type: message" format the
  retry-ceiling escalation (`_escalate`) already produces. A reviewer sees
  the whole story, not just the last failure.
- This requires one small, additive change: `Stage` (`shared/models.py`)
  gains a `"decide"` value alongside the existing `"db-insert"`, `"publish"`,
  `"resolve"`.

### PII / raw error detail

- Reuses the AD-014 precedent as-is: `decision_reason` (a DB row, inside the
  same trust boundary as `original_payload`) carries the **full,
  unsanitized** `str(exc)` — exactly like `AttemptError.message` already
  does for resolve-stage retries. No new redaction logic.
- stdout logging is unchanged: `_decide`'s `logger.error(...)` keeps using
  `sanitize(exc)` (type + safe diagnostic only). This split — full detail in
  the DB/failure_log, sanitized only on stdout — already exists in the
  codebase; this feature does not touch it.

### Scope of "any processing error"

- Escalates uniformly. This includes failures that happen *after* a decision
  was already computed in-memory but *before* it was durably persisted (e.g.
  `apply_policies`/`apply_agent_decision`'s own DB write throwing). In that
  case the escalation overwrites the computed-but-unpersisted `status`/
  `decision_reason` with `human-review` + the error — a write that never
  durably completed is never trusted as the final decision, regardless of
  what was computed in memory.

### Agent's Discretion

- The new `Stage` literal value's name (`"decide"`) — naming only, no
  behavioral ambiguity; chosen to match the existing `resolve`/`publish`/
  `db-insert` naming convention.
- Whether `_decide`'s except block calls `escalate_existing` directly or via
  a small local wrapper — pure code organization, not a spec-visible
  behavior.

### Declined / Undiscussed Gray Areas → Assumptions

- None declined — all four gray areas identified during Discuss were
  resolved above. See spec.md's Assumptions & Open Questions table for the
  formal record.

---

## Specific References

- The triggering error (from the user's own logs), used as the concrete
  worked example throughout the spec:
  `CRITICAL:reimbursementanalyzer.failures:{"event": "reimbursement.decision_failed", ...,
  "error": "Error code: 400 - {'error': {'message': \"Tool call validation
  failed: ... parameters for tool ExtractedFieldsSchema did not match
  schema: errors: [\`/receipts_date\`: '10/08/2026' is not valid 'date' ...`
- `STATE.md` AD-014 (full error detail confined to DB/failure_log, sanitized
  only on stdout) is the direct precedent this feature reuses rather than
  reinventing.
- `agent-decide-reimbursement/spec.md`'s AGD-25 and its Assumptions row
  ("Decision-stage error-handling mechanism") is the deferred decision this
  feature resolves.

---

## Deferred Ideas

- None — discussion stayed within feature scope. (Cost-bounded LLM retry,
  cause-differentiated handling, and any redesign of the resolve-stage
  retry/requeue machinery remain explicitly out of scope, per R-011's own
  reasoning: LLM calls are billed, and this session's decision is immediate
  escalation, not retry.)
