# Project State

## Decisions

Architectural decisions that constrain future work. Append-only — supersede,
never delete.

### AD-001 — Migrations live in the API package, applied by a one-shot job

**Date:** 2026-08-07
**Status:** Active

Migration SQL lives in `src/api/migrations/`. It is applied by a dedicated
one-shot `migrate` compose service that reuses the api image; `api`, `agent`,
and `publisher` all gate on `service_completed_successfully`.

**Why:** The API layer owns the schema by direction. Running DDL inside the
FastAPI lifespan would race across replicas and block readiness, so execution
is separated from the service that owns the files. The one-shot job maps
directly to a k3s `Job` or initContainer.

### AD-002 — yoyo-migrations, not Alembic

**Date:** 2026-08-07
**Status:** Active

**Why:** Plain `.sql` files match the repo's raw-SQL / no-ORM convention;
yoyo provides advisory locking and rollback. Alembic would pull SQLAlchemy
into a codebase that deliberately has no ORM, and its autogenerate is useless
without models — every revision would be hand-written `op.execute()` anyway.
Cost accepted: yoyo uses psycopg, so the api image carries a second Postgres
driver alongside asyncpg. Acceptable — migrations are a batch job, not the
request path.

### AD-003 — Status is `TEXT` + `CHECK`, kebab-case, with a `pending` value

**Date:** 2026-08-07
**Status:** Active

**Why:** `CHECK` over native `ENUM` because adding a value later is a one-line
migration while `ALTER TYPE ... ADD VALUE` is effectively irreversible.
Kebab-case matches the GET filter values at `SCOPE.md:156` verbatim, removing
any mapping layer. `pending` was added because the publisher inserts a row
carrying only UUID + payload, and all five statuses in `SCOPE.md:72-76`
describe outcomes — a nullable status would defeat the constraint.

### AD-004 — `Published` and `Decision`/`Layer` columns dropped

**Date:** 2026-08-07
**Status:** Active

**Why:** `Published` is unnecessary — the agent's staleness rule
(`SCOPE.md:229`) compares a published date carried on the Kafka message
against the row's `updated_at`, so no column participates.
`Decision`/`Decision Layer`/`Layer` is dropped because status already encodes
automated-vs-human, and structurally the column would be constant: the
probabilistic layer never decides, it only extracts (`SCOPE.md:248-249`,
`:259`), so every auto-decided row would read `deterministic`.

### AD-005 — Two gap-analysis corrections adopted into the schema

**Date:** 2026-08-07
**Status:** Active

`receipts_date` (gap #8) and `decision_reason` (gap #7) are added even though
`SCOPE.md`'s field list omits them. A proposed `llm_assisted` column was
rejected.

**Why:** Both adopted columns are required for `SCOPE.md:15`'s audit trail —
without `receipts_date` a rejection cannot be reconstructed, and without
`decision_reason` an auto-decision records no justification, which the PDF
requires for all three outcomes. `llm_assisted` was declined as unnecessary;
LLM involvement remains recoverable from `decision_reason` text and LangFuse
traces.

**Implication:** `docs/SCOPE.md` is not frozen — this spec may correct it where
the document contradicts the PDF's requirements. Corrections are recorded in
the spec's Assumptions table with the gap number they close.

### AD-006 — pytest against a dedicated `reimbursementanalyzer_test` database

**Date:** 2026-08-07
**Status:** superseded by AD-008

Tests run against a dedicated `reimbursementanalyzer_test` database on the existing
compose `postgres` service, created by a `docker-entrypoint-initdb.d` script.
A session fixture drops and recreates the `public` schema, then applies
migrations from zero.

**Why:** An unenforced `CHECK` reads as a guarantee, so every constraint must
be proven to reject bad rows. A dedicated database keeps test DDL entirely off
the dev database. Trade-off accepted over testcontainers: tests require the
`postgres` service running, in exchange for no Docker-in-test dependency.

**Implication:** This establishes the repo's first test infrastructure
(`TESTING.md:3` records that none existed).

### AD-007 — Migration SQL ships inside the installed package

**Date:** 2026-08-07
**Status:** Active
**Refines:** AD-001 (path only; ownership and execution model unchanged)

Migration files live at `src/api/src/api/migrations/`, not
`src/api/migrations/`, and are resolved at runtime via `importlib.resources`.

**Why:** the `prod` target of `src/api/Dockerfile` copies only `/app/.venv` —
never source — so migrations placed outside the installed package would be
absent from the production image and from any k3s Job built on it. uv's build
backend includes non-Python files under the module root by default, so `.sql`
files inside the package ship in the wheel. One path then works in dev, prod,
and k3s with no Dockerfile change.

**Implication:** any future packaged resource (SQL, templates, static config)
belongs under the module root for the same reason.

### AD-008 — Tests provision their own PostgreSQL via testcontainers

**Date:** 2026-08-07
**Status:** Active
**Supersedes:** AD-006

The suite starts a throwaway `postgres:18` container per session
(`testcontainers[postgres]`), migrates into it, and tears it down.
`TEST_DATABASE_URL` overrides this for environments that supply their own
server, and that path keeps the `_test` name guard.

**Why:** AD-006 pointed the suite at the compose `postgres` service, which
coupled every test run to a running stack and to whatever state that database
had accumulated. A container is hermetic by construction: no stack, nothing
shared between runs, and no reachable path to a real database. Verified by
running the full suite with the compose `postgres` service stopped.

**Implication:** a Docker daemon is now a test prerequisite. The image tag must
track `docker-compose.yml` — `uuidv7()` and `UNIQUE NULLS NOT DISTINCT` are
version-sensitive, so a mismatched major would prove nothing. A runtime
assertion on `server_version_num` guards against drift.

**Note:** import from `testcontainers.community.postgres`;
`testcontainers.postgres` is deprecated as of 4.15.0.

### AD-009 — `src/api` is organised as vertical slices, one directory per operation

**Date:** 2026-08-07
**Status:** Amended by AD-018 (path) and AD-019 (root-exception list) — 2026-08-08

**Amendment note:** the paths below (`src/api/src/api/...`) and the "three
things stay at the api root" list are as of the original decision. AD-018
flattens the path one level (`src/api/src/...`, no `api/` package
directory); AD-019 moves `config.py` and `errors.py`'s/`kafka.py`'s
framework-agnostic halves into `shared`, leaving only thin FastAPI-specific
wiring at the api root. The slicing rationale itself (operation-level,
`create`/`list`/`update`) is unchanged.

`src/api/src/api/reimbursement/create/` holds everything the `create`
operation needs (`route.py`, `validation.py`, `producer.py`) as a self-
contained unit. Three things stay at the `api` root by exception: `errors.py`
(the app-wide `{"msg"}` contract), `kafka.py` (the `AIOProducer`'s lifespan-
owned lifecycle), `config.py` (only constants something outside a slice
compares against).

**Why:** a flat module list forces every reader to hold the whole service in
their head to find one endpoint, and gets worse with every route SCOPE.md
still requires (`GET`, `PUT`). A slice is a unit you can open, understand, and
delete whole. Sliced at the *operation* level (`create`, later `list`,
`update`), not the resource level, because the operations barely overlap —
`create` is a Kafka write path with a 25 MiB body, while `list`/`update` are
small DB reads and writes.

**Implication:** tests mirror the slices 1:1
(`src/api/tests/reimbursement/create/`), which requires `pythonpath` and
`--import-mode=importlib` in the root `pyproject.toml` so sibling slices can
share filenames like `test_route.py` — verified by experiment on pytest
9.1.1. `list`/`update` inherit this shape and the app-wide error contract
rather than re-implementing either.

### AD-010 — `confluent_kafka.aio.AIOProducer` is the project's Kafka producer pattern

**Date:** 2026-08-07
**Status:** Amended by AD-019 (construction site) — 2026-08-08

**Amendment note:** `AIOProducer` construction (`producer_config()` +
`AIOProducer(...)`/`.close()`) moved to `shared.kafka.managed_producer` — a
framework-agnostic async context manager, so a future producer (the
publisher) doesn't have to duplicate this lifecycle. `api/kafka.py` keeps
only the FastAPI-specific half: the lifespan wiring and `get_producer`
dependency. The pattern itself (`batch_size=1`, `acks=all`,
`enable.idempotence=true`) is unchanged.

One `AIOProducer` per app, constructed in the FastAPI lifespan with
`batch_size=1`, `acks=all`, `enable.idempotence=true`. Routes obtain it via a
FastAPI dependency (`get_producer`), never construct one themselves.

**Why:** `AIOProducer.produce()` returns a per-message `asyncio.Future` that
resolves or raises on that message's own delivery report — exactly what a
per-request delivery-gated response needs, replacing a hand-rolled poller-
thread design entirely. `batch_size=1` is load-bearing, not cosmetic: the
1000-message/1.0s defaults (verified against
`confluent_kafka/aio/producer/_AIOProducer.py`) would add ~1s latency per
request and buffer up to 1000 × 25 MB before flushing.

**Implication:** `headers` are unusable in this producer (`NotImplementedError`
in batch mode) — any future publisher needing message headers must use the
synchronous `Producer.produce()` instead.

### AD-011 — App-wide `{"msg": "..."}` error contract

**Date:** 2026-08-07
**Status:** Active

Every non-2xx response across the API is `{"msg": "..."}`, including
FastAPI's default `422` + `detail[]` for `RequestValidationError`, which is
overridden to `400` + `{"msg"}`. Registered once in `api/errors.py` against
the `FastAPI` app, not per-route.

**Why:** `SCOPE.md:140-144` defines one response body for the documented
codes; a per-route implementation would let future routes (`GET`, `PUT`)
silently diverge from it. Error messages are built from `loc`/`msg` only,
never `input` — `ValidationError` carries the raw offending value in `input`,
and echoing it would leak PII into responses and logs.

**Implication:** any future route inherits this contract automatically by
being registered on the same `FastAPI` app; no route re-implements error
shaping.

### AD-012 — Kafka message ceiling: 26 MiB (27 262 976 bytes)

**Date:** 2026-08-07
**Status:** Amended by AD-020 (ceiling value) — 2026-08-08

**Amendment note:** the ceiling below reflects the value as originally
decided. AD-020 reduces it to 1 MiB body / 2 MiB Kafka message. The
mechanism (broker + replica-fetch + producer all raised together, 1 MiB of
headroom over the body ceiling) is unchanged — only the numbers are.
`api.config` no longer exists; both constants now live in `shared.config`
(AD-019).

`api.config.KAFKA_MAX_MESSAGE_BYTES = 27_262_976`, applied at the broker
(`KAFKA_MESSAGE_MAX_BYTES`, `KAFKA_REPLICA_FETCH_MAX_BYTES` in
`docker-compose.yml`) and the producer (`message.max.bytes`). 1 MiB of
headroom above `MAX_BODY_BYTES` (25 MiB, the documented HTTP ceiling) for the
envelope prefix and protocol framing.

**Why:** librdkafka's `message.max.bytes` defaults to 1 000 000 — far below
what `SCOPE.md:344` requires the broker to carry. `KAFKA_SOCKET_REQUEST_MAX_BYTES`
needed no change; its 100 MB default already clears this.

**Implication:** the publisher's own consumer will need `fetch.max.bytes` /
`max.partition.fetch.bytes` raised to read messages at this size — writing a
25 MiB message and being unable to read it back is a real trap for that
feature, recorded here so it isn't discovered in production.

### AD-013 — Batches are bounded by item count (500), and the publisher processes 10 items concurrently

**Date:** 2026-08-07
**Status:** Active

`POST /api/v1/reimbursement` rejects a batch of more than **500 items** with
`400`. This is a second, independent limit alongside the 25 MiB byte ceiling
of AD-012 — a payload may be up to 25 MiB *and* must be at most 500 items.
The publisher processes items from one message with **at most 10 in flight**.

**Why:** a byte ceiling does not bound item count. 25 MiB of minimally-sized
items is roughly 290 000 of them, and each item costs an `INSERT` plus an
`acks=all` Kafka round-trip awaited serially (~15 ms). Processed
sequentially that is hours; even at high concurrency the worst case stays
unbounded. Exceeding `max.poll.interval.ms` (300 s) does not merely run slow
— the broker revokes the consumer's partitions, the offset commit fails, and
the group rebalances. It recovers (redelivered items that already committed
are absorbed by the duplicate path) but only by thrashing, stalling every
other partition in the group while presenting as unexplained slowness.

The two numbers are chosen together: `500 ÷ 10 × ~15 ms ≈ 0.75 s` per
message against a 300 s interval — roughly 400× headroom, deliberately far
larger than any plausible estimation error.

Bounding at ingress rather than splitting internally keeps one HTTP request
equal to one Kafka message, which preserves the all-or-nothing publish
semantics of `api-post-reimbursement` and lets `build_envelope` keep
byte-splicing the raw body. An API-side chunking alternative was considered
and rejected for breaking both.

**Implication:** concurrency is bounded by the DB connection pool, not by
CPU — each in-flight item holds a transaction open across a Kafka round-trip
(AD-014's ordering), so concurrency N pins N connections per replica. The
pool must be sized at or above the concurrency limit. Both values are
starting estimates with no load test behind them; recorded as **R-005** in
`.specs/RISKS.md` for revisit with production data. Changing the cap is an
API contract change, not a publisher-side tuning knob.

### AD-014 — Failure history travels on the message envelope

**Date:** 2026-08-07
**Status:** Active

`RequestEnvelope` carries `errors: list[AttemptError]`, defaulting to empty.
Each entry records the attempt number, an RFC 3339 UTC timestamp, the stage
that failed (`db-insert` or `publish`), the error type, and the message. Every
retry appends one entry and never overwrites or truncates prior ones. The
`Reimbursement` envelope carries the same field for the same reason.

**Why:** retries are implemented by republishing the failed item to the topic
with `retry` incremented. The consumer that eventually sees `retry = 4` is a
different poll, potentially in a different process on a different replica —
it has no access to what failed on attempts 1-3 unless that history travels
with the message. Without this field, the `retry > 3` fallback can only
record *that* an item failed repeatedly, never *why*, which makes the
resulting `human-review` row unactionable and defeats the purpose of
preserving it (`SCOPE.md:216-217`).

Full history rather than last-error-only: a reviewer's first question is
whether the same error occurred four times (permanently bad data — fix it) or
four different errors did (flaky infrastructure — just replay it). The list is
naturally bounded at four entries because `retry > 3` ends the loop.

**Implication:** the publisher renders the full history into the
`reimbursement.decision_reason` of any `human-review` row it creates via the
retry-ceiling path. The Agent inherits both the field and the same obligation
— it has an identical `retry > 3` → human-review requirement
(`SCOPE.md:271-273`) and would otherwise rediscover this exact gap.
Error text may embed values supplied by the driver (Postgres quotes offending
column values in its `DETAIL` line), so full detail is confined to the
envelope, the row, and the failure-log file — all already inside the payload's
trust boundary — while stdout logs carry only the error type and constraint
name.

### AD-015 — `Reimbursement` messages carry the row `uuid` only

**Date:** 2026-08-07
**Status:** Active

The message the publisher produces to the `Reimbursement` topic is
`{uuid, retry, published_at, errors}`. It does **not** carry the request
payload; the Agent resolves that from `reimbursement.original_payload` by
`uuid`.

**Why:** the row is committed before the message is published, so the payload
is already durably stored and addressable. Re-propagating it would duplicate
up to 25 MiB per item on the topic for no gain, and would make the
`Reimbursement` topic a second copy of data the database already owns.

**Implication:** byte-exact fidelity of the original request now survives in
exactly one place — the retained `Request` topic log. `original_payload` is
`JSONB`, which normalizes key order, insignificant whitespace, numeric
literals (`93.50` → `93.5`) and unicode escapes on write, so what the Agent
reads back is semantically but not byte-identical to what the client sent.
`Request` topic retention is therefore an audit-retention decision rather than
an operational one, and no retention policy has been set — recorded as
**R-003**. The Agent must also tolerate a `uuid` with no matching row, which
the dual-write window of **R-001** makes possible.

### AD-018 — Flat `api` layout: `src/api/src/*.py`, `api` is a virtual (uninstalled) workspace member

**Date:** 2026-08-08
**Status:** Active

`src/api/src/api/` (the doubled path AD-009 originally described) is
flattened to `src/api/src/*.py` — no `api/` package directory. `src/api/pyproject.toml`
sets `[tool.uv] package = false` and drops `[build-system]` entirely: `api`'s
dependencies still resolve into the shared workspace venv, but `api` itself
is never built or installed. Every import that used to be `from api.X
import ...` is now a bare `from X import ...`; `reimbursement/create/`
keeps its own `__init__.py` and dotted-import form since it's a real
sub-package. Running the app now requires `src/api/src` on `PYTHONPATH`
(root `pyproject.toml`'s `pythonpath` for pytest; `ENV PYTHONPATH` in
`src/api/Dockerfile` for every runtime stage), and the ASGI entrypoint is
bare `main:app`, not `api.main:app`.

**Why:** user decision, overriding the src-layout default (`module-root =
"src"`) uv_build otherwise applies uniformly across all four workspace
members. Two structurally narrower alternatives were raised and rejected
first: `module-root = ""` (would only collapse the doubled `src`, keeping an
`api/` package folder) and a fully flat layout kept under a real uv_build
package (verified via Context7 against uv_build's own source —
`find_roots` requires a package directory bearing the module name under
`module-root`; loose `.py` files directly there are silently excluded from
the wheel). `package = false` is the only mechanism that satisfies the
literal flat-path request while still resolving `api`'s dependencies through
the normal workspace mechanism.

**Implication:** `api` is no longer independently importable/installable —
nothing outside its own Docker container or the test suite's `pythonpath`
entry can `import` it, which is fine today since no other workspace member
declares `api` as a dependency (`shared`, `agent`, `publisher` never did).
The Docker "dev vs. prod" editable/non-editable install distinction
collapses for `api`: since nothing is ever installed, every stage now
copies and runs from source identically (verified: `docker build --target
dev` + a live `/health` request against the running container). A real,
accepted risk this creates: `config.py`, `errors.py`, `kafka.py`, `main.py`
are now bare top-level module names with no package-name isolation: if a
future workspace member ever needed to sit on the *same* Python process as
`api` (none does today — each of the four services runs in its own
container) and happened to define a same-named module, they would collide
on `sys.path`. Flagged explicitly to the user before implementing; the user
chose to proceed anyway. `migrate.py`'s `migrations_path()` switched from
`importlib.resources.files("api")` (required a real package) to a plain
`Path(__file__).parent` — the only other call site this change touched.

### AD-019 — Shared kernel: `config.py`, `errors.py` (exception classes), `kafka.py` (producer factory) move from `api`-local to `shared`

**Date:** 2026-08-08
**Status:** Amended by AD-022 (config shape, publish() location, file names) — 2026-08-08

**Amendment note:** AD-022 replaces this ADR's `producer_config(...)` free
function with a `KafkaConfig` frozen dataclass, moves the actual publish
mechanics (`asyncio.wait_for` + `PublishFailed` wrapping) from
`reimbursement/create/producer.py` into `shared.kafka.publish()`, and
renames `api/kafka.py` to `api/producer.py`. Everything below describes the
shape as of this ADR's original decision; AD-022 is the current shape.

`shared.config` now holds every configuration value previously in
`api/config.py` (`MAX_BODY_BYTES`, `KAFKA_MAX_MESSAGE_BYTES`,
`MESSAGE_TIMEOUT_MS`, `bootstrap_servers()`) plus `REQUEST_TOPIC` (moved out
of `reimbursement/create/producer.py`, since the publisher reads the same
topic) and `MAX_BATCH_ITEMS` (moved out of `reimbursement/create/validation.py`
— AD-013 already treats this as a wire constant the publisher's own timing
math depends on) and a new `kafka_security_config()` for optional SASL/TLS.
`shared.errors` holds the three exception classes (`PayloadTooLarge`,
`BatchInvalid`, `PublishFailed`) — framework-agnostic, reusable by any
future layer. `shared.kafka` holds a parameterised `producer_config(...)`
and a `managed_producer(...)` async context manager (construct + guaranteed
`.close()`).

**What stayed api-local, deliberately:** `api/errors.py` keeps the FastAPI
`register_handlers`/`_msg_response`/handler functions — HTTP-response
shaping is API-specific by definition (only `api` serves HTTP), and
importing them into `shared` would give `shared` a `fastapi` dependency no
other workspace member needs. `api/kafka.py` keeps only the FastAPI
lifespan/`get_producer` dependency wiring, calling into
`shared.kafka.managed_producer`. `responses.py`'s `MessageResponse` was
folded into `api/errors.py` (its only real consumer) rather than moved to
`shared` — same reasoning: it's the API's HTTP response shape, not a
cross-service contract.

**Why:** user decision, made across several PR review-comment replies on
PR #4 (config → shared for reuse; errors "probably going to be reused by
other project layers"; kafka.py "probably gonna be reused in the future...
receive the parameters to run the publishing itself"). Consistent with the
already-drafted `publisher-consume-request` design (`design.md`
Prerequisites P4), which independently arrived at "config centralised into
shared" as a blocking prerequisite for that feature.

**Implication — flagged for whoever picks up `publisher-consume-request`
next:** this `shared.config`/`shared.kafka` shape is **function-based and
simpler** than what `design.md`'s draft (Status: Draft, unapproved) proposes
for its own AD-016/AD-017 — frozen pydantic `Settings` models with
`from_env()` classmethods, split `KafkaSettings`/`PublisherSettings`, a
`_pool_covers_concurrency` startup validator. This session's shared/config.py
does **not** implement that shape. `design.md`'s "Prerequisites" table (P3,
P4) and "P4 blast radius" note are now stale — they describe `api.config`
importers and an unmigrated `api/config.py`, both of which no longer exist
(P3 and P4 are functionally done, just not in the shape drafted). Revisit
`design.md`'s Configuration/Prerequisites/File Structure sections against
the actual current state before that design is approved or Tasks starts.
AD-016 and AD-017 remain reserved for that design, not claimed here.

### AD-020 — Body/Kafka ceiling reduced: 1 MiB body / 2 MiB Kafka message (was 25 MiB / 26 MiB, AD-012)

**Date:** 2026-08-08
**Status:** Active

`MAX_BODY_BYTES = 1_048_576` (1 MiB), `KAFKA_MAX_MESSAGE_BYTES = 2_097_152`
(2 MiB, same "1 MiB headroom over the body" rule AD-012 established).
`docs/SCOPE.md:124,344` and `.specs/features/api-post-reimbursement/spec.md`
amended in place to record this as an intentional requirement change, not a
silent drift from the original "25mb" scope line.

**Why:** user decision. Sized against `docs/original/sample.json` (3 sample
items, ~381 bytes average as compact JSON) and the existing 500-item batch
cap (AD-013): `1_048_576 ÷ 500 ≈ 2,097` bytes/item of headroom, ~5.5x the
sample's average item size — the user's own stated target ("1 MiB could
support each item in the sample 5x bigger than the sample"). Note for the
record: the user's rationale referenced "around 2.5k items," but the 5x
headroom figure only reconciles against the 500-item cap already in place
(2,500 items at 1 MiB gives ~1.1x headroom, not 5x) — flagged to the user at
the time; proceeded with 1 MiB + the existing 500-item cap since both
numbers are otherwise self-consistent.

**Implication:** `docker-compose.yml`'s `KAFKA_MESSAGE_MAX_BYTES`/
`KAFKA_REPLICA_FETCH_MAX_BYTES` updated to `2097152` to match. `api/kafka.py`
now explicitly sizes `queue.buffering.max.kbytes` (`200 *
(KAFKA_MAX_MESSAGE_BYTES // 1024)` ≈ 200 MiB — good for ~200 concurrent
max-size in-flight messages) rather than relying on librdkafka's 1 GiB
default, closing a gap flagged in code review (Perf-4). `max_workers` on the
producer's thread pool was deliberately left at the `AIOProducer` default
(4): the original concern was sized against ~26 MiB payloads tying up all 4
workers per call; at 1 MiB that per-call cost is ~26x smaller. Any future
feature (the publisher included) that reads `shared.config.MAX_BODY_BYTES`/
`KAFKA_MAX_MESSAGE_BYTES` picks up the new values automatically — no
importer needed its own update.

### AD-021 — `PublishFailed` stays a broad `except Exception`, with the exception's class name folded into the message

**Date:** 2026-08-08
**Status:** Active

`reimbursement/create/producer.py`'s `publish()` catches `Exception`
broadly (not narrowed to `KafkaException`/`BufferError`/`TimeoutError`) and
raises `PublishFailed(f"{type(exc).__name__}: {exc}")` — the failing
exception's own type name is now part of the message, both in the raised
`PublishFailed` and the `logger.error` call already there.

**Why:** user decision, overriding a code-review recommendation to narrow
the `except` clause so unrelated bugs (e.g. a future `AttributeError`) would
propagate to the catch-all handler instead of being reported as a delivery
failure. User's stated reasoning: a broader catch is fine as long as the
resulting error is identifiable — narrowing the exception surface was
judged lower-value than keeping every producer-side failure mode uniformly
converted to a 500, with the type name doing the work of distinguishing a
genuine broker/timeout failure from a code bug after the fact (in logs and
in the response body).

**Implication:** a future `AttributeError` inside `publish()` now surfaces
as `500 {"msg": "failed to publish request"}` with
`"AttributeError: ..."` in the server log, not as an unhandled exception
reaching the catch-all `_unhandled_exception_handler`. Debugging a
programming bug in this path relies on grepping logs for the type name
rather than a distinct status code or handler path.

### AD-022 — `KafkaConfig` frozen dataclass replaces free-function config; `publish()` becomes a generic `shared.kafka` helper; `api/kafka.py` renamed `api/producer.py`

**Date:** 2026-08-08
**Status:** Amended by AD-023 (loader shape, module name) — 2026-08-08

**Amendment note:** AD-023 replaces this ADR's `KafkaConfig.from_env()`
classmethod with a single module-level `load_config()` function (the user's
own established pattern from another project), and renames `shared.kafka`
to `shared.producer`. Everything below describes the shape as of this
ADR's original decision; AD-023 is the current shape.

Second round of PR #4 review (14 new comments, all from the reviewer, none
replies to prior threads) drove three related changes:

1. **`shared.config.KafkaConfig`** (frozen dataclass) replaces
   `bootstrap_servers()`, `kafka_security_config()`, and
   `shared.kafka.producer_config()`. Fields split into two groups: the
   dataclass's *defaults* (`message_max_bytes`, `message_timeout_ms`,
   `publish_timeout_seconds`, `queue_buffering_max_kbytes`) are fixed
   application constants, not independently env-configurable — changing
   them would violate documented invariants (AD-012/AD-020's byte-ceiling
   math, AD-021's timeout ordering) tied to the API contract, not to
   deployment. Only `bootstrap_servers` and the five SASL/TLS fields come
   from `KafkaConfig.from_env()` reading `os.environ` (matching
   `docker-compose.yml`'s existing env passthrough and this project's
   dotenv convention) — those are the values that legitimately vary per
   deployment. `to_producer_config()` replaces the old `producer_config()`
   free function as a method on the dataclass itself, closing the "why are
   Kafka settings split across `config.py` and `kafka.py`" gap a reviewer
   comment raised directly.
2. **`shared.kafka.publish(producer, topic, payload, timeout_seconds)`**
   is now the actual Kafka-publish mechanism (`producer.produce()` +
   `asyncio.wait_for` + `PublishFailed` wrapping) — previously this logic
   lived only inside `reimbursement/create/producer.py`, unreusable by any
   future publisher/consumer layer. `reimbursement/create/producer.py`
   is now a thin wrapper: `build_envelope()` (still create-slice-specific —
   the envelope shape is this feature's own wire contract) plus a
   `publish()` that calls the shared helper and, only on `PublishFailed`,
   logs `request_ids` against it. **This answers a direct reviewer
   question** ("why do you need to publish the request_ids?"): they are
   never part of the published payload — `request_ids` exists solely to
   give a delivery-failure log line something to correlate against.
3. **`api/kafka.py` → `api/producer.py`** (technology-agnostic name, per
   reviewer request) now holds only `get_producer`/`get_kafka_config` FastAPI
   dependency accessors — the `lifespan_producer` async context manager it
   used to own moved into `main.py`'s own `lifespan()`, which now composes
   `shared.kafka.managed_producer` + `KafkaConfig.from_env()` directly. Per
   the reviewer's stated reason ("makes it easier to add new items to the
   lifespan"), any future resource (a DB pool, a second client) is added as
   another `async with` in that one function, not by growing a
   module-specific lifespan elsewhere. `api/payload.py` also moved into
   `reimbursement/create/payload.py` — it had exactly one consumer
   (`reimbursement/create/route.py`) and belonged in the vertical slice
   (AD-009), not at the package root.

**Why:** all three are direct reviewer requests on PR #4's second review
round, not independently initiated. The `KafkaConfig` fixed-vs-configurable
split (rather than making every field env-overridable, which the reviewer's
pasted example arguably implied) is this session's own judgment call,
surfaced to the reviewer as a reply rather than silently assumed — deferred
to the reviewer's clarification if not accepted as-is.

**Implication for `publisher-consume-request`:** `shared.kafka.publish()`
now IS the "receive the topic, the payload, and publish to Kafka" primitive
that feature's own producer/consumer work should reuse directly — this
closes part of what `design.md`'s still-unclaimed AD-017 was reaching for.
`KafkaConfig` remains a **dataclass**, not the frozen pydantic `Settings`
shape `design.md` proposes for its own AD-016 — that gap (noted in AD-019's
original text) is unchanged by this ADR; `design.md` still needs a pass
against current `main`/branch state before its Tasks phase starts.

### AD-023 — Single cached `load_config()` loader replaces `KafkaConfig.from_env()`; `shared.kafka` renamed `shared.producer`

**Date:** 2026-08-08
**Status:** Active (amends AD-022 — see its amendment note)

`shared.config` now follows the user's own established pattern from another
project (`chatbot/core/config/config.py`): every dataclass is a plain data
holder with no `from_env()`/env-reading logic of its own; a single
`@lru_cache(maxsize=1)` module-level `load_config() -> Config` function does
every `os.getenv()` read in one place and returns a root `Config` dataclass
(`Config.kafka: KafkaConfig` today; future shared-kernel config domains
nest alongside it as the root grows). `KafkaConfig.from_env()` is gone —
any layer that needs Kafka settings calls `load_config().kafka`. Reading is
memoised process-wide (once per process, not once per call), which is why
`api/producer.py`'s `get_kafka_config()` dropped its `Request` parameter
and its `app.state.kafka_config` stash entirely — it's now a stateless
`load_config().kafka`, no longer something `main.py`'s `lifespan()` needs
to construct and hand down. `shared.kafka` is renamed `shared.producer`
(user request) — no behavior change, `managed_producer`/`publish` are
unchanged.

**Why:** direct user request to match a pattern already proven in another
codebase, plus "any layer can use it" — a single retrieval function is
easier to depend on from a future service than a per-dataclass
classmethod convention that would need reinventing for every new config
domain (`RedisConfig`, `DbConfig`, ... whatever `publisher-consume-request`
or later features need).

**Testing implication:** `load_config()`'s module-level cache persists for
the life of the Python process, which is normally the whole pytest run —
without intervention, whichever test calls it first would freeze the
`os.environ` view every later test sees, silently defeating any
`monkeypatch.setenv(...)`. Both `src/shared/tests/conftest.py` (new) and
`src/api/tests/conftest.py` now carry an autouse `load_config.cache_clear()`
fixture so every test starts from a clean cache. Anyone adding a new test
tree under `src/*/tests` that touches `shared.config` needs the same
fixture — pytest's per-directory `conftest.py` scoping does not fan this
autouse fixture out to sibling trees automatically.

**Implication for `publisher-consume-request`:** the loader-function
pattern (not per-class `from_env()`) is now the shape to match if that
feature adds its own config needs — nest a new dataclass field onto
`Config` and read it inside `load_config()`, don't invent a parallel
`Settings.from_env()` convention. `design.md`'s AD-016 (frozen pydantic
`Settings` models) diverges from this further than AD-022 already noted;
revisit before that design is approved.

---

## Handoff

**Last updated:** 2026-08-08

**Done:** `db-schema-migrations` — merged to `main` via PR #3 (`9799607`).
`api-post-reimbursement` — all 11 tasks complete, standalone Verifier PASS
(`.specs/features/api-post-reimbursement/validation.md`), PR #4 opened
(draft) against `main` on `feature/4-create-reimbursement-endpoint`,
`/code-review` + `/tests-code-review` run (68 raw findings), all 45 review
threads on PR #4 now fixed and resolved — 15 the user replied to directly
(some fixes, some accepted-as-is with a posted rationale), 30 with no reply
fixed and resolved without waiting on one, per explicit instruction. Full
suite green (145 tests) after the remediation. Sitting at "wait for the
user's manual submit/merge of PR #4" — no further autonomous action pending
here.

**Structural changes made during PR #4 remediation, relevant to whoever
touches `api` or `shared` next:** AD-018 (flat `api` layout, `package =
false`), AD-019 (config/errors classes/kafka producer factory moved to
`shared` — amended by AD-022), AD-020 (body ceiling 25 MiB → 1 MiB), AD-021
(`PublishFailed` broad-except pattern), AD-022 (`KafkaConfig` dataclass,
`api/kafka.py` → `api/producer.py`, `payload.py` → `reimbursement/create/`
— amended by AD-023), AD-023 (single cached `load_config()` loader,
`shared.kafka` → `shared.producer`). Read these before assuming any file
path or import from the original `api-post-reimbursement` spec/design
docs is still accurate — several are now stale (see below).

**In flight:** `publisher-consume-request` — spec (42 requirements), context,
and `design.md` drafted. Awaiting design approval before Tasks. No code yet.

AD-013 / AD-014 / AD-015 were ratified in the prior session, and
`.specs/RISKS.md` was created (R-001 … R-005) to hold what is knowingly
deferred. `design.md` proposes **AD-016** (single centralised config in
`shared`, frozen `Settings` models + `from_env()`) and **AD-017** (`asyncpg`
pool + implicit-transaction pattern) — **still reserved, not appended**,
pending approval. AD-018 through AD-023 above deliberately did not claim
these numbers.

**`design.md` is now partially stale — read before approving or starting
Tasks:** its "Prerequisites" table listed four changes to already-merged
`api-post-reimbursement` code: `RequestEnvelope.errors` + `AttemptError`
model (**P1 — still not done**), the `build_envelope` prefix (**P2 — still
not done**), the 500-item cap (**P3 — done**, `shared.config.MAX_BATCH_ITEMS`),
and the config move to `shared` (**P4 — done**, `shared/config.py`). P3 and
P4 are functionally satisfied but **not in the shape `design.md` drafted** —
`shared/config.py` today is plain module-level constants + functions, not
the frozen pydantic `Settings`/`from_env()` model `design.md`'s
"Configuration" section specifies for AD-016. `design.md`'s "P4 blast
radius" note (9 files importing `api.config`) and its File Structure
section's `api/src/api/config.py` path are both stale — `api.config` no
longer exists in any form, and the `api/src/api/` path itself no longer
exists (AD-018). Revisit `design.md`'s Configuration, Prerequisites, and
File Structure sections against current `main`/this branch before Tasks.

**Repo-wide note:** `.specs/` was untracked by git until the prior session —
a `.gitignore` pattern bug (`!.spec`/`!.spec**`, which doesn't match
`.specs/` and can't un-ignore a directory via `**` anyway) silently kept
every spec local-only. Fixed to `!.specs`/`!.specs/**` and the whole
directory committed for the first time on
`feature/4-create-reimbursement-endpoint`.

**Branch:** `feature/4-create-reimbursement-endpoint`, based on `main` at
`9799607` (post PR #3 merge). Not yet merged — PR #4 is still in draft,
awaiting the user's own review/submit.
