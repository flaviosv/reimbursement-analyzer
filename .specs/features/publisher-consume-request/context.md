# Publisher — Consume `Request`, Produce `Reimbursement` — Context

**Gathered:** 2026-08-07
**Spec:** `.specs/features/publisher-consume-request/spec.md`
**Status:** Ready for design

---

## Feature Boundary

The `publisher` service (`src/publisher/`): consume the `Request` Kafka topic
(written by `POST /api/v1/reimbursement`, see `api-post-reimbursement`),
create one `reimbursement` DB row per item, and publish one message per item
to the `Reimbursement` topic for the Agent to consume. No decision logic (the
Agent's job), no schema changes (the migrations already exist), no
`GET`/`PUT` endpoints, no consumption of the `Reimbursement` topic.

---

## Implementation Decisions

### Unit of work

- **Per-item, fully independent.** One `Request`-topic message can carry N
  items (`RequestEnvelope.payload`); each item gets its own DB transaction,
  its own row, its own publish to `Reimbursement`, and — from the moment it
  first fails — its own retry counter.
- A failure in item 2 of 3 never touches items 1 or 3. Once an item is
  committed and published, no later retry ever revisits it, because retries
  re-wrap only the failed item, not the original batch.
- The original `Request`-topic message's Kafka offset is committed once every
  item in it has been *handled* (committed, or re-queued as its own new
  message) — never once every item has *succeeded*. The original message is
  never re-read from `Request` a second time.

### DB↔Kafka atomicity

- The DB transaction is held open across the publish call: `BEGIN` → `INSERT`
  (uncommitted) → produce to `Reimbursement` and await the delivery ack →
  `COMMIT` on success, `ROLLBACK` on failure or timeout.
- This matches `SCOPE.md:219-220` literally rather than treating "rollback
  the DB transaction" as loose wording. The transaction stays open for one
  Kafka round-trip, bounded by the same publish-timeout pattern the POST
  endpoint's producer already uses (`api-post-reimbursement/design.md`:
  `message.timeout.ms` < the app-level await timeout, so a timeout is never
  a "not delivered yet" false negative).

### Retry mechanism

- On DB-insert failure or publish failure (excluding the duplicate case
  below), the failed item is wrapped in a **new** `RequestEnvelope`
  (`{retry: n+1, published_at: now, payload: [item]}`) and produced back onto
  `Request`. No new topic. The existing consumer group re-polls it as an
  ordinary message.
- `retry` lives on the envelope, not the item — so once an item is split out
  for its own retry, its retry counter is independent of every other item
  that arrived in the same original message.

### `retry > 3` — Human Review fallback

- Checked once per consumed envelope, before iterating items (the counter is
  envelope-level). When `retry > 3`: skip the normal insert+publish flow for
  every item in that envelope. For each item, attempt to insert a
  `reimbursement` row with `status = 'human-review'` instead (using whatever
  fields the item carries — see Data Corrections below for exactly which).
  If that insert also fails, fall back to logging the item to the failure
  file. **No publish to `Reimbursement` and no further republish happens for
  these items, regardless of outcome** — `SCOPE.md:218` ("No other action
  below must be done") is read as ending the item's processing entirely,
  success or failure of the fallback insert.

### Duplicate detection (deviation from SCOPE.md, confirmed)

- A DB insert that fails specifically on the unique constraint
  (`reimbursement_request_submitter_key`) is **not** treated as a generic DB
  failure. It is detected by Postgres error code, logged as informational
  (not an error), and the item is dropped — no rollback-and-republish loop,
  no human-review fallback attempt, no failure-file entry.
- Rationale: a duplicate is not a transient condition: retrying it can never
  succeed, since the row it collides with is already committed and already
  published. Applying the generic retry path would burn 3 Kafka round-trips
  and a doomed human-review insert attempt on every duplicate for no benefit.
  This is a spec correction in the same spirit as `STATE.md`'s AD-005 —
  recorded as an explicit deviation, not silently applied.
- All other DB errors (including the `submitted_at` CHECK-constraint gap
  documented below) still go through the standard retry → human-review →
  file-log path.

### `Reimbursement`-topic message shape

- One message per item: `{uuid, retry, published_at, payload}`.
  - `uuid`: the primary key of the row just inserted.
  - `payload`: the item's original JSON, byte-spliced from the consumed
    `Request` message — never re-serialized through the DB round-trip (JSONB
    normalizes on write, so byte-verbatim can only survive if the publish
    path never goes through the database). Same principle as
    `api-post-reimbursement/design.md`'s "the raw bytes are the product".
  - `retry`: resets to `0`. It now tracks the Agent's own failures
    (`SCOPE.md:269-274`), a separate counter from the Publisher's.
  - `published_at`: a fresh timestamp at the moment of *this* publish — the
    Agent's staleness rule (`SCOPE.md:229`) compares it against the row's
    `updated_at`.
- Design phase owns the exact Pydantic model name/location; this is the
  wire-contract decision the spec locks.

### Data corrections vs. `SCOPE.md:210` (confirmed, see AD-005 precedent)

- The Publisher's insert populates `uuid` (DB default), `request_id`,
  `submitted_by`, `submitted_at`, and `original_payload` — not only `uuid`
  and `original_payload` as `SCOPE.md:210` literally states. Without
  `request_id`/`submitted_by`, the unique index that the duplicate-detection
  decision above depends on cannot function as a dedup guard.
- `status` is left at its DB default (`'pending'`) for the normal path, or
  set explicitly to `'human-review'` for the `retry > 3` fallback path, with
  `decision_reason` recording why (e.g. "repeated processing failures after
  3 retries") — consistent with AD-005's rationale for that column existing.

### Known limitation inherited, not fixed here

- `reimbursement_submitted_at_check` caps `submitted_at` to `now() + 1
  hour`, but the POST endpoint's spec explicitly declined to bound future
  `submitted_at` values. A payload the API accepts today can still fail the
  Publisher's insert with a CHECK violation. This routes through the
  standard "DB insert fails" path (retry → human-review → file-log) like any
  other non-duplicate DB error. Fixing the constraint is out of this
  feature's scope (owned by `db-schema-migrations`, already merged) —
  flagged for a follow-up decision, not resolved here.

### Agent's Discretion

- Whether items within one consumed message are processed sequentially or
  concurrently (no throughput requirement is stated; sequential is the
  simpler default and what the spec assumes unless Design finds a reason to
  change it).
- The exact file path / format for the failure-log fallback.
- Whether the consumer handles broker disconnects itself or relies on
  librdkafka's built-in reconnect behavior (leaning toward the latter,
  consistent with `api-post-reimbursement`'s reliance on librdkafka's own
  retry/timeout semantics).

---

## Declined / Undiscussed Gray Areas → Assumptions

Recorded in the spec's Assumptions & Open Questions table:

- Malformed/undecodable `Request`-topic message (not valid JSON, or fails
  `RequestEnvelope` schema entirely): cannot extract a retry counter to
  increment and cannot iterate items, so it is logged to the failure file
  immediately with no DB attempt and no republish.
- An empty `payload` array inside an otherwise-valid envelope: a no-op the
  real system should never produce (the POST endpoint rejects `[]`), handled
  defensively as a dropped/logged no-op rather than an error.

---

## Specific References

- `docs/SCOPE.md:206-274` is the Publisher's contract of record.
- `.specs/features/api-post-reimbursement/spec.md` and `design.md` define
  what arrives on `Request` (`RequestEnvelope`, `ReimbursementRequest`) and
  establish the byte-verbatim / per-message-delivery-await / producer
  patterns this feature reuses.
- `src/api/src/api/migrations/0001.create-reimbursement.sql` is the schema
  of record for the `reimbursement` table (constraints, unique index,
  status default).
- `.specs/STATE.md` AD-005 is the precedent for correcting `SCOPE.md` against
  what the schema actually requires.

---

## Deferred Ideas

- Distinguishing other *permanent* (non-duplicate) DB failures — e.g. the
  `submitted_at` CHECK gap above — from genuinely transient ones, so they
  don't also burn 3 pointless retries. Noted but not adopted now: the
  duplicate case is the one instance confirmed as a deviation; generalizing
  further wasn't asked and shouldn't be assumed.
- Fixing `reimbursement_submitted_at_check` to match the POST endpoint's
  declined-to-bound decision — belongs to a `db-schema-migrations` follow-up.
- Multi-instance/partition scaling beyond what Kafka consumer-group
  partitioning and the existing unique constraint already provide for free.
