# Project Risks

Known problems that are **accepted, not solved**. Each entry states what
breaks, how likely it is, what it would take to close, and who owns the
decision. Append-only — resolve by changing Status, never by deleting.

Distinct from `STATE.md` Decisions (choices already made and binding) and
from a feature's Assumptions table (ambiguities resolved within one
feature's scope). A risk here is something that spans features or that we
have deliberately chosen to live with for now.

---

## R-001 — Dual-write window between the DB commit and the `Reimbursement` publish

**Raised:** 2026-08-07
**Status:** Partially closed by AD-033 (2026-08-09) — the ghost-message and duplicate-via-commit-failure consequences below no longer occur; a narrower residual risk is accepted in their place (see the AD-033 amendment below) — owner: Flavio
**Affects:** `publisher-consume-request`, `publisher-compensating-delete`, and the future Agent feature
**Severity:** As originally raised — low likelihood, moderate blast radius — for the two now-closed consequences below (kept for historical accuracy). **Current, post-AD-033 severity of the residual risk**: very low likelihood (requires a process crash inside the narrow window between a publish failure and the compensating delete completing — a compound of two independently low-probability events, narrower than the original window), low blast radius (one orphaned row, durably logged and discoverable via `failure_log`, not silent) — see the AD-033 amendment below for the full distinction.

### What breaks

The publisher holds a DB transaction open across the Kafka publish
(`SCOPE.md:219-220`, confirmed in `publisher-consume-request`):

```
BEGIN → INSERT ... RETURNING uuid → publish to `Reimbursement` → await ack → COMMIT
```

If the process dies **after** the broker acknowledges but **before** the
`COMMIT` lands, the transaction rolls back. The row disappears; the
`Reimbursement` message does not. Two consequences:

1. The Agent receives a message whose `uuid` has no corresponding row — a
   ghost referencing a transaction that never committed.
2. The original `Request` message is redelivered (its offset was never
   committed), the item is reprocessed, and a **new** row is created with a
   **different** uuid, which publishes a second `Reimbursement` message.

Net effect: the Agent can see two messages for one logical request, one of
which points at nothing.

### Why it cannot be ordered away

This is the classic dual-write problem. Postgres and Kafka cannot
participate in a single atomic transaction, so *some* window exists no
matter which order the two writes happen in. Publishing first only moves
which side is orphaned, and additionally violates `SCOPE.md:219` ("if the DB
insert fails, no publish must happen"). Committing first and publishing
best-effort trades this window for a worse one — a committed row that never
reaches the Agent at all.

The window here is the narrowest of the available orderings: it spans only
the time between the broker's ack and the local commit, typically low
single-digit milliseconds, and it requires a process death inside that
window specifically.

### What closing it properly requires

The **transactional outbox** pattern: insert the `reimbursement` row and an
outbox record in one genuine transaction, then have a separate relay read
the outbox and publish to `Reimbursement`, marking rows as sent. The DB
becomes the single source of truth and the publish becomes idempotently
replayable.

Cost: a new `outbox` table (the schema is owned by the already-merged
`db-schema-migrations` feature, so this is a cross-feature migration), a
relay process with its own lifecycle and retry logic, and the ordering
guarantees that go with it.

### Interim mitigation (in scope for the Agent feature, not yet specified)

The Agent must tolerate a `Reimbursement` message whose `uuid` has no row —
treat it as a ghost, log it, and drop it without error. Without this, the
Agent's error handling will escalate ghosts into human review and pollute
that queue with rows that do not exist.

**This mitigation is not yet written into any spec.** It needs to land in
the Agent's spec when that feature is specified.

### AD-033 amendment (2026-08-09) — what's closed vs. what's newly opened

`publisher-compensating-delete` (`.specs/features/publisher-compensating-delete/`)
replaces the design this risk describes: `insert_pending` now commits
immediately, and the `Reimbursement` publish is attempted only *after* that
commit — the reverse ordering from the `BEGIN → INSERT → publish → COMMIT`
sequence above. Because the row is already durable before the publish is
even attempted, **both consequences this risk originally named are closed
outright**: there is no window in which the Agent can receive a `Reimbursement`
message whose row was rolled back (consequence 1), and there is no window in
which a `COMMIT` failure after a successful publish produces a duplicate row
with a second uuid (consequence 2) — a `COMMIT` failure now can only occur
*before* the publish is attempted, in which case no publish happens at all.

This is a distinct, narrower risk, not the same one under a new name: an
un-recoverable **orphaned `pending` row**. It is possible only if the process
crashes in the specific window between a publish failure being detected and
the compensating `DELETE` (gated `WHERE uuid = $1 AND status = 'pending'`)
completing — a compound of two independently low-probability events, not the
common-path window the original design held open across every single publish.
Unlike the two closed consequences, this one is not automatically safe: the
row is not published, not retried, and not auto-recovered by anything in
`publisher-compensating-delete`'s scope. It is durably discoverable — every
compensating-delete outcome (removed, no-op, or itself failed) is logged, the
failed case via the durable `failure_log` sink — but an operator must act on
it manually. A future sweep/reconciliation job is a candidate fix, not built
here (see `publisher-compensating-delete/spec.md`'s Out of Scope table).

The **interim mitigation** above (the Agent tolerating a ghost `Reimbursement`
message) remains correct as defense-in-depth even though this design narrows
how often a ghost message can occur in the first place — the only remaining
path to one is the pre-existing publish-timeout false-negative case (the
broker actually delivered the message but the client saw `PublishFailed`),
unchanged by this amendment.

---

## R-002 — `submitted_at` accepted by the API can be rejected by the database

**Raised:** 2026-08-07
**Status:** Accepted, deferred — owner: Flavio
**Affects:** `api-post-reimbursement`, `db-schema-migrations`, `publisher-consume-request`
**Severity:** Low likelihood, low blast radius, but it is a live inconsistency between two merged pieces

### What breaks

Two already-decided pieces disagree about future-dated submissions:

- `api-post-reimbursement/spec.md` (Edge Cases) **explicitly declines** to
  bound a future `submitted_at`: "no requirement bounds it, and rejecting
  introduces a clock-skew failure mode".
- `0001.create-reimbursement.sql:43-44` caps it:
  `CHECK (submitted_at IS NULL OR submitted_at <= now() + interval '1 hour')`.

So a payload the API accepts with a `201` can fail the publisher's insert
with a CHECK violation. Because it is not a unique violation, it takes the
full generic failure path: 3 retries through Kafka, then a `human-review`
fallback insert — which fails the *same* CHECK — then the failure-log file.
The request is preserved, but only in a file, and it burns four round-trips
to get there.

### Options

1. Relax or drop `reimbursement_submitted_at_check` — matches the API's
   stated decision, costs one migration.
2. Bound `submitted_at` at the API edge to match the constraint — reverses
   the API spec's recorded decision and reintroduces the clock-skew failure
   mode it was rejected for.
3. Classify CHECK violations as permanent (like duplicates) so they
   short-circuit straight to the failure log instead of retrying. Narrower
   fix; leaves the two specs still disagreeing.

Not resolved by `publisher-consume-request`, which inherits the gap and
routes it through the standard DB-failure path.

---

## R-003 — The byte-verbatim audit copy lives only in the `Request` topic

**Raised:** 2026-08-07
**Status:** Accepted — owner: Flavio
**Affects:** `publisher-consume-request`, audit/traceability (`SCOPE.md:15`, `SCOPE.md:21`)
**Severity:** Low, but it invalidates an assumption worth stating out loud

### What breaks

`api-post-reimbursement` goes to real lengths to keep the request body
byte-for-byte intact from the socket to Kafka — the envelope is byte-spliced
specifically so key order, numeric literals, and unicode escaping survive
for the audit trail.

That property stops at the publisher. `reimbursement.original_payload` is
`JSONB`, which normalizes on write: key order is not preserved, insignificant
whitespace is dropped, numeric literals are canonicalized (`93.50` → `93.5`),
and unicode escapes are decoded. And as of the decision that the
`Reimbursement` message carries only the `uuid`, the payload is no longer
propagated downstream at all — the Agent reads it back from JSONB.

So the byte-exact original exists in exactly one place: the retained
`Request` topic log.

### Consequence

Kafka retention on `Request` is now an audit-retention policy, not just an
operational buffer. If `Request` retention is shorter than the audit window
the financial domain requires, the byte-exact original is gone and only the
JSONB-normalized copy remains.

No retention policy has been set anywhere in the project yet. That decision
should be made deliberately rather than inherited from a broker default.

### Options

1. Set `Request` topic retention explicitly, sized to the audit window.
2. Store the raw bytes alongside the JSONB (e.g. a `BYTEA` or `TEXT` column)
   — costs a migration and roughly doubles payload storage.
3. Accept the JSONB-normalized copy as the audit record of reference, and
   document that semantic equivalence — not byte equivalence — is the
   guarantee the system actually makes.
---

## R-004 — Duplicate drops are observable only as log lines, with no metric behind them

**Raised:** 2026-08-07
**Status:** Accepted, deferred — owner: Flavio
**Affects:** `publisher-consume-request`, observability project-wide
**Severity:** Medium — this is a blind spot, not a defect

### What breaks

`publisher-consume-request` short-circuits a unique-constraint violation on
`reimbursement_request_submitter_key`: the item is logged and dropped, with
no retry, no human-review row, and no failure-file entry. That is the
correct behaviour — retrying a duplicate can never succeed — but it means a
duplicate leaves **no durable artifact anywhere except a log line**.

Two very different situations produce that same silent drop, and neither is
distinguishable at a glance:

1. **Benign** — a client legitimately resubmitted, or the publisher crashed
   between committing a row and committing the Kafka offset, so redelivery
   correctly re-hit an already-processed item. This is the dedup path doing
   its job, and it is the crash-recovery mechanism the design depends on
   (see R-001).
2. **Pathological** — a client retry bug replaying the same batch in a loop,
   a stuck consumer reprocessing a partition, or a poison message cycling.
   Here the duplicate *rate* is the only symptom, and nothing surfaces it.

Without a counter, a system quietly discarding thousands of requests looks
identical to one discarding none. In a financial domain where the governing
constraint is that no request is lost (`SCOPE.md:293`), "we dropped it on
purpose" needs to be as visible as "we failed to process it".

### Interim mitigation (specified in `publisher-consume-request`)

The spec requires that every duplicate drop emit a **structured, uniquely
identifiable log record** — a stable event name plus the `request_id`, the
constraint that fired, and the envelope's `retry` value — so a log-based
monitor can count and alert on it without parsing prose. `SCOPE.md:303`
already assumes external monitoring tooling is in place; this makes the
event countable by it.

That is the floor, not the goal. It depends on log ingestion being
configured, retains nothing if it is not, and cannot express a rate
threshold on its own.

### What a better shape looks like

1. A real counter metric (`publisher_duplicate_dropped_total`, labelled by
   cause) exported to whatever the project adopts for metrics, with an alert
   on rate-of-change rather than absolute count.
2. Distinguishing the two cases above at the source: a redelivery-driven
   duplicate (benign, expected after a crash) is knowable — the envelope's
   `retry` value and the consumer's redelivery state both carry signal — and
   could be counted separately from a fresh client resubmission.
3. A durable record of dropped duplicates (a small audit table, or an entry
   in the existing failure log kept distinct from real failures) so the
   answer to "what did we discard last Tuesday" does not depend on log
   retention.

None of this is adopted now: the project has no metrics infrastructure yet,
and `SCOPE.md:300-303` explicitly defers monitoring to assumed external
tooling. Recorded so the blind spot is a known one.

---

## R-005 — Throughput parameters (500 items/batch, 10 concurrent) are starting values, not measured ones

**Raised:** 2026-08-07
**Status:** Accepted, revisit with production data — owner: Flavio
**Affects:** `api-post-reimbursement` (the cap), `publisher-consume-request` (the concurrency)
**Severity:** Low now, rises with volume

### What they are

Two coupled numbers, chosen together to bound how long one `Request` message
can occupy a consumer:

| Parameter | Value | Where it lives |
| --------- | ----- | -------------- |
| Max items per batch | 500 | API edge — rejected with `400` above this |
| Concurrent items in flight | 10 | Publisher — semaphore around the per-item unit of work |

They are coupled by design. Worst-case time to process one message is
`items ÷ concurrency × per-item-latency`:

```
500 ÷ 10 × ~15ms ≈ 0.75s     vs. max.poll.interval.ms = 300s   (~400x headroom)
```

That headroom is the whole point. It is what keeps a consumer from being
evicted mid-batch, which would revoke its partitions, fail the offset commit,
and force a group-wide rebalance (see "Why the headroom matters" below).

### Why these values are provisional

**Nothing here is measured.** The ~15ms per-item figure is an estimate for an
`INSERT` plus an `acks=all` Kafka round-trip awaited serially; real latency
depends on broker replication, disk, and network that do not exist yet. No
load test has been run, and no production traffic shape is known — neither the
typical items-per-batch nor the arrival rate.

Both numbers were picked to be obviously safe rather than optimal, and the
400× headroom means they can be wrong by a large factor and still work.

### Constraints on changing them

- **Concurrency is bounded by the connection pool, not by CPU.** Each in-flight
  item holds a DB transaction open across a Kafka round-trip (the R-001 design),
  so concurrency N pins N connections per publisher instance. At 10, three
  replicas need ~45 connections against Postgres's default `max_connections`
  of 100. Raising concurrency without raising the pool will starve; raising the
  pool without raising `max_connections` will fail to connect.
- **Raising the item cap narrows the headroom linearly.** 5000 items at
  concurrency 10 is ~7.5s — still fine. 50,000 at concurrency 10 is ~75s, and
  begins to matter once per-item latency degrades.
- **The cap is an API contract.** Raising or lowering it changes when clients
  get a `400`, so it cannot be tuned silently on the publisher side.

### Why the headroom matters (what a wrong value actually costs)

If one message's processing exceeds `max.poll.interval.ms`, the broker treats
the consumer as dead: partitions are revoked, the offset commit fails because
the consumer no longer owns the partition, and the message is redelivered.

This does **not** livelock — already-committed items are absorbed by the
duplicate path on redelivery (~1ms each, no Kafka publish), so each pass makes
real progress and it eventually converges. But it converges by thrashing:
repeated group-wide stop-the-world rebalances that stall every other partition
in the group while the offset sits uncommitted. It is a liveness failure that
presents as mysterious slowness, not as an error, which is exactly the kind
that is expensive to diagnose in production.

### What better looks like

1. Measure real per-item latency under load and re-derive both numbers from it
   rather than from an estimate.
2. ~~Make concurrency configurable and assert at startup that it does not
   exceed the configured DB pool size~~ — **done**: `PUBLISHER_ITEM_CONCURRENCY`
   and `DATABASE_POOL_MAX_SIZE` are both env-configurable, and
   `check_startup_config` refuses to boot when the pool cannot cover the
   concurrency (previously dead code — both were hardcoded literals that
   could never disagree).
3. Alert on processing time per message as a fraction of `max.poll.interval.ms`,
   so the headroom is observable before it is exhausted rather than after.

### Addendum 2026-08-08 — the "~400x headroom" figure only covers the happy
path; the failure-path worst case was tighter than `max.poll.interval.ms`

The `500 ÷ 10 × ~15ms` estimate above assumes every item succeeds quickly.
The failure path has its own, much larger budget: a publish that never gets
a delivery report waits the full `publish_timeout_seconds` (10s) before
failing over. Worst case — broker unreachable, every publish times out —
is `⌈500 ÷ 10⌉ × 10s = 500s` (~8.3 minutes). The previous
`max.poll.interval.ms` of `300_000` (5 minutes) did **not** cover this: a
sustained broker outage during a ceiling-sized batch would have triggered
the exact rebalance-thrashing failure mode this section already describes
as expensive to diagnose (found independently as **P2** in code review).

**Fix applied:** `max_poll_interval_ms` raised to `900_000` (15 minutes),
which leaves genuine headroom above the 500s worst case. This is a
config-only change (`src/publisher/src/config.py`) — it does not touch the
500-item cap or the concurrency of 10, and it costs only a slower detection
window for a genuinely dead consumer, which the offset-uncommitted design
already tolerates (redelivery is safe, per the duplicate path). The
happy-path "~400x" language elsewhere (`spec.md`'s Edge Cases section) is
corrected to note this is a happy-path-only figure.

---

## R-006 — Schema is owned by the `api` package, so no other layer can change it independently

**Raised:** 2026-08-08
**Status:** Open, needs evaluation — owner: Flavio
**Affects:** `db-schema-migrations`, the future Agent feature, `publisher-consume-request`, AD-001 / AD-007
**Severity:** Medium — no runtime failure mode; a release-coupling and ownership problem that bites the moment a second layer needs schema

### What breaks

The database schema is a **project-wide** asset — `api`, `agent`, and
`publisher` all read and write the same tables — but it is **owned by one
layer**:

| Artifact | Location | Owner |
| -------- | -------- | ----- |
| Migration runner | `src/api/src/migrate.py` | `api` |
| Migration SQL | `src/api/src/migrations/*.sql` | `api` |
| Migration image | `src/api/Dockerfile`, target `migrate` | `api` |

`src/agent` is an independent uv workspace member with no dependency on
`api`. It therefore has **no way to add a migration without editing the api
package**. An agent-only column change requires touching api's source tree,
api's build context, and api's image — then rebuilding and republishing that
image so the migrate Job can run the new DDL. Nothing in the API changed.

### Precision on the cost (it is not a runtime restart)

Migrations do **not** run in the API's request path. `docker-compose.yml:265-280`
runs them from a dedicated one-shot `migrate` service, and `api`, `agent`, and
`publisher` all gate on `service_completed_successfully`. AD-001 separated
execution from the API process deliberately, and that part is correct.

What was never separated is **ownership**. The `migrate` image is a build
target of `src/api/Dockerfile` (`src/api/Dockerfile:47-62`), built from api's
manifest and source tree. So the coupling is at build and release time, not at
runtime:

- The agent's schema change travels through the api package's review, CI, and
  versioning path.
- A new api image must be built and pushed for the migrate Job to carry the
  new SQL — in k3s, the Job's image tag is an api release artifact.
- The two layers cannot hold independent release cadences while one owns the
  other's schema.

### Second-order: blast radius through the startup gate

All three services gate on `migrate` completing. A migration authored for the
agent that fails — bad DDL, a lock timeout (`migrate.py:14`, 300s), a
constraint that rejects existing rows — blocks `api` and `publisher` from
starting as well. Ownership sitting in one layer while failure propagates to
all three is the asymmetry worth naming.

### Already visible in the register

R-001's mitigation (the transactional outbox) describes its `outbox` table as
"a cross-feature migration" owned by `db-schema-migrations`. That phrasing is
this risk surfacing early: a table needed by the publisher's correctness has
to be introduced through the api package.

### Related drift in the recorded decisions

AD-007 states migrations live at `src/api/src/api/migrations/` and are
resolved via `importlib.resources`, justified by the api wheel shipping the
`.sql` files. Neither still holds:

- `src/api/pyproject.toml:34` sets `package = false` — `api` is a **virtual,
  non-installed** workspace member, so there is no wheel to ship resources in.
- `migrate.py:24-28` resolves the directory from `Path(__file__).parent` and
  documents exactly that reason.
- The actual path is `src/api/src/migrations/`.

Whatever placement is chosen, AD-007 needs superseding rather than amending —
its rationale, not just its path, is out of date.

### Options

1. **Extract a dedicated schema workspace member** (`src/migrations`, or
   `src/schema`) with its own `pyproject.toml`, its own Dockerfile, and the
   yoyo/psycopg extras that currently hang off api's `[project.optional-dependencies]`.
   The migrate service builds from it; every layer submits SQL to a package
   none of them owns. Maps cleanly to a standalone k3s Job whose image is
   independent of any service release. Cost: one new package, one Dockerfile,
   a compose/k3s target change, and superseding AD-001 + AD-007.
2. **Move migrations into `shared`.** Every service already depends on it, so
   the files are reachable from anywhere and no new package appears. Cheapest
   structurally, but it puts DDL and its tooling inside a library imported on
   the request path, and dissolves the reviewer boundary — any layer can add a
   migration with no single owner reviewing schema as a whole. The extras must
   stay opt-in so the DDL tooling does not enter serving images
   (`src/api/pyproject.toml:19-26` deliberately keeps `psycopg[binary]` out).
3. **Accept and keep as-is.** Defensible while this is a single repo with one
   release pipeline: the coupling costs an image rebuild, not a broken
   deployment. It becomes real when `api` and `agent` gain independent
   cadences, or when the agent's schema needs iterate faster than the API's.

### Trigger to decide

The Agent feature is not yet specified and will need schema of its own (at
minimum, the columns behind the decision and staleness rules). Deciding
placement **before** that feature is specified avoids a second layer's
migrations landing inside the api package and hardening the coupling.

---

## R-007 — `retry > 3` escalates to `human-review` regardless of cause, mixing infra failures with genuine data problems

**Raised:** 2026-08-08
**Status:** Open, needs evaluation — owner: Flavio
**Affects:** `publisher-consume-request`, `agent-consume-reimbursement`
**Severity:** Medium — no runtime failure; a review-queue data-quality problem that grows with volume and with the length of any infra incident

### What breaks

Both the publisher's and the Agent's `retry > 3` fallback escalate
unconditionally to `human-review` once the ceiling is hit, regardless of
*why* every attempt failed. Each `AttemptError` entry already records an
`error type` and the failing `stage`, so the distinction between causes is
present in the data — but nothing acts on it before the row lands in
review. A request that failed four times purely because the DB or the
broker was flapping (`ConnectionError`/`TimeoutError` at a
`db-insert`/`publish`/resolve stage) lands in the exact same queue, with
the exact same `status`, as a request that failed because its data was
genuinely unprocessable. A human reviewer sees "this row needs a
decision," not "this row failed for infrastructure reasons and would very
likely have succeeded on a fifth attempt."

### Why it matters more as the system grows

The failure mode is correlated, not independent: a single infra incident
(a few minutes of DB connection exhaustion, a broker outage) can push many
concurrently-retrying items over the `retry > 3` ceiling at once, flooding
`human-review` with rows that have nothing wrong with their underlying
data. That noise can bury the genuinely-bad rows a reviewer actually needs
to judge, and burns review capacity on items that infra recovery alone
would have resolved for free had the retry ceiling been higher or the
classification been cause-aware.

### Options

1. **Classify at escalation time.** Split `AttemptError`'s causes into
   infra/transient (connection, timeout, broker unavailable) vs.
   permanent/data (validation, constraint violation) and only escalate the
   latter to `human-review`; an all-infra failure chain gets a different
   treatment instead (extended backoff, a distinct "infra-stalled" status)
   rather than consuming a review slot. Cost: a classification layer plus
   a new intermediate state or retry policy on both the publisher and the
   Agent.
2. **Keep escalating everything, but tag the cause.** `decision_reason`
   already renders the full error history in prose; add a coarse
   machine-readable category alongside it (or a `human_review` column) so
   a reviewer or a dashboard filter can deprioritize infra-caused rows
   without reading prose first. Cheaper than option 1, doesn't stop the
   pollution, only makes it filterable.
3. **Accept and keep as-is.** Defensible at current, unmeasured volume
   (mirrors **R-005**'s starting-values caveat) — no load or incident data
   yet shows this is a real problem in practice, only that it's a real gap
   in the design.

### Not resolved by either feature

Both `publisher-consume-request` and `agent-consume-reimbursement` keep
the current undifferentiated `retry > 3` → `human-review` escalation
as-is, per explicit user decision (2026-08-08) — recorded here for future
evaluation, not acted on now.

---

## R-008 — The API applies no cross-item uniqueness check within one batch

**Raised:** 2026-08-08
**Status:** Open, needs evaluation — owner: Flavio
**Affects:** `api-post-reimbursement`, `publisher-consume-request`
**Severity:** Low — a performance-only consequence today, not a correctness one

### What breaks

`POST /api/v1/reimbursement` validates each item independently but never
checks whether two items in the *same* batch share
`(request_id, lower(submitted_by))`. The publisher's insert transaction
stays open across the awaited Kafka publish (R-001's design) — so when a
batch legitimately contains such a pair, the first item holds the
unfulfilled unique-index tuple for the entire publish round-trip, and the
second serializes on Postgres rather than on the publisher's own
concurrency semaphore. That portion of the fan-out collapses to serial,
burning a pool connection and a concurrency slot to do nothing (worse
alongside any future fix to R-003/timeout handling, where the wait could
be materially longer). Found in code review (**P4**) against the
publisher's own logs, but the fix — if there is to be one — belongs at
the API edge, which is why it's recorded here rather than acted on in
`publisher-consume-request`.

### Options

1. **Reject at the API edge.** Validate batch-internal uniqueness on
   `(request_id, lower(submitted_by))` before publishing, returning `400`
   for a batch containing a duplicate pair. Matches the project's existing
   philosophy of validating at ingress; costs one O(n) pass over the batch.
2. **Accept and keep as-is.** The window only matters under real
   contention and currently only costs throughput, not correctness — the
   unique index still does its job either way. Revisit if P4's model
   proves wrong under load.

### Not resolved by `publisher-consume-request`

The publisher has no batch-internal view to check against — it processes
one item from the fan-out at a time by construction. Any fix here belongs
to `api-post-reimbursement`.

---

## R-009 — The requeue-and-per-item-insert design has no backoff and no bulk path

**Raised:** 2026-08-08
**Status:** Open, needs evaluation — owner: Flavio
**Affects:** `publisher-consume-request`
**Severity:** Medium — no failure today at documented volumes; both issues
compound with load and with the length of any DB/broker incident

### What breaks

Two related gaps in how the publisher moves items, both surfaced in code
review against the same underlying design (per-item transactions,
immediate republish on failure):

1. **No backoff on requeue (P5).** `_requeue` republishes immediately with
   no delay, jitter, or circuit breaker. During a sustained Postgres
   outage, one 500-item message becomes up to 2000 consumed messages
   (500 initial + up to 3 retries each) and 2000 connection attempts
   against the exact database that is already down, cycled as fast as the
   broker can serve them.
2. **Per-item N+1 (P6).** `insert_pending` is one `fetchval` per item
   inside its own acquire+BEGIN+COMMIT — 500 items is 500 separate
   round-trips, not one bulk insert. The per-item-transaction requirement
   (publish must sit inside the same transaction as its insert, R-001) is
   real, but the round-trip cardinality this costs was never weighed
   against a bulk-insert-then-batch-publish alternative that trades the
   same R-001 window for far fewer round-trips.

### User discussion (2026-08-08)

On P6: whether a genuine Kafka *batch* publish (one broker-level batch
containing several items' messages, not just several individual
`produce()` calls) could let a redesign keep the semaphore's concurrency
shape while processing in larger chunks (e.g. 10 processes × chunks of
10). Technically, `confluent_kafka`'s producer already batches at the
protocol level via `linger.ms`/`batch.size` under the hood for messages
produced close together in time — but that's an internal delivery
optimization, not something the application-level `_insert_and_publish`
unit of work can key correctness off of (the DB transaction still needs
to know per-item success/failure, and Postgres has no equivalent
"batch insert with per-row transactional linkage to N separate Kafka
messages"). A real redesign would most likely mean: bulk `INSERT ...
VALUES (...), (...), ...` for the whole chunk in one transaction, then
publish that chunk's N Kafka messages, accepting a wider R-001 window per
chunk (more items exposed to the dual-write gap at once) in exchange for
far fewer round-trips. That tradeoff needs its own design pass, not a
quick patch here.

### Options

1. **Add backoff to requeue.** Exponential backoff keyed on
   `envelope.retry`, ideally via a delay-topic scheme rather than a sleep
   (a sleep would consume the poll budget from R-005's headroom).
2. **Redesign around chunked bulk operations**, per the discussion above —
   bulk insert per chunk, batch-publish per chunk, wider per-chunk R-001
   window traded for far fewer round-trips. Largest change, needs its own
   design.
3. **Accept and keep as-is.** Defensible at current, unmeasured volume —
   mirrors R-005's starting-values caveat.

### Not resolved by `publisher-consume-request`

Both gaps are accepted as shipped behavior for this feature; the user
will evaluate a solution later, per the 2026-08-08 discussion above.

---

## R-010 — `shared.reimbursement.use_cases` is filling with single-consumer code

**Raised:** 2026-08-08
**Status:** Open, needs evaluation — owner: Flavio
**Affects:** `shared` (`reimbursement/use_cases/`), `api-get-reimbursement`,
`api-put-reimbursement`, `publisher-consume-request`
**Severity:** Low now, compounds with every feature that lands the same way

### What breaks

`CONVENTIONS.md:17` states the rule plainly: `shared` holds code with **more
than one real consumer across services** — everything with exactly one
consumer belongs in that consumer's own package. `shared.reimbursement.use_cases`
is not living up to it:

| Use case | Location | Real consumer(s) |
| -------- | -------- | ----------------- |
| `publish_pending` | `use_cases/publish_pending.py` | `publisher` only |
| `send_human_review` | `use_cases/send_human_review.py` | `publisher` only |
| `list_reimbursements` (specified, not yet built) | `use_cases/list_reimbursements.py` | `api` only |
| `review_reimbursement` (specified, not yet built) | `use_cases/review_reimbursement.py` | `api` only |

Every module in the directory has exactly one caller today. Nothing in
`use_cases/` is actually shared — each function was placed there because its
*data layer* (`shared.reimbursement.repository`, gated behind `shared.db.managed_pool`
per AD-029) is genuinely cross-service, and the use case sits one call above
it. But sharing the repository doesn't require sharing the orchestration
logic built on top of it — `list_reimbursements` and `review_reimbursement`
belong to `api-get-reimbursement`/`api-put-reimbursement`, which are
HTTP-only features per `CONVENTIONS.md`'s own "only `api` serves HTTP" rule
applied one layer down.

### Why it wasn't stopped earlier

AD-025 already named this exact tension when `send_human_review` first
landed: "the Agent is still a stub... if the Agent never materialises as a
second caller, `use_cases/` should collapse back into the slice's
`repository.py`" — explicitly flagged as a tracked bet, not an oversight.
That bet has not paid off yet (the Agent feature remains unspecified), and
the two new API-side designs are about to add two more single-consumer
modules to the same directory using the identical justification pattern
(`api-put-reimbursement/design.md:74`: "Direct precedent:
`send_human_review`... `review_reimbursement.py` follows the identical
shape"). Each addition individually looks like reuse of an established
pattern; the aggregate is a `shared` directory where zero of four modules
have a second consumer.

### Options

1. **Move use cases into their owning service.** `publish_pending` /
   `send_human_review` relocate into `publisher`; `list_reimbursements` /
   `review_reimbursement` are authored directly inside `api` from the start.
   `shared.reimbursement.repository` (the SQL) stays shared, since it is the
   piece both services genuinely touch. Matches `CONVENTIONS.md:17` exactly;
   costs an import-path change per relocated module plus superseding AD-025's
   placement for the two already-shipped use cases.
2. **Accept `shared.reimbursement.use_cases` as a domain-orchestration layer
   by convention, not a strict two-consumer test.** Redefine the rule for
   this specific slice: anything one call above the shared repository lives
   beside it regardless of consumer count, on the theory that a second
   consumer (the Agent, a future admin tool) is more likely here than
   elsewhere. Cheapest — no code moves — but formally waters down
   `CONVENTIONS.md:17` for one directory rather than resolving the tension.
3. **Accept and keep as-is, revisit once `agent-consume-reimbursement` is
   specified.** If the Agent ends up calling `publish_pending` or
   `send_human_review`, two of the four modules earn their placement
   retroactively and only `list_reimbursements`/`review_reimbursement` need
   moving. Defers the decision to when it's cheaper to make with full
   information — but risks the same pattern repeating a third time before
   anyone revisits it.

### Trigger to decide

Before `api-get-reimbursement` or `api-put-reimbursement` is implemented —
once either ships, `list_reimbursements`/`review_reimbursement` are
already-shipped code in the wrong place, the same way AD-029 had to correct
`managed_pool`'s placement after the fact.

---

## R-011 — Decision-stage error handling is undecided: retrying a billed LLM call is not free like retrying a DB query

**Raised:** 2026-08-08
**Status:** Open, needs evaluation — owner: Flavio
**Affects:** `agent-decide-reimbursement`
**Severity:** Medium — no correctness break, but an unbounded retry policy on
a step that calls a billed LLM API multiplies real cost per failure, unlike
`agent-consume-reimbursement`'s resolve-stage retries (Postgres queries),
which are free to repeat

### What breaks

`agent-consume-reimbursement` (shipped) retries a resolution-stage failure
up to 3 times via republish before escalating to `human-review`, at zero
marginal cost — the thing being retried is a `SELECT`/`UPDATE`. The new
decision stage's probabilistic layer invokes a paid LLM for date/value
extraction, and for the auto-approve consistency judge — every retry of a
*that* stage re-invokes the LLM. Blindly reusing the existing
retry-then-escalate machinery for decision-stage failures means a single
provider hiccup (timeout, rate limit, malformed structured output) costs up
to 3 additional billed calls per item before landing in `human-review`
anyway — and, per **R-007**'s already-recorded "correlated failure" pattern,
a systemic LLM outage or rate-limit event would multiply that cost across
every in-flight item at once, simultaneously. R-007 doesn't have a $ cost
dimension; this does.

### Options

1. **Reuse the existing retry-then-escalate machinery uniformly.** Simplest
   mental model, one error contract for the whole Agent — but retries a
   billed LLM call up to 3× per failure with no cost ceiling.
2. **Split by cause.** Infra/transient errors (DB, Kafka) retry via the
   existing path; LLM-specific failures (timeout, bad structured output,
   provider error) escalate straight to `human-review` with no retry —
   bounds LLM spend to at most one call per item per decision step, at the
   cost of a cause-aware dispatch and, per R-007, still landing in the same
   undifferentiated review queue.
3. **Cap LLM-specific retries independently of the general ceiling** (e.g.
   at most 1 retry for an LLM failure vs. 3 for infra) — a middle ground
   between 1 and 2.

### Not resolved by `agent-decide-reimbursement`

User flagged this explicitly during Specify (2026-08-08) as needing its own
cost/tradeoff evaluation before deciding — the spec proceeds without
committing to a specific decision-stage error-handling mechanism, asserting
only the project-wide invariant that no reimbursement is silently lost.
Design must not silently default to Option 1 without a follow-up
confirmation with the user.

---

## R-012 — All Pydantic models live in one flat `src/shared/src/shared/models.py`

**Raised:** 2026-08-09
**Status:** Open, needs evaluation — owner: Flavio
**Affects:** `shared` (`models.py`), `api`, `agent`, `publisher` — every
service that imports it
**Severity:** Low today, compounds with every model this file accumulates

### What breaks

Every Pydantic model in the project — regardless of which layer owns the
concept it represents — is declared in one 115-line file:

| Model | Concept | Real owner(s) |
| ----- | ------- | -------------- |
| `HealthStatus` | health-check response | `api` only |
| `SampleMessage` | test/sample Kafka payload | test scaffolding only |
| `ReimbursementRequest` | inbound API request body | `api`, `publisher` |
| `AttemptError` | retry/failure bookkeeping | `publisher`, `agent` |
| `RequestEnvelope` | `Request` topic envelope | `api`, `publisher` |
| `ReimbursementEnvelope` | envelope wrapper | `publisher`, `agent` |
| `Reimbursement` | domain row / `Reimbursement` topic payload | `publisher`, `agent`, `shared.reimbursement.repository` |

`api`, `agent`, and `publisher` all import from this single module
(confirmed via `from shared.models import` / `from shared import models`
across all three service packages plus `shared` itself). Unrelated concerns
— an HTTP-only health check, Kafka envelopes, the core domain row, and a
test-only sample message — sit in the same namespace with no boundary
between them. This is the same shape `CONVENTIONS.md:17` and **R-010**
already flag for `shared.reimbursement.use_cases`: `shared` accreting code
whose actual justification is "it's imported from more than one place,"
not "this concept is genuinely cross-cutting."

### Why it matters

1. **Confusion.** A reader has no signal from the import path which layer a
   model belongs to conceptually — `HealthStatus` (API-only) sits beside
   `Reimbursement` (the core domain entity) with identical provenance.
2. **Coupling.** Every service that needs any one model depends on the
   entire file, so an unrelated model gaining a field, a validator, or a new
   import (e.g. a future model needing a heavier dependency) risks
   invalidating the shared build cache and review scope for services that
   never touch that model.
3. **Hard to maintain.** As the Agent feature (`agent-decide-reimbursement`)
   and future features add models, this file has no organizing principle to
   push back against continued flattening — the path of least resistance is
   to keep appending to the one file that already has everything.

### Options

1. **Split by domain/module**, mirroring the `shared.reimbursement.*`
   pattern already used for use cases and the repository (e.g.
   `shared.reimbursement.models`, `shared.envelopes`, `shared.health`).
   Matches `CONVENTIONS.md:17`'s "more than one real consumer" test applied
   per-model rather than per-file; costs an import-path change everywhere
   `shared.models` is currently referenced.
2. **Accept and keep as-is.** At 115 lines and 7 models the file is still
   small enough to scan in full; the cost is organizational, not a runtime
   or correctness risk. Revisit once the Agent feature's decision-stage
   models land and the file grows further.

### Trigger to decide

Before `agent-decide-reimbursement` adds its decision/staleness models —
landing them in the same flat file compounds this a third time, the same
pattern **R-010** already names for `use_cases/`.
