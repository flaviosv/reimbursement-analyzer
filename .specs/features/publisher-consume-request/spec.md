# Publisher — Consume `Request`, Produce `Reimbursement` Specification

## Problem Statement

`docs/SCOPE.md:293` makes no-request-loss the governing constraint for the
whole system: `POST /api/v1/reimbursement` (`api-post-reimbursement`,
implementation in progress) durably hands every accepted batch to the
`Request` Kafka topic and returns. Nothing downstream exists yet — the
`publisher` service (`src/publisher/src/consumer.py`, flattened per AD-018) is still the
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
      failed item to `Request` with its retry counter incremented **and its
      accumulated error history attached** — never silently dropped, never
      silently duplicated.
- [ ] Past the retry ceiling (`retry > 3`), the item is preserved as a
      `human-review` row whose `decision_reason` spells out **every failure
      that led there**, so a reviewer can act without reading server logs
      (`SCOPE.md:216-217`).
- [ ] A resubmitted duplicate is recognized and dropped without exhausting
      retries on a condition retrying can never fix — and the drop is
      emitted as a countable event, not a silent no-op.
- [ ] A full batch completes far inside `max.poll.interval.ms`, so processing
      time never causes a consumer eviction or a group rebalance
      (**AD-013**).

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| ------- | ------ |
| Agent decision logic (deterministic/probabilistic layers, 90-day/200/2000 rules) | `SCOPE.md:223-274` — a separate service and feature; the publisher makes no approval decisions |
| Consumption of the `Reimbursement` topic | Owned by the Agent feature |
| `GET` / `PUT /api/v1/reimbursement/:uuid` | Separate endpoints (`SCOPE.md:146-204`), owned by the API |
| Database schema or migrations | Owned by `db-schema-migrations`, already merged; this feature writes into the existing `reimbursement` table and does not alter it |
| `UPDATE`ing an existing `reimbursement` row | The publisher only ever `INSERT`s. Status transitions after creation belong to the Agent |
| Closing the dual-write window (transactional outbox) | Recorded as **R-001** in `.specs/RISKS.md`; deferred by explicit decision. The Agent-side mitigation (tolerate a `uuid` with no row) belongs to the Agent's spec |
| Fixing `reimbursement_submitted_at_check`'s mismatch with the POST endpoint's declined-to-bound decision | Recorded as **R-002**; this feature inherits the gap and routes it through the standard DB-failure path |
| Kafka topic retention / audit-retention policy | Recorded as **R-003** — the byte-exact original now lives only in the `Request` log |
| Metrics infrastructure, counters, alerting rules | Recorded as **R-004**. This feature emits structured, countable log events; it does not introduce a metrics stack (`SCOPE.md:300-303` defers monitoring to assumed external tooling) |
| Enforcing the 500-item batch cap | **AD-013** places it at the API edge. **Already shipped** — `BATCH_ADAPTER` applies `Field(max_length=MAX_BATCH_ITEMS)`; this feature depends on the cap and asserts its own behaviour at that batch size |
| Tuning the 500 / 10 throughput parameters | Starting values with no load test behind them — recorded as **R-005** for revisit with production data |
| Authentication / authorization | Deliberate project decision (`SCOPE.md:295-297`); the publisher is an internal consumer with no external surface |
| LangFuse tracing | `SCOPE.md:276-280` scopes LangFuse to the Agent's LLM steps; the publisher does no LLM work |
| Kafka topic pre-creation at bootstrap | `SCOPE.md:54` bootstrap concern. The publisher's own **consumer** sizing is in scope (see P1: ceiling-sized messages) |
| Multi-instance / partition-count tuning | Kafka consumer-group partitioning plus the existing unique constraint already provide cross-instance idempotency; no new coordination mechanism is added |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here — nothing is left silently unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | --------------- | --------- | ---------- |
| Unit of work | Per-item, not per-message | `SCOPE.md:208-211`'s "iterate over each item... in a single transaction" read as one transaction per item | y |
| DB↔Kafka atomicity | Hold the DB transaction open across the publish; commit on delivery ack, rollback on failure/timeout | Matches `SCOPE.md:219-220` literally. Residual crash window accepted and recorded as **R-001** | y |
| Retry mechanism | Republish the failed item alone, as a new `RequestEnvelope` with `retry` incremented, back onto `Request` | No second topic is named anywhere in scope; composes cleanly with per-item units of work | y |
| Error context across retries | `RequestEnvelope` gains `errors: list[AttemptError]`, appended to on every failure and carried through each republish | **Without this the feature is not implementable.** The consumer that sees `retry = 4` is a different poll, possibly a different process — it has no other way to know what went wrong on attempts 1-3. `api-post-reimbursement` has since shipped, so this is now an amendment to working code: one model field in `shared/models.py` plus the `"errors":[],` splice prefix in `create/producer.py`, with the envelope-byte assertions in `test_producer.py` / `test_integration.py` updated alongside | y |
| Error history depth | Full history, one `AttemptError` per failed attempt, naturally bounded at 4 | A reviewer needs to distinguish the same error 4 times (permanently bad data — fix it) from 4 different errors (flaky infrastructure — just replay it). `retry > 3` ends the loop, so the list cannot exceed 4 entries | y |
| `Reimbursement` message contents | `{uuid, retry, published_at, errors}` — **the `uuid` only, no payload** | User decision: the row already holds `original_payload`, so re-propagating it would duplicate the whole body again for no gain. The Agent reads the payload back from the DB by `uuid` | y |
| `Reimbursement` envelope carries `errors` too | Yes, empty on first publish | The Agent has the identical `retry > 3` → human-review requirement (`SCOPE.md:271-273`) and would hit the identical missing-context gap. Defining it once here prevents rediscovering the same problem. Reversible — the Agent feature may drop the field if it solves this differently | **n** |
| Byte-verbatim payload downstream | **Abandoned as a guarantee** | `original_payload` is `JSONB`, which normalizes key order, whitespace, numeric literals, and unicode escapes on write; and the payload no longer travels on `Reimbursement` at all. The byte-exact original survives only in the retained `Request` log. Recorded as **R-003** rather than restated as a promise the system cannot keep | y |
| Republished item fidelity | Semantic equivalence — same keys, same values, key order preserved by Python's ordered dicts | Extracting one item's exact original bytes from a batch would need a span-tracking JSON parser. Since byte-identity is already lost at the JSONB boundary, the added complexity buys nothing | y |
| Duplicate handling | A unique-constraint violation on insert is detected by Postgres error code and dropped with a structured informational event — no retry, no human-review fallback, no failure-file entry | A duplicate can never succeed on retry; the generic path would burn 3 round-trips and a doomed fallback insert. Explicit deviation from `SCOPE.md`'s undifferentiated "DB insert fails" wording | y |
| Duplicate observability | Every drop emits a structured log record with a stable event name, the `request_id`, the constraint name, and the envelope's `retry` | Otherwise a system discarding thousands of requests looks identical to one discarding none. Interim measure — the proper metric is **R-004** | y |
| Fields set on insert | `request_id`, `submitted_by`, `submitted_at`, `original_payload` (plus DB-default `uuid`, `status='pending'`) | `SCOPE.md:210` lists only `UUID` and `Original Payload`, but the unique index is on `(request_id, lower(submitted_by))` — without those two columns the duplicate detection above has nothing to key on. Correction in the spirit of `STATE.md` AD-005 | y |
| `retry > 3` check granularity | Once per consumed envelope, before iterating items | `retry` is envelope-level; `RequestEnvelope` has no per-item counter | y |
| `retry > 3` fallback scope | Applies independently to every item in that envelope | Per-item units of work make this the natural granularity | y |
| `retry > 3` stops further action | No publish to `Reimbursement`, no further republish, regardless of whether the fallback insert succeeds | `SCOPE.md:218` — "No other action below must be done" | y |
| Where the error detail lands on the row | **`decision_reason`**, carrying the full rendered history | User decision. Keeps one column authoritative for "why is this row in this state" rather than splitting operational detail across two | y |
| Republish itself fails | The item is written to the failure log with its error history, then treated as handled | The failure log is the design's universal last resort. Not committing the offset instead would redeliver the whole message and hot-loop while the broker is down | y |
| Item fails `ReimbursementRequest` validation | Failure log immediately; no retry, no human-review row | `RequestEnvelope.payload` is `list[dict[str, Any]]` — items are not schema-checked on the way in, and a hand-crafted or corrupted item can lack `request_id`, which is `NOT NULL`. A human-review row is impossible without it, and retrying cannot fix bad data | **n** |
| Malformed / undecodable `Request` message | Failure log immediately; no DB attempt, no republish | A message that cannot be parsed as `RequestEnvelope` carries no readable `retry` to increment and no items to iterate | **n** |
| Empty `payload` array in a valid envelope | Dropped and logged as a no-op, not an error | The POST endpoint already rejects `[]` at ingress; this can only arise from a bug or a hand-crafted message, so it fails safe rather than crashing the loop | **n** |
| Item processing order within one message | **Bounded concurrency: at most 10 items in flight**, order of completion non-deterministic | Sequential processing of a large batch would exceed `max.poll.interval.ms` and trigger rebalance thrashing. 10 is a starting value paired with the API's 500-item cap: `500 ÷ 10 × ~15ms ≈ 0.75s` against a 300s interval. Both are provisional — recorded as **R-005** | y |
| Upper bound on items per message | 500, enforced at the API edge (already shipped) — **not by this feature** | A byte ceiling alone does not bound item count — minimal items are ~90 bytes, so even the reduced 1 MiB ceiling (AD-020) admits ~11,600 of them, well past what any fixed concurrency processes inside the poll interval. The cap belongs at ingress where a client can be told; this feature depends on it and asserts its own behaviour at that size. Requires an amendment to `api-post-reimbursement` | y |
| Concurrency vs. connection pool | The DB pool SHALL be sized at or above the concurrency limit | Each in-flight item holds a transaction open across a Kafka round-trip, so concurrency N pins N connections. A pool smaller than N starves under load rather than failing loudly | y |
| Consumer offset commit | Committed once every item in the message has been handled (committed, re-queued, dropped, or written to the failure log) | At-least-once delivery. A crash before the commit redelivers the message, and already-committed items are absorbed by the duplicate path — which is why that path is crash-recovery machinery, not just an optimization | y |
| Broker disconnect handling | Rely on librdkafka's built-in reconnect/retry behavior | Consistent with `api-post-reimbursement`'s reliance on librdkafka's own semantics rather than a hand-rolled equivalent | y |
| PII in error text | Two record kinds, deliberately different. The **failure log** carries full detail including the payload — preserving the request is its entire purpose. Ordinary **`processing` error logs get a sanitized form**: error type and constraint name, never the driver's `DETAIL` line | Postgres constraint errors embed offending values (e.g. the submitter's email) in `DETAIL`, so routine logging must not echo them. **Corrected 2026-08-08:** this row previously said full detail was safe because it went to a *file* outside stdout's trust boundary. That stopped being true when the failure log became a named logger — it propagates to the root handler, so both kinds reach stdout. That is intended, not a leak: `SCOPE.md` requires the failure log be scraped by a tool such as fluentd, which tails stdout. The real boundary is that **stdout is a privileged sink** — anyone who can read the publisher's logs can read reimbursement payloads. `propagate = False` would *not* be a fix; with no handler attached the last-resort record would vanish | y |
| Failure-log destination/format | A **dedicated named logger** at critical level, one structured JSON record per failure — not a file written by application code | User decision (2026-08-08). A container-local file is destroyed by the restart it is meant to survive, so it would lose exactly the records it exists to preserve. `SCOPE.md:216-217`'s "log the error in a file" is satisfied at the layer that owns it: ops attach a `FileHandler` or shipper to the logger name, with no code change. Also removes the volume, path config, rotation, and off-loop file I/O | y |
| Termination-signal semantics | **Graceful drain**: the message in hand finishes and commits its offset, then the loop exits between messages | Amendment (2026-08-08) — this **reverses PUB-33's original wording**, which required the in-flight transaction to roll back with its offset uncommitted. `design.md` already described the drain and the code implements it; the AC was the artefact left behind. Draining is the safer default: it avoids discarding work that already succeeded, and correctness on an *abrupt* kill (SIGKILL, OOM, node loss) is unchanged either way — it still rests on redelivery plus the duplicate path, exactly as **R-001** describes | y |
| Consumer fetch sizing vs. a single ceiling-sized message | Fetch limits sized from `KAFKA_MAX_MESSAGE_BYTES`, but **not** claimed to be what makes an oversized record readable | Amendment (2026-08-08) — **KIP-74 makes `fetch.max.bytes` a soft limit**: the broker always returns at least one record per partition regardless of the setting, so a consumer can never stall on a single oversized record and a ceiling-sized round trip proves the *broker's* behaviour, not the consumer's sizing. The settings stay because they bound the memory of a **multi-message** fetch; PUB-34 asserts them structurally and PUB-35 now claims only end-to-end processing. Recorded so the disproven premise is not re-added | y |

**Open questions:** none — the six rows marked **n** are recorded
assumptions with a chosen default and rationale. Each is independently
reversible (a code path, a file path, a boundary check, or one optional
model field) and none reshapes the spec.

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
   committed exactly one `reimbursement` row for that item with `status` at
   its DB default (`pending`), and published exactly one message to
   `Reimbursement`.
3. WHEN a `reimbursement` row is inserted THEN its `request_id`,
   `submitted_by`, `submitted_at`, and `original_payload` SHALL be populated
   from the item — not left null.
4. WHEN a `Reimbursement` message is published THEN it SHALL contain the
   `uuid` of the row just inserted, `retry` as the integer `0`, and
   `published_at` as an RFC 3339 UTC timestamp recorded at that publish.
5. WHEN a `Reimbursement` message is published THEN it SHALL NOT contain the
   request payload — the Agent resolves it from the DB by `uuid`.
6. WHEN a message carries N items and item *i* fails THEN the outcome of
   items other than *i* SHALL be unaffected.
7. WHEN every item in a consumed message has been handled THEN the system
   SHALL commit that message's consumer offset exactly once.

**Independent Test**: Publish a 3-item `Request` message where the DB write
for item 2 is made to fail; assert items 1 and 3 land as committed rows with
matching `Reimbursement` messages carrying their uuids and no payload, item
2 does not, and the offset advances exactly once.

---

### P1: Failure rolls back and retries via `Request`, carrying its error history ⭐ MVP

**User Story**: As the system owner, I want a failed insert or publish to
leave no partial state, to retry automatically, and to accumulate a record
of what went wrong on every attempt, so that a transient failure never
becomes a lost request and a persistent one arrives explained.

**Why P1**: `SCOPE.md:219-221`. The error history is what makes the
`retry > 3` human-review story actionable rather than a dead end.

**Acceptance Criteria**:

1. WHEN an item's DB insert fails for a reason other than the unique
   constraint THEN the system SHALL NOT attempt to publish that item.
2. WHEN an item's publish to `Reimbursement` fails or times out THEN the
   system SHALL roll back that item's DB transaction — no `reimbursement`
   row SHALL remain committed for it.
3. WHEN an item fails for either reason in AC1/AC2 THEN the system SHALL
   publish a new `Request` message containing only that item, with `retry`
   set to the consumed envelope's `retry + 1`.
4. WHEN that republished message is built THEN its `errors` list SHALL equal
   the consumed envelope's `errors` with **one new entry appended**,
   recording the attempt number, an RFC 3339 UTC timestamp, the stage that
   failed (`db-insert` or `publish`), the error type, and the error message.
5. WHEN an item has failed on multiple attempts THEN every prior entry SHALL
   still be present — entries are appended, never overwritten or truncated.
6. WHEN an item is republished THEN its content SHALL be semantically
   identical to the item consumed — same keys in the same order, same values.
7. WHEN the republished item is later consumed THEN it SHALL be processed
   exactly like any other `Request` message, with no special-casing beyond
   the `retry` value being non-zero.
8. WHEN a failure is logged to stdout THEN the record SHALL carry the item's
   `request_id` and the error type, and SHALL NOT carry the item's payload
   or the driver's value-bearing `DETAIL` text.
9. WHEN the republish itself fails THEN the system SHALL write the item and
   its error history to the failure log rather than losing it, and
   SHALL treat the item as handled.

**Independent Test**: Inject a fake producer whose delivery callback reports
an error; assert no row remains, and a new single-item `Request` message was
published with `retry` incremented and exactly one `errors` entry naming the
`publish` stage. Feed that message back in with a failing insert and assert
the second message carries **two** entries in order.

---

### P1: A resubmitted duplicate is dropped, counted, and never retried ⭐ MVP

**User Story**: As the system owner, I want a duplicate recognized and
dropped immediately but visibly, so that a condition retrying can never fix
doesn't burn three cycles, and so that a flood of duplicates is detectable
rather than silent.

**Why P1**: Confirmed as a deviation from `SCOPE.md`'s undifferentiated
failure handling. It is also the mechanism that makes crash recovery safe —
see AC4.

**Acceptance Criteria**:

1. WHEN an item's insert fails specifically on the
   `reimbursement_request_submitter_key` unique constraint THEN the system
   SHALL drop the item.
2. WHEN a duplicate is dropped THEN the system SHALL NOT republish it, SHALL
   NOT attempt a human-review fallback insert, and SHALL NOT write it to the
   failure log.
3. WHEN a duplicate is dropped THEN the system SHALL emit a structured log
   record at informational level carrying a stable event name, the item's
   `request_id`, the constraint name, and the envelope's `retry` — so an
   external monitor can count occurrences without parsing prose (**R-004**).
4. WHEN a message is redelivered after a crash that occurred between
   committing a row and committing the offset THEN the already-committed
   items SHALL be absorbed by this duplicate path, producing no second row
   and no second `Reimbursement` message.
5. WHEN a duplicate is dropped THEN the already-committed row it collided
   with SHALL be unaffected.

**Independent Test**: Insert a row directly, then feed the publisher a
`Request` message whose item has the same `request_id` and a case-varied
`submitted_by`; assert the item is dropped, no new row exists, no republish
occurred, no failure-log entry was written, and the structured event was
emitted with the constraint name.

---

### P1: `retry > 3` preserves the item as `human-review`, explained ⭐ MVP

**User Story**: As a human reviewer, I want an item that failed repeatedly
to arrive as a `human-review` row that tells me exactly what went wrong on
each attempt, so that I can decide what to do without access to server logs.

**Why P1**: `SCOPE.md:214-217` is the no-loss requirement's last line of
defense — and a preserved row with no explanation is not reviewable.

**Acceptance Criteria**:

1. WHEN a consumed `Request` message has `retry > 3` THEN the system SHALL
   NOT attempt the normal insert-then-publish flow for any item in it.
2. WHEN `retry > 3`, for each item THEN the system SHALL attempt to insert a
   `reimbursement` row with `status = 'human-review'`.
3. WHEN that fallback row is inserted THEN its `decision_reason` SHALL
   contain a human-readable rendering of **every** entry in the envelope's
   `errors` list — each attempt's number, timestamp, failing stage, error
   type, and message — not merely a count or a summary.
4. WHEN the envelope's `errors` list is empty or absent despite
   `retry > 3` THEN `decision_reason` SHALL still state that the retry
   ceiling was reached and that no error detail was carried, rather than
   being left null.
5. WHEN the fallback insert succeeds THEN the system SHALL take no further
   action for that item — no publish to `Reimbursement`, no republish.
6. WHEN the fallback insert fails THEN the system SHALL write the item and
   its full error history to the failure log, and take no further action.
7. WHEN the fallback insert fails specifically on the unique constraint THEN
   the system SHALL follow the duplicate path, not the failure-log fallback.

**Independent Test**: Feed a `Request` message with `retry = 4` and three
distinct `errors` entries; assert no `Reimbursement` publish occurs, a
`human-review` row exists, and its `decision_reason` contains all three
error messages and all three stage names. Repeat with the DB unavailable and
assert a failure-log entry carries the same three entries.

---

### P1: Bad input never stops the consumer ⭐ MVP

**User Story**: As the system owner, I want an unparseable or invalid
message logged and skipped rather than crashing the consumer, so that one
bad message never blocks every request behind it.

**Why P1**: Mission-critical means the consumer loop must survive bad input
(`SCOPE.md:19-21`).

**Acceptance Criteria**:

1. WHEN a consumed message is not valid JSON, or does not match the
   `RequestEnvelope` schema THEN the system SHALL write it to the
   failure log and SHALL NOT attempt a DB insert or a republish.
2. WHEN an item within a valid envelope fails `ReimbursementRequest`
   validation THEN the system SHALL write that item to the failure log
   and SHALL NOT retry it, publish it, or attempt a human-review row for it.
3. WHEN a message's `payload` is an empty array THEN the system SHALL log it
   as a dropped no-op and SHALL NOT treat it as an error.
4. WHEN any case above occurs THEN the consumer loop SHALL continue
   processing subsequent messages without interruption.
5. WHEN the process receives a termination signal THEN the message currently
   being processed SHALL complete and commit its offset, and the loop SHALL
   then exit without consuming another message.

**Independent Test**: Feed the consumer a non-JSON body, a schema-violating
envelope, an item missing `request_id`, and an empty `payload` array; assert
each is handled as specified and that a subsequent valid message is still
processed normally.

---

### P1: The consumer can read ceiling-sized `Request` messages ⭐ MVP

**User Story**: As an operator, I want the publisher able to read the
largest message the API is allowed to write, so that the size contract the
API advertises is deliverable end to end rather than only writable.

**Why P1**: `api-post-reimbursement/spec.md` flagged this as a downstream
dependency: raising broker `message.max.bytes` makes ceiling-sized messages
*writable*, and the publisher is where they have to land. The consumer's
fetch limits are sized from the same constant so that a **multi-message**
fetch is bounded by the ceiling rather than by librdkafka's smaller default —
not because a single oversized record would otherwise be unreadable (see the
KIP-74 row in Assumptions).

**Acceptance Criteria**:

1. WHEN the consumer is configured THEN its fetch limits SHALL be sized from
   the same `KAFKA_MAX_MESSAGE_BYTES` constant the API's producer and the
   compose broker already use — the number SHALL NOT be retyped.
2. WHEN a `Request` message at the size ceiling is produced THEN the
   publisher SHALL consume it and process it end to end — its row committed
   and its `Reimbursement` message published.

**Independent Test**: Against a real broker sized from
`KAFKA_MAX_MESSAGE_BYTES`, produce a ceiling-sized `Request` message and
assert the publisher consumes it and creates the corresponding rows.

---

### P1: Items are processed with bounded concurrency ⭐ MVP

**User Story**: As an operator, I want a message's items processed
concurrently under a fixed ceiling, so that a full batch completes far
inside the consumer's poll interval and the group never rebalances because
of processing time.

**Why P1**: Sequential processing is not merely slower — it is a liveness
defect. Each item costs an `INSERT` plus an awaited `acks=all` round-trip
(~15ms), so a large batch exceeds `max.poll.interval.ms`, the broker revokes
the consumer's partitions, the offset commit fails, and the group rebalances.
It recovers only by thrashing. See **AD-013** and **R-005**.

**Acceptance Criteria**:

1. WHEN a message's items are processed THEN at most **10** items SHALL be
   in flight at any moment.
2. WHEN the concurrency limit is reached THEN further items SHALL wait for a
   slot rather than being started, dropped, or failed.
3. WHEN items are processed concurrently THEN each SHALL retain its own
   transaction, its own publish, and its own outcome — one item's failure
   SHALL NOT affect another's, exactly as under sequential processing.
4. WHEN items complete THEN their completion order and their
   `Reimbursement` publish order SHALL be treated as non-deterministic; no
   behaviour SHALL depend on either.
5. WHEN every item has been handled THEN the offset SHALL be committed once,
   after the last item settles — never while items are still in flight.
6. WHEN the service starts THEN the DB connection pool SHALL be sized at or
   above the concurrency limit, since each in-flight item holds a
   transaction open across a Kafka round-trip.
7. WHEN the consumer is configured THEN `max.poll.interval.ms` SHALL be set
   explicitly rather than inherited from the client library, and a message's
   items SHALL be processed concurrently up to the configured limit rather
   than one at a time. AD-013's `500 ÷ 10 × ~15ms ≈ 0.75s` against a 300 s
   interval is the **rationale** for those two values, not an asserted
   runtime — a wall-clock promise is not observable without a load test
   (**R-005**).

**Independent Test**: Feed a 500-item message with an instrumented fake
producer that records maximum observed in-flight count; assert it never
exceeds 10, that all 500 rows are committed, and that the offset is
committed exactly once after the last item. Separately, make one item fail
and assert the other 499 are unaffected.

---

## Edge Cases

- WHEN two publisher instances consume different partitions of `Request`
  concurrently THEN correctness SHALL rely on Kafka's consumer-group
  partition assignment and the DB unique constraint — no cross-instance
  coordination is added.
- WHEN an item's `submitted_at` is more than one hour in the future (accepted
  by the API but rejected by `reimbursement_submitted_at_check`) THEN the
  system SHALL treat it as a standard non-duplicate DB failure. Inherited
  inconsistency, recorded as **R-002**.
- WHEN the process dies between the broker's ack and the DB commit THEN a
  `Reimbursement` message may exist whose `uuid` has no row, and redelivery
  will create a second message with a different `uuid`. Accepted and
  recorded as **R-001**; the Agent-side mitigation belongs to the Agent's spec.
- WHEN the broker is unreachable at consume time THEN the system SHALL rely
  on librdkafka's reconnect behavior; nothing is lost because nothing was
  polled and committed.
- WHEN the same error recurs on all four attempts THEN the resulting
  `decision_reason` SHALL show four entries rather than collapsing them, so a
  reviewer can tell a permanent fault from a flapping one.
- WHEN processing a message would exceed `max.poll.interval.ms` THEN the
  system does not fail cleanly — the broker revokes partitions, the offset
  commit fails, and the group rebalances. It converges (redelivered
  already-committed items are absorbed by the duplicate path) but only by
  thrashing. The 500-item cap and concurrency of 10 exist to keep this
  unreachable by a ~400× margin; it is a bound to preserve, not a condition
  to handle at runtime.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ------ | ------- |
| PUB-01 | P1: Atomic item→row+message (per-item unit of work) | Design | Pending |
| PUB-02 | P1: Atomic item→row+message (row committed at `pending`, one message) | Design | Pending |
| PUB-03 | P1: Atomic item→row+message (row identity fields populated) | Design | Pending |
| PUB-04 | P1: Atomic item→row+message (message carries uuid, retry=0, published_at) | Design | Pending |
| PUB-05 | P1: Atomic item→row+message (message carries no payload) | Design | Pending |
| PUB-06 | P1: Atomic item→row+message (item independence within a batch) | Design | Pending |
| PUB-07 | P1: Atomic item→row+message (offset committed exactly once) | Design | Pending |
| PUB-08 | P1: Rollback+retry (insert failure blocks publish) | Design | Pending |
| PUB-09 | P1: Rollback+retry (publish failure rolls back the insert) | Design | Pending |
| PUB-10 | P1: Rollback+retry (republish single item with retry+1) | Design | Pending |
| PUB-11 | P1: Rollback+retry (error entry appended with stage, type, message) | Design | Pending |
| PUB-12 | P1: Rollback+retry (prior entries preserved, never truncated) | Design | Pending |
| PUB-13 | P1: Rollback+retry (republished item semantically identical) | Design | Pending |
| PUB-14 | P1: Rollback+retry (republished item processed normally) | Design | Pending |
| PUB-15 | P1: Rollback+retry (stdout log omits payload and DETAIL values) | Design | Pending |
| PUB-16 | P1: Rollback+retry (republish failure falls back to the file log) | Design | Pending |
| PUB-17 | P1: Duplicate (unique-violation detection and drop) | Design | Pending |
| PUB-18 | P1: Duplicate (no retry, no fallback, no file log) | Design | Pending |
| PUB-19 | P1: Duplicate (structured countable event emitted) | Design | Pending |
| PUB-20 | P1: Duplicate (absorbs post-crash redelivery) | Design | Pending |
| PUB-21 | P1: Duplicate (existing row unaffected) | Design | Pending |
| PUB-22 | P1: `retry > 3` (skip the normal flow) | Design | Pending |
| PUB-23 | P1: `retry > 3` (human-review row inserted) | Design | Pending |
| PUB-24 | P1: `retry > 3` (`decision_reason` renders the full error history) | Design | Pending |
| PUB-25 | P1: `retry > 3` (`decision_reason` non-null even with no history) | Design | Pending |
| PUB-26 | P1: `retry > 3` (stop on success — no publish, no republish) | Design | Pending |
| PUB-27 | P1: `retry > 3` (failure-log fallback carries the history) | Design | Pending |
| PUB-28 | P1: `retry > 3` (unique violation still short-circuits) | Design | Pending |
| PUB-29 | P1: Bad input (malformed message logged, no side effect) | Design | Pending |
| PUB-30 | P1: Bad input (invalid item logged, never retried) | Design | Pending |
| PUB-31 | P1: Bad input (empty payload dropped as a no-op) | Design | Pending |
| PUB-32 | P1: Bad input (consumer loop survives) | Design | Pending |
| PUB-33 | P1: Bad input (termination signal drains the message in hand, then exits) | Design | Pending |
| PUB-34 | P1: Ceiling-sized messages (fetch limits derived from the shared constant) | Design | Pending |
| PUB-35 | P1: Ceiling-sized messages (a `KAFKA_MAX_MESSAGE_BYTES` message consumed and processed end to end) | Design | Pending |
| PUB-36 | P1: Concurrency (at most 10 items in flight) | Design | Pending |
| PUB-37 | P1: Concurrency (excess items wait for a slot, never dropped) | Design | Pending |
| PUB-38 | P1: Concurrency (per-item independence preserved under concurrency) | Design | Pending |
| PUB-39 | P1: Concurrency (completion and publish order non-deterministic) | Design | Pending |
| PUB-40 | P1: Concurrency (offset committed once, after the last item settles) | Design | Pending |
| PUB-41 | P1: Concurrency (connection pool sized at or above the limit) | Design | Pending |
| PUB-42 | P1: Concurrency (`max.poll.interval.ms` set explicitly; fan-out genuinely concurrent) | Design | Pending |

**Coverage:** 42 total, 0 mapped to tasks, 42 unmapped ⚠️ (Tasks phase
pending)

---

## Success Criteria

- [ ] A `docs/original/sample.json`-shaped batch, published through the real
      `POST /api/v1/reimbursement` → `Request` → publisher path, produces
      exactly one `reimbursement` row and one `Reimbursement` message per
      item, each message carrying only the row's `uuid`.
- [ ] Killing the DB mid-publish leaves zero orphaned rows.
- [ ] A resubmitted duplicate never produces a second row, and its drop is
      visible as a countable structured event.
- [ ] An item that failed four times arrives as a `human-review` row whose
      `decision_reason` names all four failures — readable without opening a
      server log.
- [ ] A malformed message never stops the consumer from processing the next.
- [ ] A `Request` message at `KAFKA_MAX_MESSAGE_BYTES` is consumed and processed end to end.
