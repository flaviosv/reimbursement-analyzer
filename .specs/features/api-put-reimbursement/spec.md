# PUT /api/v1/reimbursement/:uuid — Human Review Decision Specification

## Problem Statement

`docs/SCOPE.md:179-221` requires a way for a human reviewer to approve or
reject a `reimbursement` currently sitting in `human-review`,
`auto-rejected`, or `human-rejected`, recording the decision as an
immutable `human_review` row and propagating the final status onto the
`reimbursement` row itself. No such write path exists today —
`shared.reimbursement.repository` only has `insert_pending`/
`insert_human_review` (both insert-only, both used by the publisher's
ingestion path); nothing updates a `reimbursement` row or inserts a
`human_review` row from the API side. `AD-027` (`.specs/STATE.md`) resolved
four internal contradictions in `SCOPE.md`'s description of this endpoint
before this spec could be written.

## Goals

- [ ] `PUT /api/v1/reimbursement/:uuid` accepts an approve-or-reject
      decision, validates it against the target row's current status and
      the payload's required-fields-per-decision rule, and applies it
      atomically.
- [ ] Every accepted decision inserts exactly one new `human_review` row
      (append-only, per the existing DB trigger) and updates
      `reimbursement.status` — plus, on approval, the `receipts_*` columns
      — in the same transaction.
- [ ] Every response matches the app-wide `{"msg": "..."}` contract,
      including the newly-added `404` for an unknown `uuid` (AD-027 §3).
- [ ] Two concurrent PUTs on the same `uuid` never both succeed — the loser
      observes the row as already transitioned and gets the same `400` any
      other ineligible-status attempt gets.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| ------- | ------ |
| Authentication / deriving `approved_by` from a JWT | `SCOPE.md`'s own Decisions section states this project has no auth layer; `approved_by` stays a plain-text email field, same treatment as `reviewed_by`/`submitted_by` elsewhere. |
| Re-applying the Agent's 90-day-reject or >2000-review rules at approval time | Those are Agent-side classification rules (`SCOPE.md:262,281`); a human reviewer approving here is a deliberate override, not a re-run of automated policy. |
| Notifying anyone of the decision | Not documented. |
| Any status transition other than into `human-approved`/`human-rejected` | `SCOPE.md:180-189` only documents approve/reject from the three source statuses. |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here — nothing is left silently
unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| ---------------------- | --------------- | --------- | ---------- |
| PUT source-status eligibility | `human-rejected`, `auto-rejected`, `human-review` — all three | AD-027 §1 — resolved directly with the user. | y |
| "Create a new one" meaning | New `human_review` row; `reimbursement.status` updated in place | AD-027 §2 — resolved directly with the user. | y |
| Unknown `uuid` | `404` | AD-027 §3 — resolved directly with the user. | y |
| Approval field completeness gate | No extra gate beyond DB constraints + the payload's own required-for-approval fields; `submitted_by`/`submitted_at` being NULL is never a block condition (guaranteed non-null by `POST`'s own validation) | AD-027 §4 — resolved directly with the user. | y |
| Body `uuid` vs. path `uuid` | Path is canonical; if the body's `uuid` field is present, it must equal the path `uuid` or the request is `400` | Not documented; the payload's `"uuid": ""` field is otherwise dead weight duplicating the path — treating it as a consistency check is the only reading that gives it a purpose without contradicting the path being the resource identifier. | n |
| Concurrent PUTs on the same `uuid` | Single atomic `UPDATE ... WHERE uuid = $1 AND status = ANY($2) RETURNING ...`; a losing request sees 0 rows updated and returns the same `400` an already-ineligible-status attempt gets | Not documented; a read-then-write would race under concurrent requests. Follows the same transaction discipline already established for the publisher (AD-017) — the `WHERE status IN (...)` clause is what makes "already transitioned" and "never was eligible" indistinguishable to the caller, which is correct: both mean the same thing by the time this request executes. | n |
| Reject payload's `receipts_*` fields, if supplied anyway | Ignored — not written, not validated | `SCOPE.md:198` only requires `reason`+`approved_by` for reject; nothing says extra fields are rejected. | n |
| Reject field completeness gate | Blocked with `400` unless `receipts_value`/`receipts_date`/`receipts_currency` are **already** non-null on the row; no DB change on block | User directive. Reject's payload has no field to supply these three (unlike approve, which does) — without this gate a rejected row could stay permanently incomplete. Known accepted consequence: a `human-review` row that never had these three extracted can only be resolved by *approving* it, never rejecting it, until a future feature adds an independent way to correct them. | y |
| `approved_by` format | Validated as a well-formed email (mirrors `reviewed_by`'s existing DB `CHECK` in `human_review`, and `submitted_by`'s in `reimbursement`) | SCOPE.md's Human Review schema section requires "Review By must be valid email"; consistent with two existing CHECK constraints. | y — taken directly from SCOPE.md + schema |
| Success (`200`) body | `{"msg": "..."}` | AD-011's app-wide contract; `POST`'s own success response already returns `{"msg"}`, not just its error paths. | n |

**Open questions:** none — all resolved above or during Discuss (AD-027).

---

## User Stories

### P1: Approve a rejected or human-review reimbursement ⭐ MVP

**User Story**: As a reviewer, I want to approve a claim currently in
`human-review`, `auto-rejected`, or `human-rejected` so the correct final
decision is recorded.

**Why P1**: The core value of the endpoint — recovering from an automated
misclassification or completing a manual review.

**Acceptance Criteria**:

1. WHEN `PUT /:uuid` is called with `status: "approved"` and all payload
   fields (`reason`, `receipts_date`, `receipts_value`,
   `receipts_currency`, `approved_by`) present, on a row whose current
   status is `human-rejected`, `auto-rejected`, or `human-review` THEN the
   system SHALL, in one transaction: insert a new `human_review` row
   (`status: approved`, `reviewed_by: approved_by`, `reason`) and update
   `reimbursement.status` to `human-approved`, `receipts_value`,
   `receipts_date`, `receipts_currency` from the payload, and `updated_at`
   — returning `200`.
2. WHEN any required approval field is missing THEN the system SHALL
   return `422` (`SCOPE.md:212`) with `{"msg": "..."}` naming the missing
   field, and make no DB change.
3. WHEN `receipts_date`/`receipts_value`/`receipts_currency` fail the same
   format rules the DB already enforces (`yyyy-mm-dd`, non-negative,
   `^[A-Z]{3}$`) THEN the system SHALL return `422` (same class as AC2 —
   the payload failed its own shape contract) and make no DB change.
4. WHEN the target row's current status is not one of the three eligible
   statuses (e.g. already `human-approved`) THEN the system SHALL return
   `400` — a well-formed request the target row's state doesn't allow —
   and make no DB change.

**Independent Test**: Seed a `human-review` row, PUT an approval, assert
`200`, the `reimbursement` row's new status/fields, and exactly one new
`human_review` row.

---

### P1: Reject a reimbursement under review ⭐ MVP

**User Story**: As a reviewer, I want to reject a claim currently in
`human-review` (or re-reject an `auto-rejected`/`human-rejected` one) with a
reason, without needing the full field set approval requires.

**Why P1**: Equally core — half of every review decision is a rejection.

**Acceptance Criteria**:

1. WHEN `PUT /:uuid` is called with `status: "rejected"`, `reason`, and
   `approved_by` present, on a row in one of the three eligible statuses
   **and whose `receipts_value`, `receipts_date`, and `receipts_currency`
   are already all non-null** THEN the system SHALL, in one transaction:
   insert a new `human_review` row (`status: rejected`) and update
   `reimbursement.status` to `human-rejected` — returning `200`, without
   requiring `receipts_date`/`receipts_value`/`receipts_currency` in the
   *payload* (the row's existing values are left untouched).
2. WHEN `reason` or `approved_by` is missing on a reject request THEN the
   system SHALL return `422`.
3. WHEN the target row's `receipts_value`, `receipts_date`, or
   `receipts_currency` is NULL THEN the system SHALL return `400` and make
   no DB change — the reject payload has no field to supply them, so a
   finalized (`human-rejected`) reimbursement must never be left with
   incomplete receipt data.

**Independent Test**: Seed a `human-review` row with all three receipt
fields populated, PUT a rejection with only `reason`+`approved_by`, assert
`200` and the resulting status/human_review row. Separately, seed a
`human-review` row with `receipts_value` NULL, PUT the same rejection,
assert `400` and no new `human_review` row.

---

### P1: Unknown or ineligible uuid ⭐ MVP

**User Story**: As an API consumer, I want a clear, distinct response when
the target doesn't exist versus when it exists but can't be reviewed right
now.

**Why P1**: State-transition integrity — silently no-op'ing either case
would hide a real error from the caller.

**Acceptance Criteria**:

1. WHEN the path `uuid` does not match any `reimbursement` row THEN the
   system SHALL return `404`.
2. WHEN the path `uuid` matches a row whose status is not one of the three
   eligible statuses THEN the system SHALL return `400` — never `404`, the
   resource exists.

**Independent Test**: PUT a random uuid → assert `404`; PUT a real uuid
whose status is `pending` → assert `400`.

---

### P2: Concurrent decisions on the same uuid

**User Story**: As the system, I must guarantee only one of two
simultaneous review decisions on the same claim ever wins, so a claim is
never double-approved or left in a contradictory state.

**Why P2**: Correctness bar under load, not the common single-reviewer
path.

**Acceptance Criteria**:

1. WHEN two PUT requests for the same `uuid` are issued concurrently and
   both target an eligible status THEN the system SHALL apply exactly one
   of them; the other SHALL observe the row as no longer eligible and
   receive `400`.

**Independent Test**: Fire two concurrent PUT calls against one seeded row;
assert exactly one `200` and one `400`, and exactly one new `human_review`
row exists afterward.

---

## Edge Cases

- WHEN the body's `uuid` field is present and differs from the path `uuid`
  THEN the system SHALL return `400`.
- WHEN `status` is any value other than `"approved"`/`"rejected"` THEN the
  system SHALL return `422`.
- WHEN the DB transaction fails after the `human_review` insert but before
  the `reimbursement` update (or vice versa) THEN the system SHALL roll
  back both — the row must never end up with a new `human_review` entry
  whose decision isn't reflected in `reimbursement.status`, or vice versa.
- WHEN a `human-review` row has never had `receipts_value`/`receipts_date`/
  `receipts_currency` extracted (a legitimate state — see this feature's
  Assumptions table) THEN it can be *approved* (whose payload supplies
  fresh values) but never *rejected* through this endpoint — a known,
  accepted dead end until a future feature adds an independent way to
  correct those fields.

---

## Requirement Traceability

Each requirement gets a unique ID for tracking across design, tasks, and
validation.

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| REVIEW-01 | P1: Approve (happy path) | Design | Pending |
| REVIEW-02 | P1: Approve (missing field → 422) | Design | Pending |
| REVIEW-03 | P1: Approve (invalid field format → 400) | Design | Pending |
| REVIEW-04 | P1: Approve (ineligible status → 400) | Design | Pending |
| REVIEW-05 | P1: Reject (happy path) | Design | Pending |
| REVIEW-06 | P1: Reject (missing reason/approved_by → 422) | Design | Pending |
| REVIEW-07 | P1: Unknown uuid → 404 | Design | Pending |
| REVIEW-08 | P1: Ineligible status → 400, not 404 | Design | Pending |
| REVIEW-09 | P2: Concurrent decisions — exactly one wins | Design | Pending |
| REVIEW-10 | P1: Reject blocked when receipt fields incomplete → 400 | Design | Pending |

**ID format:** `REVIEW-NN`

**Status values:** Pending → In Design → In Tasks → Implementing → Verified

**Coverage:** 10 total, 0 mapped to tasks, 10 unmapped ⚠️ (Tasks phase not
yet run)

---

## Success Criteria

- [ ] Every reviewable claim (`human-review`/`auto-rejected`/
      `human-rejected`) can be moved to a final `human-approved`/
      `human-rejected` state through exactly one call, atomically, with a
      permanent audit row.
- [ ] No decision is ever lost or double-applied under concurrent requests.
