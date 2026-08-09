# GET /api/v1/reimbursement — Listing & Filtering Specification

## Problem Statement

`docs/SCOPE.md:149-176` requires a paginated, filterable read endpoint over
the `reimbursement` table so reviewers and downstream tooling can retrieve
claims by status, but today `api` exposes no read path at all —
`shared.reimbursement.repository` (built for `publisher-consume-request`)
only has `insert_pending`/`insert_human_review`; there is no query function,
and `api`'s own `main.py`/`dependencies.py` have never opened a DB
connection pool. This feature delivers the read path: a connection pool
wired into `api`, a query against `reimbursement` joined to its most recent
`human_review` row, and pagination/filtering per the documented contract.

## Goals

- [ ] `GET /api/v1/reimbursement` returns a paginated list of `reimbursement`
      rows, each enriched with its most recent `human_review` entry if one
      exists (`SCOPE.md:162`).
- [ ] `limit`/`offset` pagination with documented defaults (100/0), and an
      optional `status` filter restricted to the five client-facing values.
- [ ] Every response matches the app-wide `{"msg": "...", "data": [...]}`
      contract already established by `POST /api/v1/reimbursement` and
      `errors.py`.
- [ ] `api` gains its first DB connection pool, following the exact
      `managed_pool`/lifespan pattern the publisher already established
      (AD-017), rather than inventing a second one.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| ------- | ------ |
| Single-item `GET /api/v1/reimbursement/:uuid` | Not documented in `SCOPE.md:149-176`; only the list shape with query-param pagination/filter is specified. |
| Enforcing the 90-day / >2000 decision rules on returned rows | Those are Agent-side classification rules (`SCOPE.md:262,281`), not a read-endpoint concern. |
| Sorting other than `created_at DESC` | Not documented; the existing `reimbursement_status_created_idx` index was built specifically to serve `created_at DESC` (`ARCHITECTURE.md:84`). |
| Full-text / free-form search over `original_payload` | Not documented; only status filtering is specified. |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here — nothing is left silently
unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| ---------------------- | --------------- | --------- | ---------- |
| List endpoint's `404` | Dropped — `200` + empty `data` array for a zero-row match | AD-027 §3 (`.specs/STATE.md`) — resolved directly with the user; SCOPE.md's original `404` for this endpoint was a spec error, amended in place. | y |
| `limit` upper bound | Capped at 500; `400` above it | Not documented. An unbounded `limit` lets one request force a full-table scan and serialize arbitrarily many rows. 500 matches the project's one other documented list-size ceiling (`MAX_BATCH_ITEMS`, AD-013) purely for consistency — the two aren't semantically related. | n |
| `limit`/`offset` below 0, or non-integer | `400` via the app-wide validation handler | Standard input-bounds handling; no documented alternative. | n |
| Invalid `status` filter value (not one of the 5) | `400` | Matches `errors.py`'s existing `RequestValidationError` → `400` contract; `SCOPE.md:165` (pre-amendment numbering) already lists `400` as a documented response. | n |
| Multiple `status` values | Comma-separated in one param (`status=human-review,auto-rejected`), matched with `IN (...)`; repeating the param (`?status=a&status=b`) stays `400` | AD-028 (`.specs/STATE.md`) — user decision, so a reviewer can pull several statuses in one call. | y |
| Sort order | `created_at DESC` | The only ordering `SCOPE.md` implies via "pagination," and the only one the existing DB index (`reimbursement_status_created_idx`) was built to serve. | n |
| Response item shape | Every `reimbursement` column, plus `last_human_review: {status, reviewed_by, reason, created_at} \| null` | `SCOPE.md:162` ("Return the last Human Review if any") requires the enrichment; no other shape is documented. | n |
| Success body | `{"msg": "...", "data": [...]}` | `SCOPE.md:170-176` shows this exact shape for the `200` body. | y — taken directly from SCOPE.md |

**Open questions:** none — all resolved above or during Discuss (AD-027).

---

## User Stories

### P1: List reimbursements with pagination ⭐ MVP

**User Story**: As a reviewer/operator, I want to page through reimbursement
records so I can find claims to act on.

**Why P1**: The endpoint has no value without base pagination — it's the
entire vertical slice.

**Acceptance Criteria**:

1. WHEN `GET /api/v1/reimbursement` is called with no query params THEN the
   system SHALL return `200` with up to 100 rows ordered `created_at DESC`,
   starting at offset 0.
2. WHEN `limit` and/or `offset` are supplied as valid non-negative integers
   THEN the system SHALL apply them to the query (`LIMIT`/`OFFSET`).
3. WHEN `limit` exceeds 500, or `limit`/`offset` is negative or non-integer
   THEN the system SHALL return `400` with `{"msg": "..."}` via the
   app-wide validation handler, and run no query.
4. WHEN no rows exist for the given `limit`/`offset`/`status` combination
   THEN the system SHALL return `200` with `data: []` — never `404`.

**Independent Test**: Seed N rows, call with varying `limit`/`offset`,
assert the returned slice and ordering match what was seeded.

---

### P1: Filter by status ⭐ MVP

**User Story**: As a reviewer, I want to filter by status — one or several
at once — so I can see only, e.g., `human-review` items, or every rejected
one in a single call.

**Why P1**: `SCOPE.md:157-163` documents this as a required filter, not an
enhancement.

**Acceptance Criteria**:

1. WHEN `status` is one of `auto-approved | human-approved | human-review |
   auto-rejected | human-rejected` THEN the system SHALL return only rows
   with that status.
2. WHEN `status` is a comma-separated list of two or more of those five
   values (e.g. `status=human-review,auto-rejected`) THEN the system SHALL
   return rows matching any of the listed statuses (AD-028).
3. WHEN `status` is omitted THEN the system SHALL return rows of every
   status, including the internal-only `pending` status.
4. WHEN `status` (or any comma-separated segment of it) is outside the five
   client-facing values — including `pending` itself — THEN the system
   SHALL return `400`, rejecting the whole filter rather than dropping the
   invalid segment.
5. WHEN the `status` query param is repeated (`?status=a&status=b`) rather
   than comma-joined THEN the system SHALL return `400` — only the
   single-param comma-separated form is accepted.

**Independent Test**: Seed rows across multiple statuses; filter by one,
assert only matching rows return; filter by two comma-separated values,
assert rows of both statuses return and no others; request
`status=pending` and assert `400`; request `status=human-review,pending`
and assert `400` (one invalid segment invalidates the whole filter).

---

### P1: Enrich each row with its last human review ⭐ MVP

**User Story**: As a reviewer, I want to see the most recent human decision
on a claim without a second call.

**Why P1**: `SCOPE.md:162` states this directly as part of the endpoint's
contract.

**Acceptance Criteria**:

1. WHEN a `reimbursement` row has one or more `human_review` rows THEN the
   system SHALL include the most recent one (by `created_at DESC`) as
   `last_human_review` in that item.
2. WHEN a `reimbursement` row has no `human_review` rows THEN the system
   SHALL return `last_human_review: null` for that item.

**Independent Test**: Seed a reimbursement with two `human_review` rows at
different timestamps; assert only the newer one appears in the response.

---

### P2: Uniform error handling on DB failure

**User Story**: As an API consumer, I want DB failures to surface the same
`{"msg"}` shape as every other endpoint, so client error handling doesn't
special-case this route.

**Why P2**: Correctness bar, not the MVP path — a healthy DB is the common
case.

**Acceptance Criteria**:

1. WHEN the DB pool cannot serve the query (connection failure, timeout)
   THEN the system SHALL return `500` with `{"msg": "..."}`, and log the
   failure (matching `_unhandled_exception_handler`'s existing pattern).

**Independent Test**: Force a pool/connection failure via a test double,
assert `500 {"msg"}` and a logged error.

---

## Edge Cases

- WHEN `offset` is beyond the total row count THEN the system SHALL return
  `200` with `data: []`.
- WHEN `status=pending` is requested THEN the system SHALL return `400` —
  `pending` is an internal-only status (AD-003), never client-facing.
- WHEN the `status` query param is repeated THEN the system SHALL return
  `400` rather than silently taking the last/first value.
- WHEN the same status appears twice in the comma-separated list (e.g.
  `status=human-review,human-review`) THEN the system SHALL treat it as the
  single-value case — no error, no duplicate rows.

---

## Requirement Traceability

Each requirement gets a unique ID for tracking across design, tasks, and
validation.

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| LIST-01 | P1: List with pagination (defaults) | Design | Pending |
| LIST-02 | P1: List with pagination (bounds → 400) | Design | Pending |
| LIST-03 | P1: List with pagination (empty → 200) | Design | Pending |
| LIST-04 | P1: Filter by status (single value) | Design | Pending |
| LIST-05 | P1: Filter by status (comma-separated multiple values) | Design | Pending |
| LIST-06 | P1: Filter by status (omitted → incl. pending) | Design | Pending |
| LIST-07 | P1: Filter by status (invalid segment → 400) | Design | Pending |
| LIST-08 | P1: Filter by status (repeated param → 400) | Design | Pending |
| LIST-09 | P1: Enrich with last human review (present) | Design | Pending |
| LIST-10 | P1: Enrich with last human review (absent → null) | Design | Pending |
| LIST-11 | P2: Uniform 500 on DB failure | Design | Pending |

**ID format:** `LIST-NN`

**Status values:** Pending → In Design → In Tasks → Implementing → Verified

**Coverage:** 11 total, 0 mapped to tasks, 11 unmapped ⚠️ (Tasks phase not
yet run)

---

## Success Criteria

- [ ] A reviewer can retrieve any status subset of reimbursements, paginated,
      in one call, each item carrying its latest review decision.
- [ ] Zero `500`s attributable to unbounded queries (limit ceiling enforced)
      or malformed filters (`400`, not a crash).
