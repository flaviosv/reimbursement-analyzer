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
**Status:** Accepted, deferred — owner: Flavio
**Affects:** `publisher-consume-request`, and the future Agent feature
**Severity:** Low likelihood, moderate blast radius

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
