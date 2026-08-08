# Publisher — Consume `Request`, Produce `Reimbursement` Specification

## Problem Statement

`docs/SCOPE.md:293` makes no-request-loss the governing constraint for the
whole system: `POST /api/v1/reimbursement` (`api-post-reimbursement`,
implementation in progress) durably hands every accepted batch to the
`Request` Kafka topic and returns. Nothing downstream exists yet — the
`publisher` service (`src/publisher/src/publisher/consumer.py`) is still the
bootstrap placeholder that logs a `SampleMessage` from a `sample-queue`
topic. Until it consumes `Request`, creates the `reimbursement` row, and
publishes to `Reimbursement`, every accepted request is durably stored in
Kafka but never becomes a `reimbursement` entity, and the Agent
(`SCOPE.md:223-274`) has nothing to consume.

This feature delivers that middle link: consume, persist, publish — with the
error handling `SCOPE.md:213-221` mandates for a mission-critical, no-loss
pipeline.

## Goals

- [ ] The publisher consumes `Request`, and for each item in a message's
      batch, creates a `reimbursement` row and publishes exactly one message
      to `Reimbursement` — as one unit of work per item, not per message.
- [ ] A DB write and its paired Kafka publish either both happen or neither
      does — `SCOPE.md:219-220`'s rollback requirement is real, not
      best-effort.
- [ ] Any failure short of the retry ceiling is retried by republishing the
      failed item to `Request` with its retry counter incremented — never
      silently dropped, never silently duplicated.
- [ ] Past the retry ceiling (`retry > 3`), the item is preserved as a
      `human-review` row rather than lost, with a file-log fallback if even
      that fails (`SCOPE.md:216-217`).
- [ ] A resubmitted duplicate is recognized and dropped without exhausting
      retries on a condition retrying can never fix.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| ------- | ------ |
| Agent decision logic (deterministic/probabilistic layers, 90-day/200/2000 rules) | `SCOPE.md:223-274` — a separate service and feature; the publisher makes no approval decisions |
| Consumption of the `Reimbursement` topic | Owned by the Agent feature |
| `GET` / `PUT /api/v1/reimbursement/:uuid` | Separate endpoints (`SCOPE.md:146-204`), owned by the API, not the publisher |
| Database schema or migrations | Owned by `db-schema-migrations`, already merged; this feature writes into the existing `reimbursement` table and does not alter it |
| `UPDATE`ing an existing `reimbursement` row | The publisher only ever `INSERT`s. Status transitions after creation (`auto-approved`, etc.) belong to the Agent |
| Fixing `reimbursement_submitted_at_check`'s mismatch with the POST endpoint's declined-to-bound decision | Belongs to a `db-schema-migrations` follow-up; this feature inherits the gap and routes it through the standard DB-failure path — see Assumptions |
| Authentication / authorization | Deliberate project decision (`SCOPE.md:295-297`); the publisher is an internal consumer with no external surface |
| LangFuse tracing | `SCOPE.md:276-280` scopes LangFuse to the Agent's LLM steps; the publisher does no LLM work, so it uses the existing stdout logging convention plus the file-log fallback this feature adds |
| Kafka topic pre-creation, consumer-side `fetch.max.bytes` sizing | Flagged as a downstream dependency in `api-post-reimbursement/spec.md`; this feature is where it lands — the publisher's consumer config is in scope, broker-level topic creation is not (`SCOPE.md:54` bootstrap concern) |
| Multi-instance / partition-count tuning | Kafka consumer-group partitioning plus the existing unique constraint already provide cross-instance idempotency for free; no new coordination mechanism is added |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here — nothing is left silently unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | --------------- | --------- | ---------- |
| Unit of work | Per-item, not per-message | `SCOPE.md:208-211`'s "iterate over each item... in a single transaction" read as one transaction per item. Confirmed with the user | y |
| DB↔Kafka atomicity | Hold the DB transaction open across the publish call; commit on delivery ack, rollback on failure/timeout | Matches `SCOPE.md:219-220` literally. Confirmed with the user | y |
| Retry mechanism | Republish the failed item alone, as a new `RequestEnvelope` with `retry` incremented, back onto `Request` | No second topic is named anywhere in scope; composes cleanly with per-item units of work. Confirmed with the user | y |
| `Reimbursement`-topic message shape | `{uuid, retry, published_at, payload}`, one message per item, `payload` byte-spliced from the consumed item (not re-serialized through the DB) | Mirrors `RequestEnvelope`; `retry` resets to `0` because it now tracks the Agent's own failures, a separate counter. Confirmed with the user | y |
| Duplicate handling | A unique-constraint violation on insert is detected by Postgres error code and dropped with an informational log — no retry, no human-review fallback, no failure-file entry | A duplicate can never succeed on retry; applying the generic failure path burns 3 round-trips and a doomed fallback insert for nothing. Confirmed with the user as an explicit deviation from `SCOPE.md`'s undifferentiated "DB insert fails" wording | y |
| Fields set on insert | `request_id`, `submitted_by`, `submitted_at`, `original_payload` (plus DB-default `uuid`, `status='pending'`) | `SCOPE.md:210` lists only `UUID` and `Original Payload`, but the unique index `reimbursement_request_submitter_key` is defined on `(request_id, lower(submitted_by))` — without populating those two, the duplicate-detection decision above has nothing to key on. Correction in the same spirit as `STATE.md` AD-005 | y |
| `retry > 3` check granularity | Once per consumed envelope, before iterating items — `retry` is envelope-level, not per item | The schema (`RequestEnvelope.retry: int`) has no per-item retry field | y |
| `retry > 3` fallback scope | Applies independently to every item in that envelope; each gets its own `human-review` insert attempt and its own file-log fallback if that fails | `SCOPE.md:214-217` describes the fallback per failing unit; per-item units of work make this the natural granularity | y |
| `retry > 3` fallback stops further action | No publish to `Reimbursement`, no further republish, regardless of whether the fallback insert succeeds | `SCOPE.md:218` — "No other action below must be done" | y |
| `human-review` fallback insert content | Same fields as the normal path, `status='human-review'`, `decision_reason` set to a fixed string identifying repeated-failure as the cause | Consistent with AD-005's rationale for `decision_reason` existing — an auto-decided-adjacent row records why | y |
| Malformed / undecodable `Request` message | Logged to the failure file immediately; no DB attempt, no republish | A message that can't be parsed as `RequestEnvelope` carries no readable `retry` to increment and no items to iterate | **n** |
| Empty `payload` array in an otherwise-valid envelope | Dropped and logged as a no-op, not an error | The POST endpoint already rejects `[]` at ingress (`api-post-reimbursement/spec.md` RCV-12); this can only arise from a bug or a hand-crafted message, so it fails safe rather than crashing the consumer loop | **n** |
| Item processing order within one message | Sequential | No throughput requirement is stated in scope; sequential is the simpler default. Agent's discretion, confirmed as such | y |
| Consumer offset commit | Committed once every item in the original message has been handled (committed to DB+published, or re-queued as its own new message) — never before, and the original message is never re-read | At-least-once delivery without ever reprocessing an already-handled batch; matches Kafka's standard manual-commit pattern | y |
| Broker disconnect handling (consumer side) | Rely on librdkafka's built-in reconnect/retry behavior; no custom app-level reconnect logic | Consistent with `api-post-reimbursement`'s reliance on librdkafka's own retry/timeout semantics rather than a hand-rolled equivalent | y |
| `submitted_at` CHECK-constraint gap (`+1 hour` cap vs. the POST endpoint's declined-to-bound decision) | Inherited as-is; routes through the standard "DB insert fails" retry → human-review → file-log path like any other non-duplicate DB error | Fixing the constraint is out of this feature's scope (schema owned by `db-schema-migrations`). Flagged, not silently absorbed | y |
| Failure-log file location/format | A local file path (env-configurable, sane default), one JSON line per failure | No persistent-volume infrastructure is added by this feature — matches "Minimal Impact"; retrieval/rotation is a deferred operational concern | **n** |

**Open questions:** none — the four rows marked **n** are recorded
assumptions with a chosen default and rationale. Each is independently
reversible (a code path, a file path, or a boundary check) and none reshapes
the spec.

---

## User Stories

### P1: An item becomes a `reimbursement` row and a `Reimbursement` message, atomically ⭐ MVP

**User Story**: As the system, I want each request item to durably become
exactly one `reimbursement` row and exactly one `Reimbursement`-topic
message, so that no accepted request is lost or double-processed between
the API and the Agent.

**Why P1**: This is the publisher's entire reason to exist — without it,
accepted requests sit in `Request` forever.

**Acceptance Criteria**:

1. WHEN the publisher consumes a `Request` message with `retry <= 3` and N
   items THEN it SHALL process each item as an independent unit of work —
   one DB transaction, one insert, one publish attempt per item.
2. WHEN an item's insert and publish both succeed THEN the system SHALL have
   committed exactly one `reimbursement` row for that item, with `status`
   left at its DB default (`pending`), and published exactly one message to
   `Reimbursement` carrying that row's `uuid`.
3. WHEN a `reimbursement` row is inserted THEN its `request_id`,
   `submitted_by`, `submitted_at`, and `original_payload` SHALL be populated
   from the item — not left null.
4. WHEN the `Reimbursement` message is built THEN its `payload` member SHALL
   be byte-for-byte identical to the item as it arrived in the consumed
   `Request` message, including key order and unicode escaping.
5. WHEN the `Reimbursement` message is built THEN its `retry` member SHALL
   be the integer `0`, and its `published_at` member SHALL be an RFC 3339
   UTC timestamp recorded at the moment of this publish.
6. WHEN a message carries N items and item *i* fails THEN the outcome of
   items other than *i* SHALL be unaffected — their commits and publishes
   proceed independently.
7. WHEN every item in a consumed message has been handled (committed, or
   re-queued as its own new message) THEN the system SHALL commit that
   message's consumer offset, and SHALL NOT re-read that message again.

**Independent Test**: Publish a 3-item `Request` message where the DB write
for item 2 is made to fail; assert items 1 and 3 land as committed rows with
matching `Reimbursement` messages, item 2 does not, and the original
message's offset still advances exactly once.

---

### P1: Publish or DB failure rolls back and retries via `Request` ⭐ MVP

**User Story**: As the system owner, I want a failed insert or a failed
publish to leave no partial state and to retry automatically, so that a
transient failure never becomes a lost request.

**Why P1**: `SCOPE.md:219-221` — insert failure blocks publish, publish
failure rolls back the insert, and any error republishes with an
incremented retry.

**Acceptance Criteria**:

1. WHEN an item's DB insert fails for a reason other than the unique
   constraint THEN the system SHALL NOT attempt to publish that item.
2. WHEN an item's publish to `Reimbursement` fails or times out THEN the
   system SHALL roll back that item's DB transaction — no `reimbursement`
   row SHALL remain committed for it.
3. WHEN an item fails for either reason in AC1/AC2 THEN the system SHALL
   publish a new `Request` message containing only that item, with `retry`
   set to the original message's `retry + 1`.
4. WHEN the republished item is later consumed again THEN it SHALL be
   processed exactly like any other `Request` message — no special-casing
   beyond the `retry` value being non-zero.
5. WHEN a failure occurs THEN the system SHALL log the item's `request_id`
   and the underlying error, and SHALL NOT log the item's payload body or
   the batch's other items.

**Independent Test**: Inject a fake producer whose delivery callback
reports an error for one item; assert the DB row for that item does not
exist after the attempt, and assert a new single-item `Request` message
was published with `retry` incremented by one.

---

### P1: A resubmitted duplicate is dropped, not retried ⭐ MVP

**User Story**: As the system owner, I want a duplicate submission
recognized and dropped immediately, so that a condition retrying can never
fix doesn't burn three retry cycles and a doomed human-review attempt.

**Why P1**: Confirmed as a deviation from `SCOPE.md`'s undifferentiated
failure handling — see Assumptions.

**Acceptance Criteria**:

1. WHEN an item's insert fails specifically on the
   `reimbursement_request_submitter_key` unique constraint THEN the system
   SHALL log it as informational (not as an error) and drop the item.
2. WHEN a duplicate is dropped THEN the system SHALL NOT republish it, SHALL
   NOT attempt a human-review fallback insert for it, and SHALL NOT write it
   to the failure-log file.
3. WHEN a duplicate is dropped THEN the already-committed row it collided
   with SHALL be unaffected.

**Independent Test**: Insert a row directly, then feed the publisher a
`Request` message whose item has the same `request_id` and `submitted_by`
(case-varied); assert the item is dropped with no new row, no republish, and
no failure-log entry.

---

### P1: `retry > 3` preserves the item as `human-review`, never loses it ⭐ MVP

**User Story**: As the system owner, I want an item that has failed
repeatedly to land as a `human-review` row instead of being lost, and to
degrade to a file log only if even that fails, so that persistent failures
are still recoverable by a human.

**Why P1**: `SCOPE.md:214-217` — the mission-critical, no-loss requirement's
last line of defense.

**Acceptance Criteria**:

1. WHEN a consumed `Request` message has `retry > 3` THEN the system SHALL
   NOT attempt the normal insert-then-publish flow for any item in it.
2. WHEN `retry > 3`, for each item THEN the system SHALL attempt to insert a
   `reimbursement` row with `status = 'human-review'` and a
   `decision_reason` identifying repeated failure as the cause.
3. WHEN that fallback insert succeeds THEN the system SHALL take no further
   action for that item — no publish to `Reimbursement`, no republish to
   `Request`.
4. WHEN that fallback insert also fails THEN the system SHALL log the item
   to the failure-log file, and SHALL take no further action.
5. WHEN the fallback insert fails specifically on the unique constraint
   THEN the system SHALL follow the duplicate path (P1 story above), not the
   file-log fallback.

**Independent Test**: Publish a `Request` message with `retry = 4`; assert
no publish to `Reimbursement` occurs, and a `human-review` row is created.
Repeat with the DB unavailable; assert a failure-log entry is written and no
exception escapes the consumer loop.

---

### P1: A malformed message never crashes the consumer ⭐ MVP

**User Story**: As the system owner, I want an unparseable `Request` message
to be logged and skipped rather than stopping the whole consumer, so that
one bad message never blocks every request behind it.

**Why P1**: Mission-critical means the consumer loop itself must never die
on bad input — `SCOPE.md:19-21`.

**Acceptance Criteria**:

1. WHEN a consumed message is not valid JSON, or does not match the
   `RequestEnvelope` schema THEN the system SHALL log it to the failure-log
   file and SHALL NOT attempt a DB insert or a republish.
2. WHEN a message's `payload` is an empty array THEN the system SHALL log it
   as a dropped no-op and SHALL NOT treat it as an error.
3. WHEN either case in AC1/AC2 occurs THEN the consumer loop SHALL continue
   processing subsequent messages without interruption.

**Independent Test**: Feed the consumer a message body that is not valid
JSON, and separately one that is valid JSON but violates the schema; assert
both are logged and the consumer keeps polling afterward.

---

## Edge Cases

- WHEN two publisher instances consume from different partitions of
  `Request` concurrently THEN correctness SHALL rely on Kafka's
  consumer-group partition assignment (no cross-instance coordination is
  added) and the DB unique constraint (no new locking is added).
- WHEN an item's `submitted_at` is more than one hour in the future (passes
  the POST endpoint's validation but violates
  `reimbursement_submitted_at_check`) THEN the system SHALL treat it as a
  standard non-duplicate DB failure — retried, then human-reviewed, then
  file-logged like any other CHECK violation. This is an inherited gap
  between two already-shipped pieces, not resolved by this feature.
- WHEN the broker is unreachable at consume time THEN the system SHALL rely
  on librdkafka's built-in reconnect behavior; no message is considered
  lost because none was ever successfully polled and committed.
- WHEN the broker is unreachable at publish time for an item whose insert
  already ran THEN the system SHALL roll back that insert per the standard
  publish-failure path — never leave a committed row with no corresponding
  publish attempt having been made.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ------ | ------- |
| PUB-01 | P1: Atomic item → row + message (per-item unit of work) | Design | Pending |
| PUB-02 | P1: Atomic item → row + message (row committed, message published) | Design | Pending |
| PUB-03 | P1: Atomic item → row + message (row fields populated) | Design | Pending |
| PUB-04 | P1: Atomic item → row + message (payload byte-verbatim) | Design | Pending |
| PUB-05 | P1: Atomic item → row + message (retry=0, published_at stamp) | Design | Pending |
| PUB-06 | P1: Atomic item → row + message (item independence within a batch) | Design | Pending |
| PUB-07 | P1: Atomic item → row + message (offset committed once, never replayed) | Design | Pending |
| PUB-08 | P1: Rollback + retry (insert failure blocks publish) | Design | Pending |
| PUB-09 | P1: Rollback + retry (publish failure rolls back insert) | Design | Pending |
| PUB-10 | P1: Rollback + retry (republish with retry+1) | Design | Pending |
| PUB-11 | P1: Rollback + retry (republished item processed normally) | Design | Pending |
| PUB-12 | P1: Rollback + retry (failure logged without payload) | Design | Pending |
| PUB-13 | P1: Duplicate dropped (unique-violation detection) | Design | Pending |
| PUB-14 | P1: Duplicate dropped (no retry, no fallback, no file log) | Design | Pending |
| PUB-15 | P1: Duplicate dropped (existing row unaffected) | Design | Pending |
| PUB-16 | P1: `retry > 3` fallback (skip normal flow) | Design | Pending |
| PUB-17 | P1: `retry > 3` fallback (human-review insert with reason) | Design | Pending |
| PUB-18 | P1: `retry > 3` fallback (stop on success) | Design | Pending |
| PUB-19 | P1: `retry > 3` fallback (file log on double failure) | Design | Pending |
| PUB-20 | P1: `retry > 3` fallback (unique violation still short-circuits) | Design | Pending |
| PUB-21 | P1: Malformed message (schema/JSON failure logged, no side effect) | Design | Pending |
| PUB-22 | P1: Malformed message (empty payload dropped as no-op) | Design | Pending |
| PUB-23 | P1: Malformed message (consumer loop survives) | Design | Pending |

**Coverage:** 23 total, 0 mapped to tasks, 23 unmapped ⚠️ (Tasks phase
pending)

---

## Success Criteria

- [ ] A `docs/original/sample.json`-shaped batch, published through the real
      `POST /api/v1/reimbursement` → `Request` → publisher path, produces
      exactly one `reimbursement` row and one `Reimbursement`-topic message
      per item, with the payload byte-verbatim in both.
- [ ] Killing the DB mid-publish leaves zero orphaned rows — every committed
      row has a corresponding successful publish.
- [ ] A resubmitted duplicate never appears twice on `Request` and never
      produces a second row.
- [ ] An item retried past `retry > 3` is recoverable as a `human-review`
      row, or as a file-log entry if even that fails — never silently gone.
- [ ] A malformed message never stops the consumer from processing the next
      one.
