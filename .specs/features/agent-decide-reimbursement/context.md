# Agent — Decide `Reimbursement` Context

**Gathered:** 2026-08-08
**Spec:** `.specs/features/agent-decide-reimbursement/spec.md`
**Status:** Ready for user confirmation of spec.md (Design intentionally on hold)

---

## Feature Boundary

The `agent` service's decision logic: given a fresh, resolved reimbursement
row (handed off by the already-shipped `agent-consume-reimbursement`),
resolve the receipt date and requested value, apply the reject / auto-approve
/ human-review business rules from `docs/SCOPE.md:247-298`, and record a
traceable `decision_reason` for every outcome. **No LangGraph graph shape,
node wiring, LLM model pick, or prompt text** — the user has a refactor
session active on `feature/6_reimbursement_consumer` and asked that this
stay business-rules-only until that syncs; Design is deliberately deferred.

---

## Implementation Decisions

### Field extraction — unconditional, one combined call

- **Superseded once during this session** — see the correction note below.
  Final shape: a single LLM extraction step resolves the requested value,
  currency, and `receipts_date` **together, unconditionally, on every
  reimbursement** — before any deterministic completeness or policy check.
  It is never skipped, even when `claimed_amount_brl` is directly present.
- Reason: no sample payload carries a structured receipt-date field — it
  only ever appears inside `raw_ocr_text` — and the user's own updated
  `reimbursement-processing.png` diagram shows the "find required fields"
  box running unconditionally, ahead of the deterministic completeness
  check, for every field (not just date). Asked directly to reconcile this
  against an earlier deterministic-first-for-value answer, the user
  confirmed: "Unconditional for everything."
- **Internal composition still open.** After the diagram review, the user
  added: "the probabilistic layer will have a bit of deterministic, i'm
  gonna build the minimum object to create a Reimbursement and then run the
  LLM to get the other values ... maybe `claimed_amount_brl` will be used
  or not, it's gonna depend." So the extraction *step* is business-level
  fixed (one call, unconditional, all three fields resolve through it) —
  but whether it internally pre-fills a draft object from directly-readable
  fields before calling the LLM, or resolves everything through the LLM in
  one pass, is explicitly undecided and left to Design.
- `docs/SCOPE.md`'s Reimbursement Agent section and
  `reimbursement-processing.png` diagram are both amended to reflect this:
  the text by this session, the diagram by the user directly (already
  done).

### Correction during this session

- The first pass at this context (and `spec.md`) described a narrower
  shape: a dedicated date-only extraction node, with value/currency
  staying deterministic-first (`claimed_amount_brl` skips the LLM when
  present). That was based on the Q&A answers alone, before the user
  posted their updated diagram. The diagram showed the LLM step running
  unconditionally for *all* fields, not gated behind any one field's
  absence — surfaced back to the user rather than silently reconciled, and
  corrected to the "Field extraction" shape above once confirmed.

### Approval Policy interaction

- The 90-day-old-receipt reject rule is evaluated first, within the
  deterministic policy-application step, and always wins — it overrides
  even the mandatory `>2000` human-review rule. An old, large-value receipt
  is `auto-rejected`, not routed to human review.
- Currency is BRL-only, resolved through the same unconditional extraction
  step as value and date (not a separate deterministic-only path).
- `submitted_at` (already guaranteed non-null by
  `POST /api/v1/reimbursement`) is the fixed reference date the 90-day
  window is measured against.
- Once both required fields (value, date) resolve, the deterministic
  policy step decides `≤200` auto-approve, `>2000` human-review, or the
  ambiguous `200–2000` zone entirely on its own — no second LLM call for
  the first two. Only the ambiguous zone invokes the guardrail/judge step,
  a second, separate LLM call.

### Guardrail / consistency check scope

- Left open by the user: "just make sure there is gonna be a node that
  this processing will happen, it still unsure how it's gonna work."
- This spec commits only to the *existence* of a consistency-check step
  that gates whether an ambiguous-zone (`200 < value ≤ 2000`) item can be
  auto-approved — not to which specific contradictions it checks for
  (`SCOPE.md`'s own two named examples — category-vs-receipt-text,
  amount-vs-receipt-text — are noted as candidates, not committed
  requirements).

### Decision-stage error handling & cost sensitivity

- The user redirected this entire question to `.specs/RISKS.md` rather
  than resolving it here: "we must avoid spent unnecessary cost, so
  another moment is gonna be evaluated." Recorded as **R-011**.
- This spec asserts only the invariant that a decision-stage failure never
  silently loses a reimbursement — the actual mechanism (reuse the
  resolve stage's retry-then-escalate machinery as-is, split by cause, or
  something else) is explicitly **not** decided here, because retrying a
  billed LLM call has a cost dimension the already-shipped resolve-stage
  retries (free DB queries) don't.
- Whether a human-review `decision_reason` is LLM-generated (a further
  billed call) for every outcome or only where `SCOPE.md` names it
  (`value > 2000`) is bundled into the same open question — this is the
  agent's own inference from the user's cost framing, flagged in the spec
  for explicit confirmation rather than assumed silently.

### Agent's Discretion

- The exact LangFuse trace shape (spans per node vs. one trace per
  reimbursement) — Design's call.
- The specific prompt wording and PII-redaction technique — Design's call,
  bounded only by the spec's "avoid unnecessary PII" requirement.
- How the guardrail check's "cannot reach a confident verdict" case is
  detected mechanically (a confidence score, a forced binary field,
  something else) — Design's call.

### Declined / Undiscussed Gray Areas → Assumptions

All four gray areas raised were discussed directly with the user (see
spec.md's Assumptions & Open Questions table for the complete resolved
list, including the smaller items — date-comparison granularity, boundary
inclusivity, multiple-dates-in-OCR-text disambiguation, zero-value
handling — that were logged as reversible defaults rather than raised as
separate questions).

---

## Specific References

- `docs/original/sample.json` — grounds the "no structured receipt date
  field, ever" finding that drove the receipt-date-extraction decision.
- `docs/assets/reimbursement-processing.png` — the existing diagram; user
  is amending it directly to add the new upfront extraction node.
- `.specs/features/agent-consume-reimbursement/` — the predecessor feature
  this one hands off from; its `Stage`/`AttemptError`/`MessageOutcome`
  machinery is the candidate (not yet committed) reuse target for R-011.

---

## Deferred Ideas

- Decision-stage error-handling mechanism and cost-aware retry policy —
  **R-011** in `.specs/RISKS.md`, explicit follow-up session.
- Guardrail/consistency-check specifics — Design phase, once the user's
  refactor session syncs.
- LangGraph implementation (graph shape, node wiring, model selection) —
  Design phase, deliberately not started this session.
