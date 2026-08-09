# PUT /api/v1/reimbursement/:uuid Context

**Gathered:** 2026-08-08
**Spec:** `.specs/features/api-put-reimbursement/spec.md`
**Status:** Ready for design

---

## Feature Boundary

`PUT /api/v1/reimbursement/:uuid` — a reviewer's approve/reject decision on
a `reimbursement` row currently in `human-rejected`, `auto-rejected`, or
`human-review`. Writes a new append-only `human_review` row and updates
`reimbursement.status` (plus, on approval, the `receipts_*` columns) in one
transaction.

---

## Implementation Decisions

### Status eligibility (AD-027 §1)

- All three documented statuses — `human-rejected`, `auto-rejected`,
  `human-review` — are PUT-eligible, exactly as `SCOPE.md`'s API section
  states.
- `SCOPE.md`'s Decisions-section line "not possible to Human-Approve if
  rejected" was internally contradictory as written; resolved to mean the
  already-*approved* statuses (`auto-approved`/`human-approved`) — money
  already disbursed can't be clawed back, which doesn't describe a rejected
  item. `docs/SCOPE.md` amended in place with this clarification.

### Review-write semantics (AD-027 §2)

- "Do not override the existing record, create a new one" refers to
  `human_review` (already DB-enforced append-only via the
  `human_review_append_only` trigger). PUT inserts a new `human_review` row
  per decision.
- `reimbursement.status` (and, on approval, `receipts_value`/
  `receipts_date`/`receipts_currency`) is updated **in place** — no second
  `reimbursement` row is ever created.

### 404 semantics (AD-027 §3, shared with GET)

- `PUT` returns `404` for an unknown path `uuid` — SCOPE.md's own PUT
  Responses list omitted this despite operating on a path parameter; added
  here since it's the natural place for it (GET's list endpoint, by
  contrast, never 404s).
- A `uuid` that exists but isn't in an eligible status is `400`, not `404`
  — the resource exists, the transition doesn't.

### Approval completeness gate (AD-027 §4)

- No extra application-level gate beyond: (a) the DB's own CHECK
  constraints, and (b) the approval payload's own required fields
  (`receipts_date`/`receipts_value`/`receipts_currency`/`reason`/
  `approved_by`), which is what backfills those columns when they're NULL
  on a `human-review` row (expected, not an error state).
- `submitted_by`/`submitted_at` — the two identity columns the PUT payload
  has no field to supply — are **not** gated at approval either. They're
  already guaranteed non-null by `POST /api/v1/reimbursement`'s own
  validation (`shared.models.ReimbursementRequest` requires both), so no
  row reaching this endpoint can be missing them.

### Reject completeness gate (AD-027 addendum)

- Unlike approval, a reject request supplies no `receipts_value`/
  `receipts_date`/`receipts_currency` — so those three columns must
  **already** be non-null on the row, or the reject is blocked with `400`
  and no DB change.
- **User-directed follow-up correction** to the original AD-027 §4
  discussion: that resolution covered approval's gate only. Rejecting
  doesn't backfill the three fields the way approving does, so leaving it
  ungated would let a `human-rejected` row stay permanently incomplete.
- **Known, accepted dead end:** a `human-review` row that never had these
  three fields extracted can only be *approved* (fresh values via its own
  payload) — it can never be rejected through this endpoint until a future
  feature adds an independent way to correct them.

### Agent's Discretion

- Body `uuid` vs. path `uuid` consistency check (400 on mismatch) — not
  documented; the only reading that gives the payload's `uuid` field a
  purpose without contradicting the path as canonical identifier.
- Concurrency: single atomic `UPDATE ... WHERE uuid = $1 AND status =
  ANY($2)`, loser sees 0 rows affected → same `400` as any other
  ineligible-status attempt. Not documented; follows the transaction
  discipline already established for the publisher (AD-017).
- Reject payload's `receipts_*` fields, if supplied anyway: ignored, not
  validated, not written.

### Declined / Undiscussed Gray Areas → Assumptions

None declined — the four core gray areas were resolved directly with the
user and recorded as `AD-027`. The remaining lower-stakes items above
(body/path uuid consistency, concurrency mechanics, extra reject fields,
success-body shape) were never presented as discussion questions — logged
straight to `spec.md`'s Assumptions table per the closure gate.

---

## Specific References

None — no product reference given; the shape follows `SCOPE.md`'s
documented payload/response bodies and the existing `human_review`/
`reimbursement` schema constraints.

---

## Deferred Ideas

- Re-running the Agent's 90-day / >2000 rules at approval time — explicitly
  a human override, not a re-run of automated policy; belongs to the Agent
  feature if ever revisited.
- Notifying anyone of the decision — not documented, out of scope.
