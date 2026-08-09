# GET /api/v1/reimbursement/:uuid & PUT Response Payload — Single-Item Detail Specification

## Problem Statement

Today `api` exposes reimbursements only as a list (`GET /api/v1/reimbursement`,
`ReimbursementListItem[]`) and the write path (`PUT /api/v1/reimbursement/:uuid`)
returns a bare `{"msg": "..."}` with no view of the row it just changed — a
caller that wants a single record, or wants to see the result of a decision
it just applied, must re-call the list endpoint and search it client-side.
This spec adds the missing single-item read path and makes the write path
return that same shape post-update, so both routes share one payload
contract for "what does this reimbursement look like right now."

## Goals

- [ ] `GET /api/v1/reimbursement/{uuid}` returns the same per-item shape the
      list endpoint already returns for one element of `data`, as a single
      object rather than an array.
- [ ] `PUT /api/v1/reimbursement/{uuid}` returns that same single-item shape
      on success, reflecting the row's state immediately after the decision
      is committed (not its pre-update values).

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| ------- | ------ |
| Changing the list endpoint (`GET /api/v1/reimbursement`) response shape | Untouched; this feature reuses its existing per-item field set, it doesn't modify it. |
| Changing PUT's non-2xx bodies (`400`/`404`/`413`/`422`/`500`) | User's ask is scoped to the payload PUT returns on success; error bodies keep the existing `{"msg": "..."}` contract app-wide (AD-011). |
| New fields not already on `ReimbursementListItem` | Not requested; both endpoints reuse the exact existing field set (incl. `last_human_review`). |
| Auth / permissions on the new GET endpoint | Project has no auth layer (same precedent as `api-put-reimbursement`). |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here — nothing is left silently
unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| ---------------------- | --------------- | --------- | ---------- |
| `data` shape for a single item | A single object: `{"msg": "...", "data": {...}}`, not a one-element array | User's own framing ("a single result and not an array"); confirmed directly. | y |
| Item field set | Identical to the list endpoint's `ReimbursementListItem` (incl. `last_human_review`) | User's ask is explicitly "the same payload as the GET"; no new fields requested. | y |
| Unknown/well-formed `uuid` on GET | `404` with `{"msg": "..."}` | Mirrors PUT's existing 404 pattern for the same identifier (AD-027 §3); user confirmed the GET response set is 200/404/(400)/500. | y |
| Malformed `uuid` path segment on GET | `400` with `{"msg": "..."}` via the app-wide validation handler, no query run | User confirmed directly; matches how PUT's `uuid: UUID` path param already behaves on malformed input. | y |
| GET success `msg` text | `"reimbursement found"` | No documented text; singularized form of the list endpoint's `"{N} reimbursement(s) found"`. | n |
| PUT freshness guarantee | After the decision is committed, PUT's `data` SHALL reflect the row's state as of that commit — including the just-inserted `human_review` row as `last_human_review` — not pre-update values | Explicit user instruction: "with the most updated data." | y |
| PUT success `msg` text | Stays `"reimbursement decision recorded"`, unchanged; `data` is added alongside it | No reason given to change existing text; minimal-impact default. | n |
| PUT non-2xx responses | Unchanged — still `{"msg": "..."}` only, no `data` | User's ask ("must return the same payload as the GET") reads as the success path only, since GET itself only has a `data` payload on its own success (`200`). | n |

**Open questions:** none — all resolved or logged above.

---

## User Stories

### P1: Retrieve a single reimbursement by uuid ⭐ MVP

**User Story**: As an API consumer, I want to fetch one reimbursement by its
`uuid` so I don't have to page through the list endpoint to find it.

**Why P1**: The endpoint has no value without this — it's the entire
vertical slice, and the PUT story below depends on its payload shape.

**Acceptance Criteria**:

1. WHEN `GET /api/v1/reimbursement/{uuid}` is called with a `uuid` matching
   an existing row THEN the system SHALL return `200` with
   `{"msg": "reimbursement found", "data": {...}}`, where `data` has the
   exact same field set as one element of the list endpoint's `data` array
   (including `last_human_review`, `null` if none exists).
2. WHEN `GET /api/v1/reimbursement/{uuid}` is called with a syntactically
   well-formed `uuid` that matches no row THEN the system SHALL return `404`
   with `{"msg": "..."}`.
3. WHEN `GET /api/v1/reimbursement/{uuid}` is called with a path segment
   that is not a valid `uuid` THEN the system SHALL return `400` with
   `{"msg": "..."}`, and run no query.
4. WHEN the DB pool cannot serve the query (connection failure, timeout)
   THEN the system SHALL return `500` with `{"msg": "..."}`, and log the
   failure — matching the list endpoint's existing pattern.

**Independent Test**: Seed a row with a `human_review` entry and one
without; `GET` each by its `uuid` and assert the returned `data` matches
seeded values, including `last_human_review` being populated or `null`
respectively. `GET` a random, well-formed, non-existent `uuid` and assert
`404`. `GET` a malformed `uuid` string and assert `400`.

---

### P1: PUT returns the updated single-item payload ⭐ MVP

**User Story**: As a reviewer, I want the PUT response to show me the
reimbursement's state right after my decision, so I can confirm it took
effect without a follow-up `GET`.

**Why P1**: Stated as a hard requirement, not an enhancement — the PUT
response today (`{"msg": "..."}` only) gives no visibility into the result.

**Acceptance Criteria**:

1. WHEN `PUT /api/v1/reimbursement/{uuid}` successfully applies an approve
   or reject decision THEN the system SHALL return `200` with
   `{"msg": "reimbursement decision recorded", "data": {...}}`, where `data`
   has the exact same shape as `GET /api/v1/reimbursement/{uuid}`'s `data`,
   built from the row's state after the decision is committed — updated
   `status`, `updated_at`, and the newly inserted `human_review` row as
   `last_human_review`.
2. WHEN PUT's decision is rejected for validation/business reasons (`uuid`
   mismatch, ineligible source status, unknown `uuid`, oversized or
   malformed body) THEN the system SHALL keep returning its existing error
   responses unchanged (`400`/`404`/`413`/`422`) — only the `200` body gains
   `data`.

**Independent Test**: `PUT` an approve decision against a seeded
`human-review` row; assert the response's `data.status` is
`human-approved` and `data.last_human_review` matches the decision just
submitted. Repeat for a reject decision. Assert a subsequent
`GET /api/v1/reimbursement/{uuid}` returns byte-identical `data` to what
the `PUT` response returned.

---

## Edge Cases

- WHEN a reimbursement row has never had a `human_review` row THEN both
  `GET` and `PUT` responses SHALL show `last_human_review: null`.
- WHEN a PUT decision is the row's first-ever `human_review` row THEN the
  PUT response's `last_human_review` SHALL reflect that just-inserted row —
  never `null`.
- WHEN two `GET`s are made back-to-back with no intervening state change
  THEN both SHALL return identical `data`.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| DETAIL-01 | P1: Retrieve by uuid (found → 200) | Design | Verified |
| DETAIL-02 | P1: Retrieve by uuid (well-formed, no match → 404) | Design | Verified |
| DETAIL-03 | P1: Retrieve by uuid (malformed uuid → 400) | Design | Verified |
| DETAIL-04 | P1: Retrieve by uuid (DB failure → 500) | Design | Verified |
| DETAIL-05 | P1: PUT returns updated payload (success → 200 + data) | Design | Verified |
| DETAIL-06 | P1: PUT error responses unchanged | Design | Verified |

**ID format:** `DETAIL-NN`

**Status values:** Pending → In Design → In Tasks → Implementing → Verified

**Coverage:** 6 total, 0 mapped to tasks, 6 unmapped ⚠️ (Tasks phase not run
— scope is Medium, tasks stay implicit in Execute)

---

## Success Criteria

- [ ] A caller can fetch any single reimbursement by `uuid` without paging
      through the list endpoint.
- [ ] A caller can read the outcome of its own `PUT` decision directly from
      that request's response, with no follow-up `GET` required, and that
      response is byte-identical to what a subsequent `GET` would return.
