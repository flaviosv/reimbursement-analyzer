# Agent — Consume `Reimbursement`, Resolve by UUID — Context

**Gathered:** 2026-08-08
**Spec:** `.specs/features/agent-consume-reimbursement/spec.md`
**Status:** Ready for design (blocked — see Feature Boundary)

---

## Feature Boundary

The `agent` service (`src/agent/`): consume the `Reimbursement` Kafka topic
(written by the publisher, see `publisher-consume-request`), resolve each
message to its `reimbursement` row by `uuid`, apply the staleness guard
(`SCOPE.md:229`), and handle every way that resolution step can fail — DB
errors, a `uuid` with no row (R-001's ghost case), retry-ceiling escalation
to `human-review`, and malformed input. **No decision logic** (reject /
auto-approve / deterministic / probabilistic / human-review-with-LLM-note,
`SCOPE.md:232-266`) — that is a separate, later feature ("processing"),
explicitly deferred by the user to a future session.

---

## Implementation Decisions

### Error-handling depth

- **Full retry/republish/human-review loop**, not detect-and-log-only.
  Mirrors `SCOPE.md`'s Agent "Error Handling" section literally: on error,
  rollback and republish to `Reimbursement` with `retry + 1`; on
  `retry > 3`, escalate to `human-review`.
- Rationale: a resolution-stage failure (e.g. the DB is down) must not
  silently break the no-loss guarantee just because it happens before the
  decision layer exists.

### Staleness check — included in this feature

- `SCOPE.md:229`: ignore the message if its `published_at` is lower than
  the row's `updated_at`. Included because it only needs the row this
  feature already fetches — no dependency on decision logic.
- User note (superseded during Design, recorded for history): the initial
  intent was a vertical slice mirroring `AD-009`'s pattern in `api`
  (`agent/reimbursement/resolve/`), with `consumer.py` at the package root
  as the entry point. **During Design, the user rejected the
  `reimbursement/` nesting** — `agent`'s domain is exclusively
  reimbursement, so a resource-name directory adds a level with nothing to
  disambiguate, unlike `api/reimbursement/create/` where `reimbursement`
  distinguishes among potential future resources. Landed shape: a flat
  `agent/src/agent/validation.py` beside `consumer.py`, preserving the
  original testability goal (decision tree separable from the Kafka loop)
  without the extra nesting. See `design.md`'s Tech Decisions.

### Shared persistence layer — this feature is a consumer of it, not the builder

- `STATE.md` reserves AD-017/AD-025 for "the Agent feature," and
  `publisher-consume-request` (a separate, parallel session) built the
  shared modules this feature needs: `shared/reimbursement/repository.py`,
  `shared/failure_log.py`, `shared/reimbursement/use_cases/send_human_review.py`,
  and `ReimbursementEnvelope`/`AttemptError` in `shared/models.py`.
- **Decision (as made): this feature blocked on `publisher-consume-request`
  landing those modules first**, rather than building them here. This
  feature *extends* `repository.py` with a read-by-uuid statement and a
  human-review UPDATE statement (distinct from the publisher's INSERT-based
  human-review fallback) — it does not construct the module from scratch.
- **Resolved 2026-08-08 (later session):** `publisher-consume-request`
  shipped 21 commits, fully implemented and validated (PASS recorded). Ran
  `architecture-evaluate` to sync `docs/codebase/` and re-verified every
  signature this feature's design assumed directly against the committed
  code, not the draft — no mismatches found. **The block is lifted — Execute
  is unblocked.** Two small additive gaps remain, both are this feature's
  own first tasks, not blockers: widening `shared.models.Stage`'s `Literal`
  to add `"resolve"`, and adding `AgentConfig`/`Config.agent` to
  `shared/config.py`. See `design.md`'s Risks & Concerns.

### Retry / human-review mechanics — adapted from the publisher's, not copied

- Republish target is `Reimbursement` itself — the only topic the agent
  owns (unlike the publisher, which republishes to `Request`).
- `retry > 3` is checked **before** attempting the normal resolve/staleness
  flow, mirroring the publisher's own check-before-iterating ordering
  (`publisher-consume-request/design.md`'s `parse -->|retry > 3| esc`
  step).
- Because the publisher already `INSERT`ed the row, the agent's
  `retry > 3` fallback is an **`UPDATE ... SET status = 'human-review'`**
  by `uuid` — not a fresh `INSERT` like the publisher's own fallback.

### Ghost handling (`uuid` with no matching row) — two distinct cases

- **`retry <= 3` and no row found:** per R-001's already-recorded
  mitigation, this is *not* an error. Log it, drop the message, commit the
  offset. No republish, no retry, no failure-log entry — retrying a genuine
  ghost can never resolve it (the row will never appear), so treating it as
  a transient failure would misclassify it and eventually pollute
  `human-review` with rows that don't exist.
- **`retry > 3` and the `human-review` `UPDATE` affects zero rows (ghost):**
  there is nothing to escalate. Write the item to the failure log instead —
  same fallback as "the human-review UPDATE itself fails" — so a ghost that
  also exhausted retries still leaves a durable record.
- These two cases are reachable independently: a ghost is normally detected
  and dropped immediately (first case), but `retry` can still climb past 3
  through unrelated *transient* DB errors (e.g. connection timeouts) before
  the agent ever gets a definitive "row not found" answer — at which point
  the `retry > 3` path's `UPDATE` is what discovers the ghost, landing in
  the second case.

### Success handoff — nothing beyond this feature's own scope

- User: "the scope of this task is just consume the message, get the data
  from DB and validate the date, other steps is gonna be done in another
  moment." On success (row found, not stale), the feature logs the
  resolution and commits the offset — **no stub hook, no call point into
  the not-yet-built processing stage.** The processing session wires its
  own entry when it lands; this feature does not need to anticipate its
  shape.

### Log severity ("HIGH", so the monitoring tool triages correctly)

- **Maps to `logging.ERROR`** — no existing severity convention in this
  codebase beyond stdlib logging (`.error()`/`.info()`/`.exception()`,
  verified by grep across `src/`); no "HIGH" tag or structured severity
  field exists to reuse.
- **Scope: only genuine failures, not the ghost case.** Applies to the two
  stdout log lines this feature owns directly: the transient
  resolve-failure log before republishing, and the `retry > 3`
  human-review-escalation log. A ghost (`uuid` with no row) stays
  informational — R-001 already defines it as an accepted, non-error
  condition, and elevating it to `ERROR` would flag an expected race as if
  it were a bug. Stale-ignored likewise stays informational (a defined,
  expected skip per `SCOPE.md`'s Constraints, not a failure).
- **`shared.failure_log` writes need no separate change.** Republish-itself-
  fails, `retry > 3` + ghost, `retry > 3` genuine `UPDATE` failure, and
  malformed input all already route through `shared.failure_log`, which
  `publisher-consume-request` already specs at `CRITICAL` — strictly above
  `ERROR`, so those paths already clear the "HIGH" bar via that
  already-decided convention.

### Agent's Discretion

- Exact log message wording and structured-event field names for each
  outcome (ghost drop, stale drop, resolved, republish, human-review
  escalation, failure-log write) — should follow the publisher's
  precedent (stable event name + `uuid` + `retry`), but exact field names
  beyond the now-fixed severity are implementation detail.
- Whether the vertical slice's internal module names mirror
  `reimbursement/create/` (`route.py`/`validation.py`/`producer.py`-style
  splits) or use different internal names — Design's call, as long as
  `consumer.py` stays the root-level entry point per the user's stated
  layout.

---

## Declined / Undiscussed Gray Areas → Assumptions

Recorded in the spec's Assumptions & Open Questions table:

- `published_at == updated_at` (exact tie) → **not** stale, processed
  normally. `SCOPE.md:229` says "lower than," not "lower than or equal
  to"; undiscussed, resolved by literal reading.
- All DB failures during the resolve/staleness/human-review-update path
  are treated as transient/retryable — there is no analogue to the
  publisher's "unique violation = permanent" classification, since a
  `SELECT`/`UPDATE`-by-`uuid` has no comparable permanent-failure class.
- Sequential, one-message-at-a-time processing — no bounded-concurrency
  requirement. Unlike the publisher (N items per `Request` message), one
  `Reimbursement` message already carries exactly one logical unit of work.
- No consumer fetch-size tuning — `Reimbursement` messages are small,
  fixed-shape envelopes (`uuid`/`retry`/`published_at`/`errors`), unlike
  `Request`'s large batch payloads that motivated `KAFKA_MAX_MESSAGE_BYTES`
  sizing on the publisher's consumer.
- Redelivery after a crash (offset never committed) is naturally
  idempotent — re-running the same read-then-conditionally-write is safe
  by construction, with no unique-constraint-style collision to guard
  against, unlike the publisher's `INSERT` path.

---

## Specific References

- `docs/SCOPE.md:223-274` is the Agent's contract of record (staleness
  constraint, reject/auto-approve/human-review rules — the latter two out
  of this feature's scope — and the Error Handling section this feature
  implements).
- `.specs/RISKS.md` R-001 defines the ghost-tolerance mitigation this
  feature is required to implement; R-005's throughput discussion explains
  why per-message concurrency isn't needed here (no per-message item fan-out).
- `.specs/features/publisher-consume-request/spec.md` and `design.md`
  (v3, Draft) define the `Reimbursement` message shape (AD-015) and the
  shared modules this feature depends on and extends.
- `src/api/src/migrations/0001.create-reimbursement.sql` is the schema of
  record — `status`, `decision_reason`, `updated_at` already exist; no
  migration is needed for this feature.
- `.specs/STATE.md` AD-017/AD-025 (reserved for the Agent feature),
  AD-014/AD-015 (envelope/message contract).

---

## Deferred Ideas

- All decision/processing logic (`SCOPE.md:232-266`) — explicit next
  session, per the user.
- Building `shared/reimbursement/repository.py` /
  `shared/failure_log.py` / `shared/reimbursement/use_cases/send_human_review.py`
  from scratch — owned by `publisher-consume-request`; this feature is
  blocked on it landing.
- Per-message concurrency / multi-instance tuning for the Agent's own
  consumer — not requested, not motivated by the current message shape.
- A real metric behind ghost-drop / stale-drop counts, beyond structured
  log events — same interim-measure gap as R-004 for the publisher.
