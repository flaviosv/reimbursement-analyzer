# Publisher Compensating-Delete Specification

## Problem Statement

The publisher currently inserts a `reimbursement` row and publishes its
`Reimbursement` message inside one DB transaction (`docs/SCOPE.md:241-252`),
relying on Postgres's own rollback if the publish fails. This is correct but
holds a transaction open across a Kafka round-trip, and the project has
accepted (`R-001`, `.specs/RISKS.md`) that a narrow crash window between the
broker's ack and the commit can still leave a ghost message (row rolled back,
message already sent) or a duplicate row (commit fails after a successful
publish).

Time constraints rule out closing that window properly via a transactional
outbox (R-001's documented proper fix — a new `outbox` table, a relay
process, its own lifecycle and retry logic). This feature replaces the
transactional design with a simpler one: commit the insert immediately,
attempt the publish, and if it fails, explicitly delete the row before
requeueing. Traced precisely, this is not a straight downgrade — inserting
before attempting the publish means there is no window where the message is
out but the row doesn't exist yet, so it **closes** R-001's ghost-message and
duplicate-via-commit-failure cases outright. What it opens instead is
narrower: an un-recoverable orphaned row, possible only if the process
crashes between a publish failure and the compensating delete completing —
and, unlike the cases it closes, that one is not automatically safe, so this
feature must make it durably traceable rather than silent.

## Goals

- [ ] Insert commits before the `Reimbursement` publish is attempted — no
      open transaction spans the Kafka round-trip.
- [ ] A publish failure deletes the just-inserted row before the existing
      requeue path fires, with no change to requeue/retry/duplicate-detection
      behavior otherwise.
- [ ] Every compensating delete outcome (succeeded, affected zero rows, or
      itself failed) is durably logged with enough context to reconstruct
      what happened — satisfies this project's hard traceability
      requirement (`CLAUDE.md`).
- [ ] `docs/SCOPE.md`'s Reimbursement Publisher / Error Handling section and
      `.specs/RISKS.md`'s R-001 are amended in place to describe the new
      design and its actual residual risk, matching the AD-020/AD-027/AD-028
      precedent of correcting the written spec rather than leaving it to
      silently contradict shipped code.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| --- | --- |
| Transactional outbox pattern | The proper fix R-001 already names; time-boxed out for this feature. Still the recommended future direction — not superseded, only deferred further. |
| Automatic recovery/reconciliation of an orphaned row | If the compensating delete itself fails after a crash, there is no auto-heal in this feature — the row is logged as discoverable, not repaired. A future sweep job is a candidate, not built here. |
| R-005 connection-pool sizing | This feature does not change how long the pool connection is held (still acquired for the full insert→publish→[delete] span) and makes no performance claim. |
| Changes to the Agent's ghost-tolerance logic (`GHOST_DROPPED_EVENT`) | Still correct as defense-in-depth even though this design narrows how often a ghost message can occur (only the pre-existing publish-timeout false-negative case remains, unchanged from today). |
| R-008 (batch-internal uniqueness) / R-009 (backoff, bulk redesign) | Unrelated open risks; untouched by this feature. |
| `escalate_item`'s direct `human-review` insert path | Already its own transaction, already correct, not touched — this feature only changes `process_item`'s insert+publish path. |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here — nothing is left silently unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --- | --- | --- | --- |
| Delete's `WHERE` clause | `WHERE uuid = $1 AND status = 'pending'`, not a bare `uuid` match | Defense-in-depth: nothing else can plausibly write to this row before the Agent ever sees its uuid (publish hasn't succeeded), but gating on status costs nothing and matches the codebase's existing defensive style (`approve`/`reject` gate on eligible statuses) | n (low-stakes default) |
| Connection reuse for the delete | Same `conn` already acquired for insert+publish, no second `pool.acquire()` | Simplest; avoids a second acquire-timeout failure mode mid-compensation | n (low-stakes default) |
| New structured log event names | `reimbursement.compensating_delete` (row removed), `reimbursement.compensating_delete_noop` (zero rows affected), `reimbursement.compensating_delete_failed` (delete itself raised) | Matches the existing `reimbursement.<snake_case>` event-naming convention already used for `duplicate_dropped`, `ghost_dropped`, etc. | n (low-stakes default) |
| `docs/SCOPE.md` amendment style | Inline dated note beside the existing bullets, not a rewrite | Matches AD-020/AD-027/AD-028 precedent exactly | y (user selected "amend in place" explicitly) |
| New AD number in `.specs/STATE.md` | Assigned at Execute time from the current tip of the Decisions log | Spec-time is too early to know the exact next number if other work lands first | n (deferred, not a spec-time decision) |
| False-negative publish timeout (broker actually delivered, client sees `PublishFailed`) | Not a new risk — `publish()`'s `PublishFailed` semantics are unchanged by this feature; the resulting ghost message is the same pre-existing case the Agent already tolerates | Confirmed by tracing: this ambiguity exists identically in the current transactional design (rollback fires on the same exception) | y (traced and confirmed during Specify, not merely assumed) |

**Open questions:** none — all resolved or logged above.

---

## User Stories

### P1: Insert commits immediately; a publish failure is compensated by an explicit, traceable delete ⭐ MVP

**User Story**: As the publisher's operator, I want the insert and the
`Reimbursement` publish decoupled from a shared transaction, so the write
path is simpler than a transactional outbox while every compensating action
still leaves a durable trace — because in a financial domain, a probabilistic
downstream decision must never rest on an untraceable state change.

**Why P1**: This is the entire feature — there is no smaller independently
demoable slice.

**Acceptance Criteria**:

1. WHEN the publisher processes a valid item THEN the system SHALL insert
   the `reimbursement` row and let it commit before attempting to publish
   the `Reimbursement` message — no `conn.transaction()` spans the publish
   call.
2. WHEN the publish succeeds THEN the system SHALL leave the inserted row as
   the sole durable record and return `ItemOutcome.PUBLISHED`, unchanged
   from today's observable behavior.
3. WHEN the publish raises `PublishFailed` THEN the system SHALL delete the
   just-inserted row (`WHERE uuid = $1 AND status = 'pending'`) before the
   existing requeue path runs.
4. WHEN the compensating delete affects exactly one row THEN the system
   SHALL emit a structured log record (`reimbursement.compensating_delete`)
   carrying the `uuid`, `request_id`, `retry`, and the publish error's type —
   sufficient to reconstruct why the row was removed without re-running
   anything.
5. WHEN the compensating delete affects zero rows THEN the system SHALL
   emit a distinct structured anomaly record
   (`reimbursement.compensating_delete_noop`) and proceed with requeue
   unchanged — this SHALL NOT raise or block the requeue.
6. WHEN the compensating delete itself raises an exception THEN the system
   SHALL write a durable `failure_log` record
   (`reimbursement.compensating_delete_failed`) carrying the `uuid`, the
   item, the original publish error, and the delete error — without raising
   further — so the orphaned row is discoverable by an operator even though
   this feature does not auto-recover it.
7. WHEN a publish failure occurs (compensated cleanly, a no-op delete, or a
   failed delete) THEN the system SHALL requeue the original item exactly as
   today: republish to `Request` with `retry+1` and the `AttemptError`
   history appended — this path is unchanged by this feature.
8. WHEN the DB insert itself fails (not the publish) THEN the system SHALL
   behave exactly as today — nothing was committed, nothing to compensate,
   existing requeue/duplicate-detection logic (`repository.is_duplicate`)
   applies unchanged.
9. WHEN this feature ships THEN `docs/SCOPE.md:241-252` SHALL carry an
   inline, dated note describing the insert-commits-immediately +
   delete-on-publish-failure design, in place of the single-transaction
   description, per the AD-020/AD-027/AD-028 precedent.
10. WHEN this feature ships THEN `.specs/RISKS.md`'s `R-001` SHALL record
    that the ghost-message and duplicate-via-commit-failure consequences are
    closed by this design, and that a new, narrower residual risk (an
    un-recoverable orphaned row, only on delete-failure-after-crash) is
    accepted in their place — distinguished explicitly from the closed
    cases, not merged into the same paragraph.

**Independent Test**: Kill the publish call with a forced `PublishFailed` in
an integration test against a real Postgres — assert the row is gone
(`get_by_uuid` returns `None`), the item was requeued with `retry=1`, and the
`compensating_delete` log event fired. Separately, force the delete itself to
raise (e.g. drop the connection) and assert the `failure_log` record lands
and requeue still happens.

---

## Edge Cases

- WHEN the publish call times out client-side but the broker actually
  delivered the message (a false negative) THEN the system SHALL behave
  exactly as today: the delete removes the row, producing the same
  ghost-message case the Agent already tolerates (`GHOST_DROPPED_EVENT`) —
  not a new regression, traced and confirmed identical to the current
  transactional design's own behavior on the same exception.
- WHEN the process crashes between the compensating delete's dispatch and
  its completion THEN the row remains committed, unpublished, and
  unretried — accepted as this feature's residual risk (see R-001 AC10),
  not auto-healed.
- WHEN `envelope.retry > MAX_RETRY` THEN this feature does not apply —
  `escalate_item` inserts directly at `human-review` status inside its own
  transaction, untouched by this change.
- WHEN the insert succeeds but the compensating delete races with a
  concurrent write to the same `uuid` (theoretically impossible today, since
  no other writer can reach a `uuid` the Agent has never received) THEN the
  `AND status = 'pending'` guard makes the delete a no-op instead of an
  incorrect removal, and AC5's anomaly log fires.

---

## Requirement Traceability

Each requirement gets a unique ID for tracking across design, tasks, and validation.

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| PCD-01 | P1: Insert commits before publish is attempted | T2, T3 | Implemented |
| PCD-02 | P1: Publish success unchanged | T2, T3 | Implemented |
| PCD-03 | P1: Publish failure triggers compensating delete | T1, T2 | Implemented |
| PCD-04 | P1: Delete-succeeded traceability | T2 | Implemented |
| PCD-05 | P1: Delete-no-op traceability | T2 | Implemented |
| PCD-06 | P1: Delete-failed traceability (failure_log) | T2 | Implemented |
| PCD-07 | P1: Requeue path unchanged | T3 | Implemented |
| PCD-08 | P1: DB-insert-failure path unchanged | T3 | Implemented |
| PCD-09 | P1: docs/SCOPE.md amendment | T4 | Implemented |
| PCD-10 | P1: R-001 amendment | T5 | Implemented |

**ID format:** `PCD-[NUMBER]` (Publisher Compensating Delete)

**Status values:** Pending → In Design → In Tasks → Implementing → Verified

**Coverage:** 10 total, 10 mapped to tasks, 0 unmapped — all implemented; final "Verified" status pending the Verifier sub-agent's independent pass (`.specs/features/publisher-compensating-delete/validation.md`)

---

## Success Criteria

- [ ] All PCD-01..10 acceptance criteria have a passing test asserting the
      spec-defined outcome (not implementation mirroring).
- [ ] Full publisher + shared test suite passes with the transaction-based
      `_insert_and_publish` removed and no regression in existing
      `publisher-consume-request` (PUB-*) coverage that doesn't depend on the
      removed transaction wrapper.
- [ ] `docs/SCOPE.md` and `.specs/RISKS.md` diffs are part of this feature's
      commit(s), not left for a follow-up.
