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
Agent's job), no schema changes (the migrations are merged), no
`GET`/`PUT` endpoints, no consumption of the `Reimbursement` topic.

---

## Implementation Decisions

### Unit of work

- **Per-item, fully independent.** One `Request` message can carry N items
  (`RequestEnvelope.payload`); each item gets its own DB transaction, its own
  row, its own publish to `Reimbursement`, and — from the moment it first
  fails — its own retry counter and its own error history.
- A failure in item 2 of 3 never touches items 1 or 3. Retries re-wrap only
  the failed item, so a committed item is never revisited.
- The consumed message's offset is committed once every item has been
  *handled* (committed, re-queued, dropped as duplicate, or written to the failure log) —
  never once every item has *succeeded*.

### DB↔Kafka atomicity

- The DB transaction is held open across the publish:
  `BEGIN` → `INSERT ... RETURNING uuid` → produce to `Reimbursement` → await
  the delivery ack → `COMMIT` on success, `ROLLBACK` on failure or timeout.
- Matches `SCOPE.md:219-220` literally rather than treating "rollback the DB
  transaction" as loose wording.
- **The residual crash window is accepted and deferred — see R-001 in
  `.specs/RISKS.md`.** A process death between the broker ack and the commit
  leaves a `Reimbursement` message whose uuid has no row, and redelivery
  creates a second message with a different uuid. The proper fix is a
  transactional outbox; the interim mitigation (the Agent tolerating an
  unknown uuid) must land in the Agent's spec.

### Retry mechanism

- On DB-insert failure or publish failure (excluding duplicates), the failed
  item is wrapped in a **new** `RequestEnvelope` and produced back onto
  `Request`. No new topic. The existing consumer group re-polls it normally.
- `retry` lives on the envelope, not the item — so once an item is split out
  for its own retry, its counter is independent of its former batch-mates.

### Error history carried across retries — new contract field

- **This is the change that makes the human-review requirement
  implementable.** The consumer that eventually sees `retry = 4` is a
  different poll, possibly a different process on a different instance. It
  has no way to know what failed on attempts 1-3 unless the errors travel
  with the message.
- `RequestEnvelope` gains `errors: list[AttemptError]`, defaulting to empty.
  Each `AttemptError` records: attempt number, RFC 3339 UTC timestamp, the
  stage that failed (`db-insert` or `publish`), the error type, and the
  message.
- Full history, not last-error-only: a reviewer needs to distinguish the same
  error four times (permanently bad data) from four different errors (flaky
  infrastructure). Naturally bounded at 4 entries, because `retry > 3` ends
  the loop.
- **Cost of the change, as of 2026-08-08.** `api-post-reimbursement` has
  shipped, so this amends working code: one model field in
  `shared/models.py` plus the `"errors":[],` splice prefix in
  `api/src/reimbursement/create/producer.py`, with the envelope-byte
  assertions in `test_producer.py` / `test_integration.py` updated alongside.

### `Reimbursement`-topic message shape

- **`{uuid, retry, published_at, errors}` — the uuid only, no payload.**
  User decision: the row already stores `original_payload`, so re-propagating
  the whole body a second time buys nothing. The Agent resolves the payload from the
  DB by uuid.
- `retry` resets to `0` — it now tracks the Agent's own failures
  (`SCOPE.md:269-274`), a separate counter from the publisher's.
- `published_at` is stamped fresh at this publish — the Agent's staleness
  rule (`SCOPE.md:229`) compares it against the row's `updated_at`.
- `errors` is included (empty on first publish) because the Agent has the
  identical `retry > 3` → human-review requirement and would otherwise
  rediscover the exact same missing-context gap. Reversible if the Agent
  feature solves it differently.

### Byte-verbatim is abandoned as a guarantee

- `api-post-reimbursement` byte-splices its envelope specifically to keep the
  body byte-exact for the audit trail. **That property cannot survive the
  publisher**, for two independent reasons: `original_payload` is `JSONB`,
  which normalizes key order, whitespace, numeric literals and unicode
  escapes on write; and the payload no longer travels on `Reimbursement` at
  all.
- The byte-exact original therefore exists in exactly one place — the
  retained `Request` topic log, which makes Kafka retention an
  audit-retention decision. **Recorded as R-003**, not restated as a promise
  the system cannot keep.
- Republished items are held to **semantic** equivalence (same keys, same
  order, same values), not byte identity. Extracting one item's exact
  original bytes from a batch would need a span-tracking JSON parser, and
  buys nothing once JSONB has already normalized the stored copy.

### Duplicate detection (deviation from SCOPE.md, confirmed)

- An insert failing on `reimbursement_request_submitter_key` is **not** a
  generic DB failure. It is detected by Postgres error code, dropped, and
  never retried, never escalated to human review, never written to the failure log.
- Rationale: retrying cannot succeed — the row it collides with is already
  committed and already published. The generic path would burn three Kafka
  round-trips and a doomed fallback insert per duplicate.
- **It is also the crash-recovery mechanism.** After a crash between a row
  commit and an offset commit, redelivery replays already-processed items;
  the duplicate path is what absorbs them safely. It is load-bearing, not an
  optimization.
- **Every drop emits a structured, countable log event** — stable event name,
  `request_id`, constraint name, envelope `retry` — because otherwise a
  system discarding thousands of requests looks identical to one discarding
  none. This is the interim measure; the real metric is **R-004**.

### `retry > 3` — Human Review fallback

- Checked once per consumed envelope, before iterating items (`retry` is
  envelope-level). When `retry > 3`: skip the normal flow entirely; for each
  item, attempt a `reimbursement` insert with `status = 'human-review'`.
- **`decision_reason` carries the full rendered error history** — every
  attempt's number, timestamp, failing stage, error type and message. Not a
  count, not a summary. A reviewer must be able to act without opening a log
  file. If the history is somehow empty, `decision_reason` still states that
  the ceiling was reached and no detail was carried, rather than being null.
- User decision: everything goes in `decision_reason`; `human_review_notes`
  is left for the Agent's LLM side-note (`SCOPE.md:267`).
- If the fallback insert fails → failure log with the same history. If
  it fails on the unique constraint → the duplicate path instead.
- Either way, no publish and no republish — `SCOPE.md:218` ("No other action
  below must be done") ends the item's processing.

### Consumer sizing

- The publisher's consumer fetch limits are derived from the same
  `KAFKA_MAX_MESSAGE_BYTES` constant the API producer and compose broker use.
  This closes the downstream dependency `api-post-reimbursement` flagged:
  raising broker `message.max.bytes` makes ceiling-sized messages *writable*, but a
  default-configured consumer cannot *read* them.

### PII handling in error text

- Postgres constraint errors embed offending column values in their `DETAIL`
  line (a unique violation quotes the submitter's email). Full detail goes to
  the envelope, the row, and the failure-log record — all already hold the payload,
  so it is the same trust boundary.
- **stdout logs get a sanitized form**: error type and constraint name only,
  never `DETAIL`. This keeps the rule `api-post-reimbursement` established.

### Data corrections vs. `SCOPE.md:210`

- The insert populates `request_id`, `submitted_by`, `submitted_at`, and
  `original_payload` — not only `uuid` and `original_payload` as
  `SCOPE.md:210` states. Without `request_id`/`submitted_by`, the unique
  index the duplicate detection depends on has nothing to key on. Correction
  in the spirit of `STATE.md` AD-005.

### Bounded concurrency, and the batch cap that makes it sufficient

- **10 items in flight**, per **AD-013**. Sequential processing is not just
  slower, it is a liveness defect: each item costs an `INSERT` plus an awaited
  `acks=all` round-trip (~15ms), so a large batch runs past
  `max.poll.interval.ms` (300s), the broker revokes partitions, the offset
  commit fails, and the group rebalances. It converges — redelivered
  already-committed items are absorbed by the duplicate path — but only by
  thrashing every partition in the group while presenting as unexplained
  slowness.
- **Concurrency alone does not fix it.** A byte ceiling does not bound item
  count: at ~90 bytes each, even AD-020's reduced 1 MiB ceiling admits ~11,600, and no fixed
  concurrency makes that worst case safe. The deterministic bound is the
  **500-item cap at the API edge**, which this feature depends on but does not
  own. Together: `500 ÷ 10 × ~15ms ≈ 0.75s` against 300s — ~400× headroom.
- **Capping at ingress beat the alternatives.** API-side chunking (publish ~55
  messages of 1000) was considered and rejected: it breaks the all-or-nothing
  publish semantics (earlier chunks can't be unpublished on a mid-batch
  failure) and kills `build_envelope`'s byte-splice, since chunks must be
  re-serialized. Publisher-side splitting was also rejected — it writes the
  full-size message *and* the chunks, doubling Kafka storage for an extra hop.
  One request = one message survives both.
- **The cap does not relax Kafka sizing.** Item count does not bound message
  size — one item carrying a large `raw_ocr_text` or attachment can approach
  the whole ceiling alone, and an item cannot be split. AD-020's Kafka ceiling and the
  consumer fetch sizing both remain necessary.
- **Concurrency is bounded by the connection pool, not CPU.** Each in-flight
  item holds a transaction open across a Kafka round-trip, so 10 concurrent
  pins 10 connections per replica (~45 across three, against Postgres's
  default `max_connections` of 100). The pool must be sized at or above the
  limit.
- Both numbers are estimates with no load test behind them — **R-005**.

### Agent's Discretion

- The failure log's record fields (the destination is settled: a dedicated
  named logger, not a file — see below).
- Whether to rely on librdkafka's built-in reconnect (leaning yes, consistent
  with `api-post-reimbursement`).

---

## Declined / Undiscussed Gray Areas → Assumptions

Recorded in the spec's Assumptions & Open Questions table:

- Republish itself failing → failure log, item treated as handled.
- An item failing `ReimbursementRequest` validation → failure log, never
  retried (a human-review row is impossible without `request_id`, which is
  `NOT NULL`, and retrying cannot fix bad data).
- A malformed/undecodable envelope → failure log, no DB attempt, no
  republish.
- An empty `payload` array → dropped no-op, not an error.
- `errors` on the `Reimbursement` envelope → included for the Agent's benefit,
  reversible.
- Failure-log record format (destination settled as a named logger).

---

## Specific References

- `docs/SCOPE.md:206-274` is the publisher's contract of record.
- `.specs/features/api-post-reimbursement/spec.md` and `design.md` define
  what arrives on `Request` and establish the producer patterns this feature
  reuses (`acks=all`, idempotence, per-message delivery await, timeout
  ordering).
- `src/api/src/migrations/0001.create-reimbursement.sql` is the schema of
  record — constraints, unique index, status default.
- `.specs/RISKS.md` R-001 through R-004 hold everything knowingly deferred.
- `.specs/STATE.md` AD-005 is the precedent for correcting `SCOPE.md` against
  what the schema actually requires.

---

## Deferred Ideas

- Distinguishing other *permanent* DB failures (e.g. the R-002
  `submitted_at` CHECK gap) from transient ones, so they don't burn three
  pointless retries. The duplicate case is the one confirmed deviation;
  generalizing further wasn't asked.
- A real duplicate-drop counter metric and a durable record of drops — R-004.
- Transactional outbox to close the dual-write window — R-001.
- Explicit `Request` topic retention as an audit policy — R-003.
- Re-deriving the 500-item cap and concurrency of 10 from measured latency
  instead of estimates, and asserting at startup that concurrency never
  exceeds the DB pool size — R-005.
- Multi-instance scaling beyond what consumer-group partitioning and the
  unique constraint already give for free.
