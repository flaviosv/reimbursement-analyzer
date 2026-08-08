# Agent — Consume `Reimbursement`, Resolve by UUID Specification

## Problem Statement

`docs/SCOPE.md:223-274` gives the Reimbursement Agent its own error-handling
and staleness contract, but `src/agent` (`src/agent/src/agent/consumer.py`)
is still the bootstrap placeholder that logs a `SampleMessage` from a
`sample-topic`. Once `publisher-consume-request` starts publishing to
`Reimbursement` (`{uuid, retry, published_at, errors}`, AD-015), nothing
consumes it — every published message would sit unread, and the pipeline
that is otherwise durable end to end (`Request` → row → `Reimbursement`)
dead-ends at the last hop.

This feature delivers the first half of the Agent: consume `Reimbursement`,
resolve the message to its `reimbursement` row by `uuid`, apply the
staleness guard, and handle every way that resolution can fail — with the
same no-loss discipline `publisher-consume-request` established. The
decision logic that acts on a resolved, fresh row (reject / auto-approve /
human-review scoring, `SCOPE.md:232-266`) is a separate, later feature.

## Goals

- [ ] The agent consumes `Reimbursement` and resolves each message to its
      `reimbursement` row by `uuid`, or safely recognizes it cannot (ghost,
      per **R-001**) — without ever crashing the consumer loop.
- [ ] A message whose `published_at` is older than the row's `updated_at`
      is ignored, per `SCOPE.md:229`.
- [ ] Any resolution-stage failure short of the retry ceiling is retried by
      republishing to `Reimbursement` with `retry` incremented and its
      accumulated error history attached — never silently dropped, never
      silently duplicated.
- [ ] Past the retry ceiling (`retry > 3`), the row is updated to
      `human-review` with a `decision_reason` that spells out every failure
      that led there — or, if the row doesn't exist (a ghost that never
      cleared retries), the item is preserved in the failure log instead.
- [ ] A malformed or schema-invalid message is logged and skipped, never
      crashes the consumer loop.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| ------- | ------ |
| Decision logic (reject / auto-approve, deterministic + probabilistic layers, human-review LLM side-note) | `SCOPE.md:232-266` — a separate, later feature; explicitly deferred by the user to a future session |
| `GET` / `PUT /api/v1/reimbursement/:uuid` | Separate endpoints (`SCOPE.md:146-204`), owned by the API |
| Database schema or migrations | `status`, `decision_reason`, `updated_at` already exist (`0001.create-reimbursement.sql`); this feature needs no new column |
| Building `shared/reimbursement/repository.py`, `shared/failure_log.py`, `shared/reimbursement/use_cases/send_human_review.py` from scratch | Owned by `publisher-consume-request` (Draft `design.md` v3 already specs them). This feature **depends on and extends** them — adds a read-by-uuid statement and a human-review `UPDATE` statement — but does not construct the modules. See Assumptions |
| Closing the dual-write window (transactional outbox) | **R-001** — this feature implements the *interim mitigation* (tolerate a ghost `uuid`), not the fix |
| Fixing `reimbursement_submitted_at_check` (**R-002**) | Not applicable — this feature never inserts or checks `submitted_at` |
| Consumer fetch-size tuning for ceiling-sized messages | `Reimbursement` messages are small, fixed-shape envelopes, unlike `Request`'s large batch payloads that motivated `KAFKA_MAX_MESSAGE_BYTES` consumer sizing |
| Bounded concurrency across items within one message | A `Reimbursement` message already carries exactly one logical unit of work (one `uuid`) — no per-message fan-out exists to bound |
| Metrics infrastructure, counters, alerting rules | Same gap as **R-004**; this feature emits structured, countable log events only |
| Authentication / authorization | `SCOPE.md:295-297` — internal consumer, no external surface |
| LangFuse tracing | `SCOPE.md:276-280` scopes it to the Agent's LLM steps, which are out of scope here |
| Multi-instance / partition-count tuning | Kafka consumer-group partitioning plus row-level `UPDATE`/read atomicity already provide cross-instance safety; no new coordination mechanism is added |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here — nothing is left silently unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | --------------- | --------- | ---------- |
| Error-handling depth | Full retry/republish/human-review loop, not detect-and-log-only | Mirrors `SCOPE.md`'s Agent Error Handling section; a resolution-stage failure must not silently break the no-loss guarantee before the decision layer even exists | y |
| Staleness check inclusion | Included in this feature | Only needs the row this feature already fetches — no dependency on decision logic | y |
| Shared persistence sequencing | This feature **blocked on `publisher-consume-request`** landing `shared/reimbursement/repository.py` / `shared/failure_log.py` / `shared/reimbursement/use_cases/send_human_review.py` first, then extends them. **Resolved 2026-08-08**: that feature shipped and validated (PASS) — the block is lifted, Execute is unblocked | Avoided duplicating a module another in-flight feature already specs in detail. Signatures re-verified against the committed code during this recalibration — no mismatches | y |
| Republish target | `Reimbursement` itself (the only topic the agent owns) | The agent has no equivalent of the publisher's `Request` topic to republish to | y |
| Retry-ceiling check ordering | `retry > 3` checked before attempting the normal resolve/staleness flow | Mirrors the publisher's own per-envelope check-before-processing ordering | y |
| `retry > 3` fallback mechanism | `UPDATE reimbursement SET status = 'human-review', decision_reason = ... WHERE uuid = ...` | The row already exists (the publisher inserted it) — unlike the publisher's own fallback, which `INSERT`s a new row | y |
| Ghost + `retry <= 3` | Log, drop, commit offset — no error, no republish, no failure-log entry | **R-001**'s already-recorded mitigation: retrying a genuine ghost can never resolve it; treating it as retryable would eventually pollute `human-review` with rows that don't exist | y (R-001) |
| Ghost + `retry > 3` (the human-review `UPDATE` affects 0 rows) | Write to the failure log | Same fallback as "the human-review `UPDATE` itself fails" — a ghost that also exhausted retries still needs a durable record | y |
| Success handoff | Log resolution, commit offset — no stub hook or call point into the not-yet-built processing stage | User: scope is "just consume the message, get the data from DB and validate the date" — nothing else | y |
| `published_at == updated_at` (exact tie) | **Not** stale — processed normally | `SCOPE.md:229` says "lower than," not "lower than or equal to"; literal reading, undiscussed | n |
| DB failure permanence classification | All DB failures on the resolve/staleness/human-review-update path are transient/retryable | No analogue to the publisher's "unique violation = permanent" case exists for a `SELECT`/`UPDATE`-by-`uuid` | n |
| Per-message concurrency | Sequential, one message at a time — no bounded-concurrency requirement | Unlike the publisher (N items per `Request` message), one `Reimbursement` message already carries exactly one logical unit of work | n |
| Consumer fetch-size tuning | Not needed | `Reimbursement` messages are small fixed-shape envelopes, unlike `Request`'s large batch payloads | n |
| Crash-redelivery idempotency | Naturally idempotent — re-running the same read-then-conditionally-write is safe by construction | No unique-constraint-style collision exists on the read/update path, unlike the publisher's `INSERT` path | n |
| Log severity for genuine failures ("HIGH", so the monitoring tool triages correctly) | Maps to `logging.ERROR` for the two stdout log lines this feature owns directly (transient resolve failure before republish; `retry > 3` human-review escalation). Ghost/`uuid`-not-found and stale-ignored stay informational — explicitly excluded, since R-001 defines a ghost as an accepted non-error condition, not a failure | User decision. `shared.failure_log` (republish-itself-fails, `retry > 3` + ghost, `retry > 3` genuine `UPDATE` failure, malformed input) already writes at `CRITICAL`, strictly above `ERROR` — those paths already clear the bar via the publisher's already-decided failure-log convention and need no separate change | y |

**Open questions:** none — the five rows marked **n** are recorded
assumptions with a chosen default and rationale; each is independently
reversible (a comparison operator, a log classification, a concurrency
knob, a consumer config, or a note) and none reshapes the spec.

---

## User Stories

### P1: A `Reimbursement` message resolves to its row, or is safely recognized as unresolvable ⭐ MVP

**User Story**: As the system, I want each `Reimbursement` message resolved
to its `reimbursement` row by `uuid`, or safely dropped if no row exists,
so that the pipeline stays durable end to end and a dual-write race
(**R-001**) never pollutes downstream state.

**Why P1**: Without this, the Agent cannot begin any future decision work —
every message would either crash the consumer or silently vanish.

**Acceptance Criteria**:

1. WHEN the agent consumes a `Reimbursement` message with `retry <= 3` THEN
   it SHALL query the `reimbursement` table by the message's `uuid`.
2. WHEN the query returns a matching row THEN the system SHALL proceed to
   the staleness check (P1 story below).
3. WHEN the query returns no matching row THEN the system SHALL treat it
   as a ghost (**R-001**): log the occurrence at informational level (not
   `logging.ERROR` — a ghost is an accepted, non-error condition, not a
   failure) and drop the message — no republish, no retry, no failure-log
   entry.
4. WHEN a ghost is dropped, or a row is resolved and handled to completion
   (stale-ignored, or resolved-and-logged) THEN the consumer offset SHALL
   be committed for that message.

**Independent Test**: Publish a `Reimbursement` message whose `uuid` matches
a real row; assert the row is fetched and no error is raised. Publish a
second message with a `uuid` matching no row; assert it is logged, dropped,
and the offset advances, with no republish and no failure-log entry.

---

### P1: A stale message is ignored ⭐ MVP

**User Story**: As the system, I want a message that is older than the
row's last update ignored, so that an out-of-order or superseded message
never reprocesses a row that has already moved on.

**Why P1**: `SCOPE.md:229` — an explicit constraint on the Agent.

**Acceptance Criteria**:

1. WHEN a resolved row's `updated_at` is strictly greater than the
   message's `published_at` THEN the system SHALL ignore the message: log
   it and take no further action.
2. WHEN the message's `published_at` is greater than or equal to the row's
   `updated_at` THEN the system SHALL treat the message as fresh and
   proceed (logging resolution success; no decision logic in this
   feature).
3. WHEN a message is ignored as stale THEN the consumer offset SHALL still
   be committed for that message.

**Independent Test**: Resolve a row, then feed a `Reimbursement` message
for that `uuid` with `published_at` before the row's current `updated_at`;
assert it is logged as ignored and no further action is taken. Feed a
second message with `published_at` at or after `updated_at`; assert it is
treated as fresh.

---

### P1: A resolution-stage failure rolls back and retries via republish, carrying its error history ⭐ MVP

**User Story**: As the system owner, I want a resolution-stage DB failure
to retry automatically and accumulate a record of what went wrong, so that
a transient failure never breaks the no-loss guarantee before the decision
layer even runs.

**Why P1**: `SCOPE.md`'s Agent Error Handling section; without this, a
transient DB outage during resolution would either crash the consumer or
silently drop the message.

**Acceptance Criteria**:

1. WHEN a DB error occurs while querying for the row (not "no row found" —
   a genuine query/connection failure) THEN the system SHALL republish a
   new `Reimbursement` message for that `uuid` with `retry` set to the
   consumed message's `retry + 1`.
2. WHEN that republished message is built THEN its `errors` list SHALL
   equal the consumed message's `errors` with **one new entry appended**,
   recording the attempt number, an RFC 3339 UTC timestamp, the failing
   stage, the error type, and the error message.
3. WHEN an item has failed on multiple attempts THEN every prior `errors`
   entry SHALL still be present — entries are appended, never overwritten
   or truncated.
4. WHEN the republished message is later consumed THEN it SHALL be
   processed exactly like any other `Reimbursement` message, with no
   special-casing beyond `retry` being non-zero.
5. WHEN a failure is logged to stdout THEN it SHALL be logged at
   `logging.ERROR` (so the monitoring tool triages it correctly), the
   record SHALL carry the message's `uuid` and the error type, and it
   SHALL NOT carry driver-supplied value-bearing detail (matching the
   publisher's PII-sanitization rule).
6. WHEN the republish itself fails THEN the system SHALL write the item
   and its error history to the failure log, and SHALL treat the item as
   handled (offset committed).

**Independent Test**: Inject a fake DB client that raises on the
resolve query; assert no crash occurs, a new single-item `Reimbursement`
message was published with `retry` incremented and exactly one `errors`
entry naming the resolve stage. Feed that message back in with a second
injected failure and assert the message carries **two** entries in order.

---

### P1: `retry > 3` escalates the row to `human-review`, explained ⭐ MVP

**User Story**: As a human reviewer, I want a `Reimbursement` message that
failed repeatedly during resolution to update its row to `human-review`
with an explanation, so that I can act without reading server logs — and
so a ghost that never resolved still leaves a durable trace.

**Why P1**: `SCOPE.md`'s Agent Error Handling section's last line of
defense — mirrors the publisher's identical `retry > 3` requirement, and a
row updated with no explanation is not reviewable.

**Acceptance Criteria**:

1. WHEN a consumed `Reimbursement` message has `retry > 3` THEN the system
   SHALL NOT attempt the normal resolve/staleness flow — it SHALL attempt
   to `UPDATE` the row's `status` to `'human-review'` by `uuid`.
2. WHEN that `UPDATE` affects exactly one row THEN its `decision_reason`
   SHALL contain a human-readable rendering of **every** entry in the
   message's `errors` list — each attempt's number, timestamp, failing
   stage, error type, and message — not merely a count or a summary.
3. WHEN the message's `errors` list is empty or absent despite
   `retry > 3` THEN `decision_reason` SHALL still state that the retry
   ceiling was reached and that no error detail was carried, rather than
   being left null.
4. WHEN the `UPDATE` succeeds THEN the system SHALL log the escalation at
   `logging.ERROR` (so the monitoring tool triages it correctly) and take
   no further action for that item (offset committed; no republish).
5. WHEN the `UPDATE` affects zero rows (the `uuid` is a ghost) THEN the
   system SHALL write the item and its full error history to the failure
   log instead, and take no further action.
6. WHEN the `UPDATE` itself fails on a genuine DB error (not the
   zero-rows-affected case) THEN the system SHALL write the item and its
   full error history to the failure log, and take no further action.

**Independent Test**: Feed a `Reimbursement` message with `retry = 4` and
three distinct `errors` entries for a `uuid` that resolves to a real row;
assert the row's `status` becomes `human-review` and `decision_reason`
contains all three error messages and all three stage names. Repeat with a
`uuid` matching no row and assert a failure-log entry carries the same
three entries instead of an `UPDATE`.

---

### P1: Bad input never stops the consumer ⭐ MVP

**User Story**: As the system owner, I want an unparseable or invalid
`Reimbursement` message logged and skipped rather than crashing the
consumer, so that one bad message never blocks every message behind it.

**Why P1**: Mission-critical means the consumer loop must survive bad
input (`SCOPE.md:19-21`), exactly as required of the publisher.

**Acceptance Criteria**:

1. WHEN a consumed message is not valid JSON, or does not match the
   `Reimbursement` message schema THEN the system SHALL write it to the
   failure log and SHALL NOT attempt a DB query, an `UPDATE`, or a
   republish.
2. WHEN any case in this feature's other stories completes — resolved and
   logged, ghost-dropped, stale-ignored, republished, escalated to
   `human-review`, or written to the failure log — THEN the consumer
   offset SHALL be committed for that message and the consumer loop SHALL
   continue processing subsequent messages without interruption.
3. WHEN the process receives a termination signal mid-transaction THEN the
   in-flight transaction SHALL roll back and the offset SHALL NOT be
   committed for the message being processed.

**Independent Test**: Feed the consumer a non-JSON body and a
schema-violating message; assert each is written to the failure log with
no DB attempt and no republish, and that a subsequent valid message is
still processed normally.

---

## Edge Cases

- WHEN two agent instances consume different partitions of `Reimbursement`
  concurrently THEN correctness SHALL rely on Kafka's consumer-group
  partition assignment and the row-level `UPDATE`/read's natural
  idempotency — no cross-instance coordination is added.
- WHEN a `Reimbursement` message is redelivered after a crash that occurred
  between an action (a resolved log, a `human-review` `UPDATE`) and the
  offset commit THEN redelivery SHALL re-run the same read-then-
  conditionally-write safely — an already-`human-review` row `UPDATE`d
  again is a no-op in effect, and an already-logged resolution is logged
  again, with no corrupted state.
- WHEN `retry` climbs past 3 through unrelated transient DB errors (not
  through ghost detection, which never increments `retry` per this
  feature's ghost handling) and the row genuinely does not exist THEN the
  `retry > 3` `UPDATE` SHALL discover zero rows affected and route to the
  failure log, per the P1 `retry > 3` story's AC5.
- WHEN the same error recurs on all resolution attempts THEN the
  resulting `decision_reason` SHALL show that many entries rather than
  collapsing them, so a reviewer can tell a permanent fault from a
  flapping one — matching the publisher's identical requirement.
- WHEN the broker is unreachable at consume time THEN the system SHALL
  rely on librdkafka's reconnect behavior; nothing is lost because nothing
  was polled and committed.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ------ | ------- |
| AGT-01 | P1: Resolve or recognize unresolvable (query by uuid) | Design | Pending |
| AGT-02 | P1: Resolve or recognize unresolvable (row found → staleness check) | Design | Pending |
| AGT-03 | P1: Resolve or recognize unresolvable (ghost dropped, no error) | Design | Pending |
| AGT-04 | P1: Resolve or recognize unresolvable (offset committed once handled) | Design | Pending |
| AGT-05 | P1: Staleness (older published_at ignored) | Design | Pending |
| AGT-06 | P1: Staleness (fresh message proceeds) | Design | Pending |
| AGT-07 | P1: Staleness (offset committed on ignore) | Design | Pending |
| AGT-08 | P1: Retry/republish (transient DB error republishes with retry+1) | Design | Pending |
| AGT-09 | P1: Retry/republish (error entry appended with stage, type, message) | Design | Pending |
| AGT-10 | P1: Retry/republish (prior entries preserved, never truncated) | Design | Pending |
| AGT-11 | P1: Retry/republish (republished message processed normally) | Design | Pending |
| AGT-12 | P1: Retry/republish (stdout log sanitized) | Design | Pending |
| AGT-13 | P1: Retry/republish (republish failure falls back to the failure log) | Design | Pending |
| AGT-14 | P1: `retry > 3` (skip normal flow, attempt human-review UPDATE) | Design | Pending |
| AGT-15 | P1: `retry > 3` (decision_reason renders full error history) | Design | Pending |
| AGT-16 | P1: `retry > 3` (decision_reason non-null even with no history) | Design | Pending |
| AGT-17 | P1: `retry > 3` (stop on success — no republish) | Design | Pending |
| AGT-18 | P1: `retry > 3` (zero rows affected → failure log) | Design | Pending |
| AGT-19 | P1: `retry > 3` (genuine UPDATE failure → failure log) | Design | Pending |
| AGT-20 | P1: Bad input (malformed message logged, no side effect) | Design | Pending |
| AGT-21 | P1: Bad input (offset committed + consumer loop survives, every outcome) | Design | Pending |
| AGT-22 | P1: Bad input (termination signal rolls back, offset uncommitted) | Design | Pending |

**Coverage:** 22 total, 0 mapped to tasks, 22 unmapped ⚠️ (Design complete
as Draft; Tasks phase pending. No longer blocked — `publisher-consume-request`
has shipped, see Assumptions)

---

## Success Criteria

- [ ] A `Reimbursement` message for a real, fresh row is resolved and
      logged, with no crash and no side effect beyond that log.
- [ ] A `Reimbursement` message whose `uuid` has no row (a ghost) is
      dropped, logged, and never republished or escalated.
- [ ] A stale `Reimbursement` message is ignored without touching the row.
- [ ] A transient resolution failure republishes with an incremented
      `retry` and an appended, non-truncated error history.
- [ ] An item that failed resolution four times updates its row to
      `human-review` with a `decision_reason` naming all four failures —
      or, if the row is a ghost, lands in the failure log instead.
- [ ] A malformed message never stops the consumer from processing the
      next one.
