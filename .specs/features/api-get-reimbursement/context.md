# GET /api/v1/reimbursement Context

**Gathered:** 2026-08-08
**Spec:** `.specs/features/api-get-reimbursement/spec.md`
**Status:** Ready for design

---

## Feature Boundary

`GET /api/v1/reimbursement` — a paginated, status-filterable list of
`reimbursement` rows, each enriched with its most recent `human_review`
entry if one exists. First DB read path in `api`; `api` gains a connection
pool wired the same way the Kafka producer already is.

---

## Implementation Decisions

### 404 semantics (shared with PUT — see AD-027)

- The list endpoint never returns `404`. A `status`/pagination combination
  that matches zero rows is `200` with `data: []`.
- `docs/SCOPE.md`'s originally-documented `404` for this endpoint was a spec
  error — amended in place, see `AD-027` in `.specs/STATE.md`.

### Multi-status filter (AD-028)

- `status` accepts a comma-separated list of the five client-facing values
  (`status=human-review,auto-rejected`), matched with `IN (...)` — not just
  a single value as originally documented.
- Any invalid segment (including `pending`) invalidates the whole filter,
  same `400` as the single-value case. A repeated param
  (`?status=a&status=b`) is still `400` — only the comma-joined single-param
  form is accepted.
- `docs/SCOPE.md:157-163` amended in place with this clarification.

### Agent's Discretion

- `limit` upper bound (500, `400` above it) — not documented; capped so one
  request can't force an unbounded scan/serialization. Logged as an
  assumption in `spec.md`, not asked — a technical bound with no user-facing
  tradeoff to weigh.
- Sort order (`created_at DESC`) — the only ordering the existing DB index
  (`reimbursement_status_created_idx`) was built to serve; no alternative
  was ever on the table.
- Response item shape (full `reimbursement` row + `last_human_review: {...}
  | null`) — the only shape that satisfies `SCOPE.md:162`'s "return the last
  Human Review if any."

### Declined / Undiscussed Gray Areas → Assumptions

None declined — the four gray areas raised during Discuss (status
eligibility, review-write semantics, 404 trigger, approval completeness
gate) were all resolved directly with the user and recorded as `AD-027`.
The remaining lower-stakes items above (limit ceiling, sort order, response
shape) were never presented as discussion questions — they're technical
defaults with no product-visible tradeoff, logged straight to `spec.md`'s
Assumptions table per the closure gate.

---

## Specific References

None — no product reference was given; the shape follows `SCOPE.md`'s own
documented response body (`{"msg": "", "data": [...]}`) and the existing DB
index built for this exact query.

---

## Deferred Ideas

- Single-item `GET /api/v1/reimbursement/:uuid` — not documented in scope;
  would be a natural follow-up but is out of this feature's boundary.
- Full-text search over `original_payload` — not documented, out of scope.
