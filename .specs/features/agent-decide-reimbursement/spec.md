# Agent — Decide `Reimbursement` (Reject / Auto-Approve / Human Review) Specification

## Problem Statement

`agent-consume-reimbursement` (shipped, validated PASS on
`feature/6_reimbursement_consumer`) consumes `Reimbursement`, resolves the
message to its row by `uuid`, applies the staleness guard, and hands off a
fresh, resolved row — but makes **no decision**. Every row it hands off sits
at `pending` forever. `docs/SCOPE.md:247-298` ("Reimbursement Agent") defines
the decision logic that must run next: reject, auto-approve (deterministic
+ probabilistic/LLM layers), or route to human review — the actual business
judgment the whole pipeline (API → publisher → agent) exists to produce.

This feature specifies that decision logic: the business rules for reject,
auto-approve, and human-review classification, the extraction+policy
strategy for fields the payload doesn't guarantee, and the audit-trail
requirement a mission-critical financial decision carries. It does **not**
specify the LangGraph graph shape, node wiring, or any other implementation
detail — the user has an active refactor in flight on
`feature/6_reimbursement_consumer` and asked that this session stay in
business rules until that syncs; Design starts only after that.

## Goals

- [ ] Every `Reimbursement` handed off by the resolve stage reaches exactly
      one terminal or human-review status (`auto-approved`, `auto-rejected`,
      `human-review`) — never stays `pending` indefinitely.
- [ ] The reject rule (receipt older than 90 days) always takes precedence
      over every other rule, including the mandatory `>2000` human-review
      rule.
- [ ] A reimbursement is never auto-approved or auto-rejected on data the
      system could not actually resolve — unresolved value or receipt date
      always means human review, never a guess in either direction.
- [ ] Every decision — automated or pending human review — carries a
      non-null, rule-naming `decision_reason` and an LLM trace (LangFuse,
      file-log fallback) for every LLM step that ran.
- [ ] The common case (a `claimed_amount_brl`-shaped payload, value ≤ 200,
      fresh receipt) costs exactly one LLM call — extraction — never a
      second, guardrail/judge call.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| ------- | ------ |
| Message resolution, staleness guard, resolve-stage retry/republish/escalation | Owned by `agent-consume-reimbursement` (shipped) — this feature starts from an already-resolved, fresh row |
| Decision-stage error-handling **mechanism** (retry-then-escalate vs. immediate escalation vs. cause-differentiated policy) | Deferred — LLM calls are billed, unlike the resolve stage's free DB retries; tracked as **R-011** in `.specs/RISKS.md`, pending a cost/tradeoff evaluation in a later session |
| Specific consistency/guardrail checks the probabilistic judge runs (e.g. category-vs-receipt-text, amount-vs-receipt-text) | User: "leave open ... still unsure how it's gonna work" — this spec requires the check step to exist and gate ambiguous-zone auto-approval, not what it checks |
| LangGraph graph topology, node wiring, state schema, LLM/SLM model selection, prompt text, extraction structured-output schema | Design phase — deliberately deferred until the user's concurrent refactor session syncs |
| `GET` / `PUT /api/v1/reimbursement/:uuid`, the reviewer-facing surface | Separate, already-specified features (`api-get-reimbursement`, `api-put-reimbursement`) |
| Extracting `raw_ocr_text` data for analytics/ETL purposes | `SCOPE.md`'s own Decisions section explicitly excludes this |
| Validating attachments against `raw_ocr_text` | `SCOPE.md`'s own Decisions section explicitly excludes this ("deliberately decided to leave this feature out of scope") |
| Multi-currency support | Approval Policy is BRL-only per this spec's Assumptions |
| Authentication / authorization | `SCOPE.md:295-297` — no auth anywhere in this project |
| Human Review Evaluator (mining human-review gaps to improve the agent) | `SCOPE.md` Phase 2 |
| Load testing, E2E testing infrastructure | `SCOPE.md` Phase 2 |
| Database schema changes | `status`, `decision_reason`, `receipts_value`, `receipts_date`, `currency`, `human_review_notes` already exist (`0001.create-reimbursement.sql`) — this feature needs no new column |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here — nothing is left silently unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | --------------- | --------- | ---------- |
| Field extraction is unconditional, for all three fields, in one call | A single LLM extraction step runs **first, unconditionally, on every reimbursement** — before any deterministic check — and returns the requested value, currency, and receipts date together from the full payload (structured fields + `raw_ocr_text`). The deterministic layer that follows is a pure completeness + policy check on the extraction step's output; `claimed_amount_brl` being directly present does **not** skip this call | Revised after the user updated `reimbursement-processing.png`: the "find required fields" box now runs unconditionally, ahead of the deterministic completeness check, for every field — not gated behind any one field's absence. Asked directly to reconcile this against the earlier deterministic-first-for-value answer, user confirmed: "Unconditional for everything." One combined call (not three separate ones) also better satisfies `SCOPE.md:275`'s "one prompt per Reimbursement" than a per-field call would | y |
| `docs/SCOPE.md` amendment | `docs/SCOPE.md`'s Reimbursement Agent section is amended to document this unconditional upfront extraction step — text amended by this session; `reimbursement-processing.png` was amended by the user directly, already reflected above | User: "this must be updated in the scope document and in the diagram, the scope you can do it, the diagram i'm gonna do it" | y |
| Reject-rule precedence | The 90-day-old-receipt reject rule is evaluated first within the deterministic policy-application step and always wins — an item that would otherwise be mandatory-human-review (`value > 2000`) or auto-approved is rejected instead if its receipt is also stale | User: "Reject always wins" | y |
| Currency / locale | BRL only, always. The extraction step returns currency alongside value/date as one unit of work — whether it internally pre-fills a minimum object from directly-readable fields (e.g. `claimed_amount_brl`) before the LLM call fills in the rest, or resolves everything through the LLM in one pass, is an implementation choice this spec doesn't fix | User: "the probabilistic layer will have a bit of deterministic ... maybe `claimed_amount_brl` will be used or not, it's gonna depend" — confirms the internal composition is undecided, while the business-level contract (one unconditional call, final value/currency/date used as-is by every later step) stands | y |
| `submitted_at` as the reject rule's reference date | `submitted_at` (guaranteed non-null by `POST /api/v1/reimbursement`'s own validation) is the fixed reference date the 90-day window is measured against | User: "the submitted_date is mandatory in the payload" | y |
| Required-fields set (post-extraction) | "All required fields present" means the extraction step successfully resolved a requested value and a receipts date; currency is trivially satisfied once a value resolves, given the BRL-only assumption above | `SCOPE.md:263-269,279-282`'s two field lists collapse to these two once currency is fixed and `request_id`/`submitted_by`/`submitted_at` are already guaranteed by the API | y |
| Date comparison granularity | Calendar-day, UTC | `reimbursement.receipts_date` is a `DATE` column (no time component) — sub-day precision cannot be stored or compared meaningfully | n |
| Threshold boundary semantics | `≤ 200` (inclusive) auto-approve fast path; `200 < value ≤ 2000` ambiguous zone (guardrail-gated); `> 2000` (strict) mandatory human review; `> 90 days` (strict) reject | Literal `SCOPE.md:25-27` wording, each independently reversible | y |
| Guardrail/consistency check specifics | Left open. This spec requires only that a consistency-check step exists and gates whether an ambiguous-zone (`200 < value ≤ 2000`) item can be auto-approved — the exact checks it runs are Design's call | User: "That can leave open, just make sure there is gonna be a node that this processing will happen, it still unsure how it's gonna work" | y |
| Decision-stage error-handling mechanism | Not selected in this spec. Only the invariant "no reimbursement is silently lost" is asserted; retry-vs-immediate-escalate-vs-cause-differentiated is deferred to a later session — see **R-011** | User: "Add this note to RISKS.md ... we must avoid spent unnecessary cost, so another moment is gonna be evaluated" | y |
| `decision_reason` source for human-review outcomes | This spec requires `decision_reason` to always be populated and human-readable for every outcome. Whether it is LLM-generated (a further billed call) or purely deterministic text is bundled into the same R-011 cost evaluation, not decided here | Inferred from the same cost-sensitivity the user raised for error handling — flagged for the user to confirm or correct when reviewing this spec | n |
| Unresolvable `receipts_date` | If the extraction step finds no date anywhere, the reject rule cannot fire (nothing to compare) — the item does **not** default to auto-approved; an unknown receipt date routes to human review, since the "all required fields present" gate fails | The system must never auto-approve without knowing whether the receipt is stale; mirrors SCOPE's own "if it doesn't return enough data ... move to Human Review" principle | y |
| Insufficient value or date never auto-rejects | If extraction cannot resolve the requested value or the receipts date, the outcome is always `human-review`, never `auto-rejected` | Literal `SCOPE.md:273`: "do not rely on it [the LLM] to determine if it's auto-approved or rejected... move to Human Review" — absence of data is not evidence of a stale receipt | y |
| One LLM call for extraction, a second only for the ambiguous zone | Extraction: exactly one call per reimbursement, never batched across items. The guardrail/judge step is a second, separate call, invoked only for `200 < value ≤ 2000` items that clear the reject rule | Literal `SCOPE.md:275`: "One prompt per Reimbursement, to prevent LLM from hallucinating" — read as "not batched with another item," not "exactly one call total," since the diagram shows two distinct LLM steps | y |
| PII minimization in prompts | Every LLM prompt in this feature avoids including PII (e.g. `submitted_by`) beyond what the step operationally needs. Concretely, AGD-01's extraction prompt allow-lists `original_payload` down to `claimed_amount_brl`, `claimed_category`, `raw_ocr_text` — excluding `request_id`/`submitted_by`/`submitted_at`/`attachments`, none of which carry extraction signal. AC1's "full `original_payload`" wording means this allow-listed set, not every payload key verbatim | `SCOPE.md:59` marks this "(?)" (uncertain) in the original doc, but it's a low-cost, defensible default for a financial-domain LLM integration | n |
| Full LangFuse tracing, file-log fallback | Every LLM invocation in this feature is traced via LangFuse; if LangFuse is unreachable, the step falls back to file logging | Literal `SCOPE.md:304`, mirrors the project's already-established `failure_log` fallback pattern | y |
| Multiple date-like values in `raw_ocr_text` | The extraction step is trusted to select the transaction/receipt date (not e.g. a hotel's check-in date) when several dates appear in the OCR text; exact disambiguation logic is a prompt-engineering concern, not a business rule this spec fixes | `sample.json`'s REQ-0003 (hotel) has `CHECK-IN`/`CHECK-OUT` but no explicit "receipt date" label — a genuine, low-risk ambiguity to defer | n |
| Zero/near-zero requested values | No special floor or sanity check beyond the stated thresholds — a `claimed_amount_brl` of `0` still evaluates through the `≤ 200` fast path exactly as any other small value would | No such floor is stated anywhere in `SCOPE.md`; inventing one would be scope creep | n |

**Open questions:** none — every row above is either user-confirmed (`y`) or
a logged, independently-reversible assumption (`n`) with a stated rationale.

---

## User Stories

### P1: Every reimbursement's value, currency, and receipt date resolve in one unconditional extraction pass ⭐ MVP

**User Story**: As the system, I want a single LLM pass to resolve the
requested value, currency, and receipt date together — up front, on every
reimbursement — so no later step has to special-case whether a field came
from a structured payload field or from `raw_ocr_text`.

**Why P1**: `docs/SCOPE.md:247-298`, as clarified by the user's own updated
`reimbursement-processing.png` — the extraction step runs before, and
regardless of, what the deterministic layer would otherwise find.

**Acceptance Criteria**:

1. WHEN a fresh, resolved reimbursement (post-staleness-check, handed off by
   `agent-consume-reimbursement`) enters this feature's flow THEN the system
   SHALL invoke a single LLM extraction step against the full
   `original_payload` (structured fields plus `raw_ocr_text`, when present —
   see the Assumptions table's PII-minimization row: "full" means every
   field this step operationally needs, not every payload key verbatim)
   to resolve the requested value, currency, and `receipts_date` together,
   before any deterministic completeness or policy check runs.
2. WHEN the extraction step returns a field THEN every later step
   (completeness check, policy application, guardrail check) SHALL use that
   value as-is — no later step re-derives it independently from the raw
   payload.
3. WHEN the extraction step runs THEN it SHALL count as exactly one LLM
   invocation for the reimbursement, never batched with another item's
   payload in the same prompt.
4. This step SHALL run unconditionally, even when the payload already
   contains a directly-usable field such as `claimed_amount_brl` — there is
   no field-presence check that skips this call.

**Independent Test**: Feed a `sample.json`-shaped payload (`claimed_amount_brl`
present, date only inside `raw_ocr_text`); assert exactly one extraction
call is made and it returns all three fields. Feed a payload with no
extractable signal for one field; assert the call still happens and returns
the other two.

---

### P1: A receipt older than 90 days is rejected, ahead of every other rule ⭐ MVP

**User Story**: As the system owner, I want a stale receipt rejected
regardless of the claimed amount, so that an old receipt can never be
auto-approved or parked in human review by a rule that runs before the
staleness check.

**Why P1**: `SCOPE.md:257,27` — explicit, and the user confirmed reject
always wins over the `>2000` mandatory-human-review rule.

**Acceptance Criteria**:

1. WHEN a `receipts_date` is resolved AND `submitted_at − receipts_date >
   90 days` (calendar-day granularity, UTC) THEN the system SHALL set the
   reimbursement's `status` to `auto-rejected`, regardless of the requested
   value or any other outcome that would otherwise apply.
2. WHEN a reimbursement is auto-rejected under this rule THEN its
   `decision_reason` SHALL state the rule and both dates
   (`submitted_at`, `receipts_date`) so the rejection is reproducible
   without re-reading the payload.
3. WHEN the deterministic policy-application step evaluates its rules THEN
   the reject rule SHALL be checked first — before the `≤ 200` and
   `> 2000` rules — so that an item qualifying for either is rejected
   instead if its receipt is also stale.
4. WHEN `submitted_at − receipts_date ≤ 90 days`, or `receipts_date` could
   not be resolved THEN the reject rule SHALL NOT fire and evaluation SHALL
   continue to the other policy rules.

**Independent Test**: Resolve a `receipts_date` 91 days before
`submitted_at` on a value of `5000`; assert `auto-rejected` (not
`human-review`) with a `decision_reason` naming both dates. Resolve a date
exactly 90 days before; assert it is NOT rejected by this rule (the
boundary is exclusive — "more than 90 days").

---

### P1: A required field the extraction step could not resolve routes straight to human review ⭐ MVP

**User Story**: As a human reviewer, I want an item the system couldn't
confidently price or date to land in my queue rather than being silently
approved or rejected on missing information.

**Why P1**: `SCOPE.md:273` — explicit instruction not to let the LLM decide
auto-approve/reject from absence of data; mirrors the diagram's "Is all
[required] field[s] present? No →" branch straight to Human Review.

**Acceptance Criteria**:

1. WHEN the extraction step could not resolve a requested value THEN the
   reimbursement SHALL route to `human-review`, never `auto-rejected` and
   never `auto-approved`, with `decision_reason` naming the value as
   unresolved.
2. WHEN the extraction step could not resolve a `receipts_date` THEN the
   reimbursement SHALL route to `human-review` — the reject rule cannot
   affirmatively clear it without a date, so an unknown date is never
   treated as "not stale" — with `decision_reason` naming the date as
   unresolved.
3. WHEN both the requested value and `receipts_date` resolve THEN the
   system SHALL proceed to the deterministic policy-application step
   (reject / `≤200` / `>2000` / ambiguous-zone) — this story's routing does
   not apply once both fields are present.

**Independent Test**: Feed a payload with no extractable value; assert
`human-review` with a reason naming the value, and no reject/auto-approve
attempted. Feed a payload with a resolvable value but nothing extractable
as a date; assert `human-review` with a reason naming the date, never
`auto-approved`.

---

### P1: A value ≤ 200 auto-approves deterministically — no guardrail/judge call ⭐ MVP

**User Story**: As the system owner, I want the common, low-risk case to
skip the second LLM step entirely, so cost and latency stay proportional to
actual risk.

**Why P1**: `SCOPE.md:25` — the deterministic fast path; matches the
diagram's "Can apply? Yes → End" branch, which never reaches the
probabilistic-judge node.

**Acceptance Criteria**:

1. WHEN both required fields resolved AND the reject rule did not fire AND
   the resolved requested value is `≤ 200` THEN the reimbursement SHALL be
   `auto-approved` without invoking the guardrail/judge LLM step.
2. WHEN this rule auto-approves a reimbursement THEN `decision_reason`
   SHALL state the rule and the resolved value.

**Independent Test**: A resolved value of exactly `200` with a fresh
receipt auto-approves with exactly one LLM call recorded for the whole
decision (extraction only — no judge call).

---

### P1: A value > 2000 always routes to human review, decided deterministically, unconditionally ⭐ MVP

**User Story**: As the system owner, I want every large claim seen by a
human, no matter how clean the rest of the data looks, so a probabilistic
layer never single-handedly approves a high-value claim — and so this case,
like the two above, costs no second LLM call either.

**Why P1**: `SCOPE.md:26` — "hard-requirement."

**Acceptance Criteria**:

1. WHEN both required fields resolved AND the reject rule did not fire AND
   the resolved requested value is `> 2000` THEN the reimbursement SHALL
   route to `human-review`, decided by the deterministic policy step alone
   — the guardrail/judge LLM step SHALL NOT be invoked for this case.
2. No combination of guardrail confidence or deterministic signal SHALL
   override this rule.
3. WHEN this rule routes a reimbursement to `human-review` THEN
   `decision_reason` SHALL state the rule and the resolved value.

**Independent Test**: A resolved value of `2000.01` with a fresh receipt
routes to `human-review` with exactly one LLM call recorded (extraction
only).

---

### P1: A consistency/guardrail check gates auto-approval only for the ambiguous 200–2000 zone ⭐ MVP

**User Story**: As the system owner, I want a value in the `200`–`2000`
range checked for internal consistency before it's auto-approved, so the
probabilistic layer never rubber-stamps a contradictory claim — and so this
second LLM call is spent only where the deterministic rules genuinely can't
decide.

**Why P1**: `SCOPE.md:271-284`'s "LLM as Judge" requirement; the user
confirmed the check must exist even though its specific rules stay open.

**Acceptance Criteria**:

1. WHEN both required fields resolved AND the reject rule did not fire AND
   the resolved requested value falls in `200 < value ≤ 2000` THEN the
   system SHALL invoke a second, separate LLM step — the guardrail/judge —
   to assess whether the resolved data is internally consistent enough to
   auto-approve.
2. WHEN the guardrail check finds the resolved data internally consistent
   THEN the reimbursement SHALL be `auto-approved`.
3. WHEN the guardrail check finds the data contradictory, or cannot reach a
   confident verdict THEN the reimbursement SHALL route to `human-review`.
4. The specific checks the guardrail step runs are explicitly **not**
   fixed by this spec (see Assumptions) — only that the step exists and
   gates this outcome, and only for this value range.

**Independent Test**: Using a stubbed guardrail verdict on a value of
`1000`, assert a "consistent" result auto-approves and a "contradictory"
result routes to `human-review` — independent of what the guardrail
actually checks. Assert the guardrail step is invoked exactly once, after
exactly one prior extraction call.

---

### P1: Every decision, automated or pending, is explained and traceable ⭐ MVP

**User Story**: As an auditor, I want every decision this feature makes to
be reproducible from stored data and trace logs alone, so a financial
decision driven partly by an LLM can survive an audit.

**Why P1**: `SCOPE.md:19-21` — "full traceability of all operations is
imperative to facilitate auditing and reproducibility."

**Acceptance Criteria**:

1. WHEN any terminal or human-review status is written (`auto-approved`,
   `auto-rejected`, `human-review`) THEN `decision_reason` SHALL be
   non-null and SHALL name the rule(s) or check(s) that produced the
   outcome.
2. WHEN the deterministic layer reaches a terminal outcome — whether
   decided on its own (reject, `≤200` auto-approve, `>2000` human-review)
   or handed a verdict by the guardrail/judge step (auto-approve or
   human-review) — THEN persisting `status` and `decision_reason` to the
   `reimbursement` row SHALL be that outcome's own last action before the
   flow ends, for every outcome, with no path that reaches an end state
   without first persisting it.
3. WHEN any step in this feature invokes an LLM (extraction, or the
   guardrail/judge) THEN the invocation SHALL be recorded via LangFuse
   tracing.
4. WHEN LangFuse is unreachable THEN the invocation SHALL fall back to file
   logging instead — mirroring the project's existing `failure_log`
   fallback pattern — never silently untraced.
5. WHEN a decision is later audited THEN it SHALL be reconstructable from
   `decision_reason` plus the LangFuse/file trace alone, with no need to
   re-run the LLM.

**Independent Test**: Trigger each outcome (reject, `≤200` auto-approve,
guardrail-gated auto-approve, `>2000` human-review, missing-field
human-review) and assert each writes a non-null `decision_reason` and a
trace entry for every LLM call that ran.

---

### P1: A decision-stage failure never silently loses a reimbursement ⭐ MVP

**User Story**: As the system owner, I want a failure during decision-making
to never leave a reimbursement stuck or vanished, even though I haven't yet
decided exactly how retries should work for a billed LLM call.

**Why P1**: Mission-critical, no-loss guarantee (`SCOPE.md:19-21`) — but the
*mechanism* is intentionally deferred (see Assumptions, **R-011**).

**Acceptance Criteria**:

1. WHEN any step in this feature's flow fails (DB error, LLM error,
   malformed structured output from either LLM step) THEN the reimbursement
   SHALL NOT be left in `pending` indefinitely and SHALL NOT be silently
   dropped — it SHALL eventually reach a terminal or human-review status,
   or a durable failure-log entry.
2. The specific mechanism (retry-then-escalate, immediate escalation, or a
   cause-differentiated policy) is **not** selected by this spec — see
   **R-011** in `.specs/RISKS.md`.
3. Design MUST NOT silently default to reusing the resolve-stage's full
   retry-then-republish machinery for LLM-calling steps without a
   follow-up confirmation with the user, per R-011.

**Independent Test**: Deferred to Design/Tasks, once R-011 resolves — this
story's AC1 (the invariant) is testable now; AC2/AC3 gate what Design is
allowed to assume.

---

### P2: LLM prompts minimize PII exposure

**User Story**: As the system owner, I want prompts sent to a third-party
LLM to carry as little personal data as operationally necessary, so a
financial-domain integration doesn't leak more PII than it must.

**Why P2**: `SCOPE.md:59` marks this uncertain ("(?)") in the original doc —
treated as a defensible default, not a hard-blocking MVP gate.

**Acceptance Criteria**:

1. WHEN a prompt is constructed for either LLM step in this feature (the
   extraction pass or the guardrail/judge) THEN it SHALL avoid including
   PII fields (e.g. `submitted_by`) beyond what that step's task
   operationally requires.

---

## Edge Cases

- WHEN a resolved value is exactly `2000` THEN it SHALL NOT be forced to
  `human-review` by the mandatory rule (`> 2000` is strict) — it falls in
  the ambiguous, guardrail-gated zone instead.
- WHEN `raw_ocr_text` contains multiple date-like values (e.g. a hotel
  receipt's `CHECK-IN`/`CHECK-OUT`, `sample.json` REQ-0003) THEN the
  extraction step is trusted to select the transaction/receipt date — exact
  disambiguation is a prompt-engineering concern, not a business rule this
  spec fixes (see Assumptions).
- WHEN `claimed_amount_brl` is present but the extraction step's own read
  of `raw_ocr_text` implies a different total THEN that discrepancy is
  exactly the kind of contradiction the (intentionally open-specified)
  guardrail/judge step exists to catch when it runs — this spec does not
  add a separate rule for it.
- WHEN `claimed_amount_brl` is present but `0` or otherwise minimal THEN no
  special floor applies — it evaluates through the `≤ 200` fast path like
  any other small value, since no such floor is stated anywhere in scope.
- WHEN a reimbursement was already `stale`-ignored or `ghost`-dropped by
  `agent-consume-reimbursement` THEN it never reaches this feature at all —
  that boundary belongs entirely to the resolve stage.
- WHEN the reject rule and the mandatory `>2000` human-review rule would
  both apply to the same item (an old, large receipt) THEN reject wins —
  the item is `auto-rejected`, not routed to `human-review`.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ------ | ------- |
| AGD-01 | P1: Extraction (single unconditional call, before any check) | Design | Pending |
| AGD-02 | P1: Extraction (output used as-is by every later step) | Design | Pending |
| AGD-03 | P1: Extraction (one LLM call per reimbursement, never batched) | Design | Pending |
| AGD-04 | P1: Extraction (unconditional even if claimed_amount_brl present) | Design | Pending |
| AGD-05 | P1: Reject rule (>90 days → auto-rejected, overrides all) | Design | Pending |
| AGD-06 | P1: Reject rule (decision_reason states both dates) | Design | Pending |
| AGD-07 | P1: Reject rule (checked first, ahead of ≤200/>2000) | Design | Pending |
| AGD-08 | P1: Reject rule (≤90 days or unresolved date → continue) | Design | Pending |
| AGD-09 | P1: Missing field (value unresolved → human-review, never rejected/approved) | Design | Pending |
| AGD-10 | P1: Missing field (date unresolved → human-review, never approved) | Design | Pending |
| AGD-11 | P1: Missing field (both resolved → proceed to policy application) | Design | Pending |
| AGD-12 | P1: ≤200 fast path (auto-approve, no guardrail call) | Design | Pending |
| AGD-13 | P1: ≤200 fast path (decision_reason states rule + value) | Design | Pending |
| AGD-14 | P1: >2000 mandatory human-review (unconditional, no guardrail call) | Design | Pending |
| AGD-15 | P1: >2000 mandatory human-review (overrides any guardrail outcome) | Design | Pending |
| AGD-16 | P1: >2000 mandatory human-review (decision_reason states rule + value) | Design | Pending |
| AGD-17 | P1: Guardrail (invoked only for 200<value≤2000, second LLM call) | Design | Pending |
| AGD-18 | P1: Guardrail (consistent → auto-approved) | Design | Pending |
| AGD-19 | P1: Guardrail (contradictory/unsure → human-review) | Design | Pending |
| AGD-20 | P1: Guardrail (specific checks intentionally unspecified) | Design | Pending |
| AGD-21 | P1: Traceability (decision_reason non-null on every outcome) | Design | Pending |
| AGD-22 | P1: Traceability (persisting status+decision_reason is every outcome's own last action) | Design | Pending |
| AGD-23 | P1: Traceability (LangFuse trace on every LLM invocation) | Design | Pending |
| AGD-24 | P1: Traceability (file-log fallback if LangFuse unreachable) | Design | Pending |
| AGD-25 | P1: No-loss invariant on decision-stage failure | Design | Pending |
| AGD-26 | P2: PII minimization in prompts | Design | Pending |

**Coverage:** 26 total, 0 mapped to tasks, 26 unmapped ⚠️ (Specify complete;
Discuss folded into this session's clarifying rounds, including a
diagram-driven correction — see Assumptions; Design intentionally not
started, per user request, until the concurrent refactor session syncs)

---

## Success Criteria

- [ ] A `sample.json`-shaped item (`claimed_amount_brl` present, value
      `≤ 200`, receipt within 90 days) auto-approves after exactly one LLM
      call (extraction) — no guardrail/judge call.
- [ ] A receipt dated more than 90 days before submission is always
      rejected, regardless of amount — including amounts `> 2000`.
- [ ] An amount `> 2000` always lands in `human-review`, decided
      deterministically, with no second LLM call.
- [ ] An item whose value or receipt date cannot be resolved by extraction
      lands in `human-review` — never silently dropped, never guessed into
      `auto-approved` or `auto-rejected`.
- [ ] Every `auto-approved`, `auto-rejected`, or `human-review` outcome
      carries a non-null, rule-naming `decision_reason`.
- [ ] Every LLM invocation for a reimbursement is traceable via LangFuse or,
      on its unavailability, a file log.
