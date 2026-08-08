# SCOPE.md Gap Analysis

Author: Claude (review only — `docs/SCOPE.md` is authored by Flavio Studart and is not modified by this file).

Source of truth: `docs/original/Requirements.pdf`. Compared against `docs/SCOPE.md` as of commit `f431f5d`.

Purpose: a requirement-traceability check, to be worked through manually (or via `/grill-me`) — not an instruction to auto-apply anything.

## 1. Structural gaps (requirement stated in PDF, not represented as a scope item)

| # | PDF requirement | Location in PDF | Current state in SCOPE.md | Gap |
|---|---|---|---|---|
| 1 | Deliverable: install/test/run instructions | p.3, Deliverables | Not present anywhere | No scope item commits to producing a README/runbook |
| 2 | Deliverable: written design-choices explanation mapped to **both** functional and non-functional requirements | p.3, Deliverables | `Decisions` section (`SCOPE.md:278-311`) exists but isn't framed as satisfying this, and doesn't map back to specific functional/non-functional requirements | Explanation exists in spirit but isn't structured to demonstrably close this deliverable |
| 3 | Deliverable: written explanation of compromises/trade-offs **made due to time constraints** | p.3, Deliverables | `Decisions` touches on some scope cuts (e.g. `SCOPE.md:308` attachments-validation) but never frames them as time-constraint trade-offs | Not explicitly captured as a deliverable to produce |
| 4 | Instruction: provide a rough time estimate upon completion | p.1, Instructions | Not present | No placeholder/reminder for this anywhere in scope |
| 5 | Baseline policy: "You may extend it, but must not weaken it" | p.2, Baseline Approval Policy | Not stated | The three thresholds are copied, but this governing constraint on any future change to them is missing |
| 6 | "State explicitly any assumptions about locale, currency, date format, and how these thresholds interact with other validation outcomes" | p.2, directly under Baseline Approval Policy | Currency assumption appears once, incidentally, under the probabilistic layer (`SCOPE.md:237`, "Hardcoded as BRL") | Locale and date-format assumptions are absent entirely; the interaction between the reject-rule and the two amount thresholds is only implied by section ordering (`SCOPE.md:223-226` vs. `256-259`), never stated as an explicit precedence rule |

## 2. Functional-requirement traceability (audit trail / justification)

| # | PDF requirement | Location in PDF | Current state in SCOPE.md | Gap |
|---|---|---|---|---|
| 7 | "which must be rejected, **with proper justification** for each decision" — applies to all three outcomes | p.1, Objectives | `Human Review` entity has a `Reason` field (`SCOPE.md:102`) | `Reimbursement` entity has only a bare `Decision` field (`SCOPE.md:77`), with no clear field to record *why* the deterministic/probabilistic layer auto-approved or auto-rejected |
| 8 | "Audit Trail: every decision... must be traceable" | p.2, Functional Requirements | `Reimbursement` schema (`SCOPE.md:61-81`) | `Receipts Date` is not a schema field at all — yet it's the input that the reject rule, the human-review gate, and both auto-approval layers reason over. Without persisting it, the decision that was made isn't reconstructable from stored data. Note `Reciepets Value` is listed twice (`SCOPE.md:74-75`) — this looks like the second occurrence was meant to be `Receipts Date` |

## 3. Internal inconsistencies (not PDF-derived, but block a clean implementation)

| # | Location | Issue |
|---|---|---|
| 9 | `SCOPE.md:256-259` | Human Review rule lists `Receipt Date must be newer than 90 days` as a sub-bullet under the `>2000` gate — but `SCOPE.md:223-225` already says any receipt older than 90 days is instantly rejected. As written, this sub-bullet is either dead logic or the ordering/precedence between "reject" and "human review" needs to be stated explicitly (ties back to gap #6) |
| 10 | `SCOPE.md:150` | GET filter status enum: `` auto-approved \| human-approved \| human-review \| auto-rejected \| human-approved `` — `human-approved` is listed twice; `human-rejected` is missing entirely |
| 11 | `SCOPE.md:182` | PUT payload example: `"receipts_currency": "yyyy-mm-dd"` — a date-format placeholder was pasted into the currency field; should be something like `"BRL"` |
| 12 | `SCOPE.md:299` | Decisions section references a `Layer` field on the `Reimbursement` entity ("represents if the status was defined by the Deterministic or Probabilistic layer") but this field isn't listed in the schema (`SCOPE.md:61-81`) |
| 13 | `SCOPE.md:298` | Truncated bullet: `- It's possib` — incomplete sentence, needs finishing or removing |
| 14 | `SCOPE.md:85` | "Unique constraint agains Request ID / submited By" — ambiguous: a composite unique constraint on (Request ID, Submitted By) together, or each unique independently? Worth stating explicitly since it affects idempotency handling referenced later (`SCOPE.md:311`) |
| 15 | `SCOPE.md:66-70` vs `SCOPE.md:98-100` | `Reimbursement.Status` and `Human Review.Status` are two separate enums with overlapping vocabulary (`Human-Approved`/`Human Review`/`Human-Rejected` vs. `Approved`/`Rejected`). Likely intentional (one is the aggregate state, one is the review outcome) — worth confirming explicitly since it's easy to conflate during implementation |

## 4. Minor / cosmetic (fix in a proofread pass, not a requirements gap)

- `SCOPE.md:19` — "missions-critical" → "mission-critical"
- `SCOPE.md:21` — "the service rives monetary decisions" → "drives"
- `SCOPE.md:27` — "submittions date" → "submission date"
- `SCOPE.md:170` — "recheable" → "reachable"
- `SCOPE.md:244` — "contraditory" → "contradictory"
- `SCOPE.md:293` — "Started with eh assumption" → "the assumption"
- `SCOPE.md:311` — "idepotent" → "idempotent"
- "Submited"/"submited" used consistently in place of "Submitted"/"submitted" throughout (e.g. `SCOPE.md:72, 88, 92, 231, 233, 235`) — confirm whether this is an intentional field-naming convention (it does match `submitted_by`/`submitted_at` from the actual payload... no, those use double-t) or a typo to fix globally

## 5. Confirmed as matching (no action needed)

- Objectives / problem statement — `SCOPE.md:1-6` matches p.1 closely.
- Functional requirements (all 5 bullets) — `SCOPE.md:10-15` matches p.2 near verbatim.
- Non-functional requirements (mission-critical, financial domain, traceability given probabilistic/LLM outputs) — `SCOPE.md:17-21` matches p.2-3 in substance.
- The three baseline approval-policy numeric thresholds themselves (200 / 2000 / 90 days) — `SCOPE.md:25-27` match p.2 exactly.
- Sample dataset reference and content — `docs/original/sample.json` matches the PDF's sample dataset (p.3-4) exactly; `SCOPE.md:30-32` correctly points to it.
- Technical stack choices (FastAPI, PostgreSQL, Kafka, etc.) — the PDF leaves all technical choices beyond Python fully at your discretion (p.3), so nothing here is a gap by definition.
