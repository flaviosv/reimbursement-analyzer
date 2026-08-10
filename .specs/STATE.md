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

### AD-016 — Vacated (never adopted)

**Date:** 2026-08-08
**Status:** Vacated — superseded before adoption by AD-022 + AD-023

Reserved by `api-post-reimbursement`'s design for "a single centralised
config in `shared`, as frozen `Settings` models with `from_env()`
classmethods." Never appended, and the shape was never built.

**Why vacated:** the decision it reserved was genuinely taken, in a
different form: AD-022 introduced the `KafkaConfig` frozen dataclass and
AD-023 replaced per-class `from_env()` with one `@lru_cache`'d
`load_config()`. Recording the vacancy explicitly so the 016/017 gap in this
log reads as a deliberate release rather than a lost entry.

### AD-017 — `asyncpg` pool + implicit-transaction as the project's async DB pattern

**Date:** 2026-08-08
**Status:** Amended by AD-033 (publisher's insert+publish unit of work only — the pattern below remains Active for every other write-plus-side-effect unit of work) — 2026-08-09

Every service reaching Postgres at runtime does so through an `asyncpg` pool
opened by a `managed_pool()` async context manager, and wraps any
write-plus-side-effect unit of work in `async with conn.transaction():` —
placing the side effect *inside* the context manager so a failure rolls back
without an explicit rollback call.

**Why:** the publisher must not leave a committed `reimbursement` row whose
`Reimbursement` publish never happened (`SCOPE.md:219-220`). Putting the
publish inside `conn.transaction()` makes that guarantee structural rather
than a discipline the next author has to remember. `managed_pool` mirrors
`shared.producer.managed_producer`'s construct/yield/close shape, so the two
resources compose identically in a service's startup.

Pool sizing is set explicitly, never left at `create_pool`'s `min_size=10,
max_size=10` default — which happens to equal the publisher's item
concurrency and would leave zero headroom.

**Implication:** this is the project's first runtime database access
(`CONCERNS.md:21` records `asyncpg` as declared-but-unused). The Agent
inherits the pattern. Any service whose concurrency pins a connection per
in-flight unit must assert `pool_max_size >= concurrency` at startup —
otherwise it starves under load instead of failing loudly (**R-005**).

### AD-018 — Flat `api` layout: `src/api/src/*.py`, `api` is a virtual (uninstalled) workspace member

**Date:** 2026-08-08
**Status:** Amended by AD-031 (build backend: uv_build → setuptools) — 2026-08-09

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

### AD-024 — `get_kafka_config` DI wrapper removed; `api/producer.py` renamed `api/dependencies.py`

**Date:** 2026-08-08
**Status:** Active (follow-on correction to AD-023)

AD-023's `get_kafka_config()` had shrunk to a one-line pass-through
(`return load_config().kafka`) with no runtime resource behind it — unlike
`get_producer()` (which reads a live `AIOProducer` off `app.state`,
constructed once by `main.py`'s `lifespan()` and genuinely needing
FastAPI's request-scoped DI so tests can override it with a fake), Kafka
config is just a cached, deterministic value reachable from anywhere via
`load_config()`. Routing it through `Depends()` bought nothing — it can't
be usefully overridden for a test in a way that plain `load_config()`
already isn't (tests never needed a non-default `KafkaConfig` for
route-level behavior; the one test that does — the publish-timeout test in
`reimbursement/create/test_producer.py` — calls `publish()` directly, below
the route layer, where `dataclasses.replace()` already handles it).
`reimbursement/create/route.py` now calls `load_config().kafka` directly
inside `create_reimbursement()`; `get_kafka_config` is deleted.

With that gone, the file it lived in (`api/producer.py`) held only
`get_producer` — which genuinely needs a shared, feature-independent module
(not `main.py`, because `route.py`'s `Depends(get_producer)` would then
import from `main.py`, which itself imports `route.py` for the router — a
real import cycle; not `reimbursement/create/route.py` either, because
`test_main.py` exercises `get_producer`/`lifespan` standalone, independent
of any specific feature slice). Since its only remaining content **is** a
route dependency, the file is renamed `api/dependencies.py` — a name that
describes what's actually in it, rather than what technology it used to
also configure.

**Why:** direct question from the user ("why do you need the file
src/producer.py?"), which surfaced that the file's sole remaining
justification (`get_producer`) doesn't match its old name once
`get_kafka_config` — the thing that made "producer" sound right — was
gone. Renaming and dropping the dead wrapper together avoids leaving a
misleadingly-named file with only accidental content in it.

### AD-025 — `shared` owns cross-service persistence, sliced by domain

**Date:** 2026-08-08
**Status:** Active

`shared` gains `asyncpg` and a domain-sliced persistence layer:
`shared/reimbursement/repository.py` (pool lifecycle + statements) and
`shared/reimbursement/use_cases/send_human_review.py`, plus a cross-domain
`shared/failure_log.py`. `shared/review/` follows when the `human_review`
table gets a consumer. Cross-domain infrastructure (`config`, `errors`,
`models`, `producer`, `failure_log`) stays at the `shared` root.

**Why:** AD-018's `package = false` makes flattened services uninstallable,
so `agent` cannot import from `publisher` — anything both need must live in
`shared` or be copy-pasted later. `SCOPE.md` names both consumers explicitly:
the last-resort log is mandated twice (`:216-217`, `:272-273`), as is the
`retry > 3` escalation.

**Tension, accepted knowingly:** `CONVENTIONS.md:17` scopes `shared` to code
with *more than one real consumer*, and the Agent is still a stub. The
precedent for placing infrastructure there ahead of its second consumer is
`shared/producer.py`, which today has exactly one (`api`). Slicing by domain
rather than by flat module keeps the future `review` slice from landing
beside reimbursement statements in one file.

**Implication:** if the Agent never materialises as a second caller,
`use_cases/` should collapse back into the slice's `repository.py` — a
one-member abstraction layer is ceremony, and this is a tracked bet.

### AD-026 — `publisher` flattens to `src/publisher/src`, a virtual workspace member

**Date:** 2026-08-08
**Status:** Amended by AD-031 (build backend: uv_build → setuptools) — 2026-08-09
**Follows:** AD-018 (same mechanism, applied to a second service)

`src/publisher/src/publisher/` collapses to loose modules under
`src/publisher/src/`. `[tool.uv] package = false`, `[build-system]` dropped,
`ENV PYTHONPATH=/app/src/publisher/src` in the Dockerfile, and both CMDs move
from `python -m publisher.consumer` to `python -m consumer`.

**Why:** user decision, matching AD-018's shape for `api`. uv_build's
`find_roots` requires a package directory, so `package = false` is the only
mechanism that permits loose modules while still resolving dependencies
through the workspace.

**Implication — a real constraint this creates:** flat services expose bare
top-level module names, and the pytest `sys.path` carries every flattened
service at once (`pythonpath` gains `src/publisher/src` alongside
`src/api/src`). Bare names resolve first-match-wins, so publisher's modules
must not collide with `api`'s `{dependencies, errors, main, migrate}`.
`consumer` and `processing` are clear. **`agent` is namespaced and immune
only while it stays an installable package** — flattening it would collide
its natural `consumer.py` with the publisher's. Decide before that feature
starts.

### AD-027 — PUT/GET reimbursement state-transition rules clarified: three source statuses confirmed, review writes are append + update, uuid-not-found is 404, no identity-field gate

**Date:** 2026-08-08
**Status:** Active

Four internal contradictions in `docs/SCOPE.md`'s PUT/GET sections, surfaced
while specifying `api-get-reimbursement`/`api-put-reimbursement`, are
resolved:

1. **PUT source statuses.** `human-rejected`, `auto-rejected`, and
   `human-review` are all PUT-eligible, exactly as the API section states.
   The Decisions section's "not possible to Human-Approve if the
   Reimbursement is rejected" line does not exclude `human-rejected` — it
   refers to the two already-*approved* statuses (`auto-approved`/
   `human-approved`), which were already excluded from PUT's reachable set;
   money already disbursed cannot be clawed back, which only applies to an
   approved item, not a rejected one.
2. **"Do not override the existing record, create a new one."** Refers to
   `human_review`, which is already DB-enforced append-only
   (`human_review_append_only` trigger, `0002.create-human-review.sql`). PUT
   inserts a new `human_review` row per review event and updates
   `reimbursement.status` (and the approval-only fields) in place — it never
   creates a second `reimbursement` row.
3. **404 semantics.** `GET /api/v1/reimbursement`'s list endpoint never
   returns 404 — a filter matching zero rows is still `200` with an empty
   `data` array; SCOPE.md's documented 404 for GET is dropped as a spec
   error. `PUT /api/v1/reimbursement/:uuid` gains 404 for an unknown `uuid`
   instead — SCOPE.md's own PUT Responses list omitted it despite PUT
   operating on a path parameter.
4. **Approval field completeness.** No additional application-level gate
   beyond the DB's own constraints and the payload's own required fields.
   `receipts_value`/`receipts_date`/`receipts_currency` being NULL on a
   `human-review` row is expected — the approval payload already supplies
   them (SCOPE.md's existing "all fields from the payload are required for
   approval" rule already covers this). `submitted_by`/`submitted_at` are
   not part of the PUT payload and are not gated at approval either — both
   are already guaranteed non-null, since `POST /api/v1/reimbursement`
   requires them on every item (`shared.models.ReimbursementRequest`).

**Why:** user decisions made while resolving four contradictions between
`docs/SCOPE.md`'s API section, its own Decisions section, and the actual
`reimbursement`/`human_review` schema (AD-003/AD-004), surfaced during the
Specify/Discuss phase for `api-get-reimbursement`/`api-put-reimbursement`.

**Implication:** `docs/SCOPE.md`'s PUT/GET sections and the Human Review
Decisions bullets are amended in place with inline notes pointing here,
following the AD-020 precedent — the original text is corrected rather than
left to silently contradict the two new specs.

**Addendum 2026-08-08 — reject has its own, opposite-direction completeness
gate.** Point 4 above resolved *approval's* gate (none needed — the payload
backfills the three receipt fields). Rejecting does **not** backfill them
(`SCOPE.md:195`: "just the `reason` and `approved_by` are required") — so a
reimbursement rejected while `receipts_value`/`receipts_date`/
`receipts_currency` are still NULL would stay permanently incomplete, since
no other write path sets them. User decision: **reject is blocked with
`400` unless all three are already non-null on the row**, making no DB
change. This creates a known dead end, accepted as-is: a `human-review` row
that never had these three fields extracted can only be resolved by
*approving* it (which supplies fresh values via its own payload) — rejecting
such a row is impossible until/unless a future feature adds a way to correct
these fields independently of a review decision. Recorded here rather than
as a new AD — same feature pair, same session, extends point 4 rather than
contradicting it.

---

### AD-028 — GET's `status` filter accepts a comma-separated list, not a single value

**Date:** 2026-08-08
**Status:** Active

`GET /api/v1/reimbursement?status=` accepts one or more of the five
client-facing status values joined by commas (e.g.
`status=human-review,auto-rejected`), matching rows whose status is any one
of the listed values (SQL `IN (...)`). A single value remains valid syntax
(the one-element case). Any comma-separated segment that isn't one of the
five values invalidates the whole filter (`400`) — same as today's
single-value validation; `pending` remains excluded either way (AD-003 —
internal-only status, never client-facing).

**Why:** user decision — a reviewer's dashboard commonly needs several
statuses in one call (e.g. "everything rejected":
`auto-rejected,human-rejected`) rather than N calls merged client-side.

**Implication:** `docs/SCOPE.md:157-161` amended in place to document the
comma-separated form. The query-param type is a raw `str` (split and
validated in code), not FastAPI's native `list[str]` repeated-param
binding — a repeated `?status=a&status=b` stays a validation error; only the
comma-joined single-param form is accepted.

---

### AD-029 — Generic DB pool lifecycle relocated: `shared.reimbursement.repository.managed_pool` → `shared.db.managed_pool`

**Date:** 2026-08-08
**Status:** Active

`managed_pool()` — construct an `asyncpg.Pool` from `DatabaseConfig`,
guarantee `.close()` on exit — moves to a new `shared/db.py`, mirroring
`shared/producer.py`'s existing shape (a generic, domain-agnostic
resource-lifecycle module at the `shared` root). `shared.reimbursement.repository`
keeps only reimbursement-table SQL, matching its own module docstring's
stated scope ("every SQL statement against the reimbursement table").

**Why:** user decision, surfaced while designing `api-get-reimbursement`.
`managed_pool` has zero reimbursement-specific logic — it takes a generic
`DatabaseConfig` and yields a generic `asyncpg.Pool`. Placing it under
`shared.reimbursement` mis-scoped it as domain code, the way placing
`managed_producer` under a hypothetical `shared.reimbursement.producer`
would have — and `managed_producer` was correctly kept generic at
`shared/producer.py` from the start (AD-010/AD-019). This corrects a
placement inconsistency in already-shipped code
(`publisher-consume-request`, AD-017/AD-025's persistence layer), not a new
pattern being introduced for the new features alone.

**Implication:** existing import sites move: `src/publisher/src/consumer.py`,
`src/publisher/tests/test_integration.py`, `src/agent/src/agent/consumer.py`,
`src/agent/tests/test_integration.py`, and
`src/shared/tests/reimbursement/test_repository.py` (its `DescribeManagedPool`
test class relocates to a new `src/shared/tests/test_db.py`, matching the
1:1 test-mirrors-source convention AD-009 established). Pure relocation, no
behavior change. Any future domain package needing DB access imports
`shared.db.managed_pool`, never a sibling domain's repository module. This
is a prerequisite refactor task for whichever of `api-get-reimbursement`/
`api-put-reimbursement` is implemented first.

**Correction (2026-08-08, PR review):** this record originally inventoried
only three import sites and omitted `src/agent/src/agent/consumer.py` and
`src/agent/tests/test_integration.py`, both of which also imported
`managed_pool` from `shared.reimbursement.repository`. The initial
implementation of this task moved the two publisher sites and the test
class but left the original `managed_pool` definition and both agent
import sites untouched, producing a duplicate, byte-identical
implementation. Fixed as part of PR review remediation: the duplicate
definition is deleted from `shared.reimbursement.repository`, both agent
import sites now import from `shared.db`, and a repo-wide grep confirms a
single definition remains.

---

### AD-030 — Reimbursement Agent gains an upfront receipt-date extraction node; reject always outranks the mandatory human-review rule; BRL-only, `claimed_amount_brl`-first value resolution; decision-stage error handling deferred

**Date:** 2026-08-08
**Status:** Active

Surfaced during Specify for `agent-decide-reimbursement` (the decision-logic
half of the Reimbursement Agent — reject / auto-approve / human-review —
that `agent-consume-reimbursement` explicitly deferred). Four related
decisions:

1. **Field extraction is a single, unconditional LLM step, ahead of every
   other rule.** No sample payload (`docs/original/sample.json`) carries a
   structured receipt-date field — it only ever appears inside
   `raw_ocr_text`. An LLM extraction step resolves the requested value,
   currency, and `receipts_date` **together, on every reimbursement,
   unconditionally** — before the reject rule or any approval-policy rule
   evaluates. Revised once during Specify: the first pass (based on the
   Q&A alone) had value/currency staying deterministic-first
   (`claimed_amount_brl` skipping the LLM when present) with only date
   unconditional; the user's own updated `reimbursement-processing.png`
   showed the extraction box running unconditionally for *all* fields, and
   asked to reconcile, the user confirmed "unconditional for everything."
   The extraction step's internal composition (whether it pre-fills a
   minimum object from directly-readable fields like `claimed_amount_brl`
   before calling the LLM, or resolves everything through the LLM in one
   pass) is explicitly left open — user: "it's gonna depend."
2. **Reject always wins.** The 90-day-old-receipt reject rule
   (`docs/SCOPE.md:257`) takes precedence over the mandatory `>2000`
   human-review rule (`docs/SCOPE.md:26`) — an old, large-value receipt is
   `auto-rejected`, not routed to human review.
3. **BRL-only.** Currency resolves through the same unconditional
   extraction step as value and date, not a separate deterministic-only
   path. `submitted_at` (already guaranteed non-null by
   `POST /api/v1/reimbursement`) is the fixed reference date the 90-day
   window is measured against. Once both required fields resolve, the
   `≤200`/`>2000`/ambiguous-zone split is decided by the deterministic
   policy step alone — a second, separate LLM call (the guardrail/judge)
   is spent only on the ambiguous `200–2000` zone.
4. **Decision-stage error-handling mechanism is not decided.** Unlike
   `agent-consume-reimbursement`'s resolve-stage retries (free DB queries),
   a decision-stage retry re-invokes a billed LLM call. Whether to reuse
   the resolve stage's retry-then-escalate machinery as-is, split by cause,
   or cap LLM-specific retries independently is deferred to a later
   session — tracked as **R-011** in `.specs/RISKS.md`. This spec asserts
   only the invariant that no reimbursement is silently lost, not a
   mechanism.

**Why:** user decisions made directly during Specify, mirroring the
AD-020/AD-027/AD-028 precedent of amending `docs/SCOPE.md` in place rather
than leaving it silently contradicted. Decision 4 in particular reflects the
user's explicit reasoning that LLM calls are billed and unbounded retries
of them are a real cost risk the resolve stage's free-DB-query retries
never had — evaluated later with real cost data, not guessed at now.

**Implication:** `docs/SCOPE.md`'s Reimbursement Agent section is amended in
place with inline notes pointing here, same style as AD-020/AD-027/AD-028.
The `reimbursement-processing.png` diagram is being amended separately by
the user to add the new upfront extraction node — not done by this session.
Design for `agent-decide-reimbursement` — LangGraph graph shape, node
wiring, LLM/SLM model choice, prompt text — is explicitly not started this
session; the user has a concurrent refactor in flight on
`feature/6_reimbursement_consumer` and asked that Design wait until that
syncs.

---

### AD-031 — `api`/`publisher`/`reimbursement` switch build backend from `uv_build` to `setuptools` (`package-dir` mapping); cross-package test helpers consolidate into `shared.testing`

**Date:** 2026-08-09
**Status:** Active
**Amends:** AD-018 (`api` flat layout) and AD-026 (`publisher` flattens) —
same flat-on-disk intent preserved, different build-backend mechanism.

**Root cause:** `reimbursement` (renamed from `agent`, kept flat per the
AD-018/AD-026 precedent) and `publisher` both had flat, bare `config.py`/
`consumer.py` modules. A single `uv run pytest` invocation across both put
every service's `src/` on one shared `sys.path` in one process; Python
caches imports by bare name in `sys.modules` process-wide, so once `config`
resolved to one package's file, every `from config import X` elsewhere in
that same pytest session — including inside the *other* package's own
code — got that same cached module. Confirmed empirically:
`uv run pytest src/reimbursement src/publisher` failed immediately with
`ImportError: cannot import name 'load_agent_config' from 'config'`
(resolved to `publisher`'s `config.py`).

**Resolution:** `api`, `publisher`, and `reimbursement` each switch
`[build-system]` from `uv_build` to `setuptools`, adding
`[tool.setuptools.package-dir] <pkg> = "src"`. This gives each real,
dotted-import namespacing (`api.main`, `publisher.consumer`,
`reimbursement.config`, etc.) while keeping the exact same flat
`src/<pkg>/src/*.py` layout on disk — no directory moves, no doubled path.
`setuptools`' `package-dir`-mapped explicit-layout discovery auto-detects
nested subpackages (e.g. `api.reimbursement.create`,
`reimbursement.agent.nodes`) with a single config line, no manual `packages`
list.

**Rejected alternatives** (both explicitly ruled out with the user before
this design was chosen):
1. Reverting to `uv_build`'s doubled-path convention
   (`src/<pkg>/src/<pkg>/*.py`, AD-018/AD-026's pre-flatten shape) — verified
   via Context7 against `uv_build`'s own source (`find_roots`) that it
   cannot decouple an import name from a same-named directory even in
   `namespace = true` mode, so this was the only way `uv_build` could give
   real namespacing. The user rejected the doubled path outright.
2. Pytest-only isolation — a standalone, nested `[tool.pytest.ini_options]`
   per package plus a `--confcutdir` override reaching into `api/tests/`.
   Rejected because it made `reimbursement`'s tests depend on `api`'s
   private test tree, which the user does not want; it also required a
   second Postgres testcontainer or new orchestration tooling to keep the
   workspace-root `conftest.py`'s shared fixtures reachable.

**Side effect — test-helper consolidation:** `valid_reimbursement_item` and
the generic DB-provisioning utilities (`POSTGRES_IMAGE`,
`MAINTENANCE_DATABASE`, `database_name`, `with_database`, `maintenance_url`,
`disposable_database_name`, `guard_is_test_database`) previously lived in
`api/tests/helpers.py`, reached by `publisher`'s, `reimbursement`'s, and
`shared`'s own tests, and by the workspace-root `conftest.py`, via bare-name
`pythonpath` resolution. A real installed `api` package no longer offers
that resolution to sibling packages, so these moved into `shared.testing` —
a normal package import every service already reaches — reconciling
`seed_reimbursement`'s signature to add `original_payload` support in the
process. `api`-specific fakes (`FakePool`, `_build_client`,
`valid_approve_payload`, `valid_reject_payload`) stayed local to
`api/tests/helpers.py`.

**Practical effect:** the root `pyproject.toml`'s
`[tool.pytest.ini_options]` collapses back to one unified config covering
all four packages (`testpaths = ["src/api", "src/publisher",
"src/reimbursement", "src/shared"]`), restoring the single
shared-Postgres-testcontainer architecture with zero new test-orchestration
tooling. `uv run pytest` at the workspace root runs all four packages
together again — 447 tests passed, the actual proof the collision is gone.

**Amendment (2026-08-09) — mechanism corrected: `setuptools`/`package-dir` → `uv_build`.**
The resolution above fixed the pytest collision but was never checked
against IDE/static-analysis tooling. It broke go-to-definition and
autocomplete for `api`/`publisher`/`reimbursement` in every
Pyright-family editor (VS Code/Pylance, Cursor/cursorpyright), confirmed
via direct `pyright` CLI runs against the shipped tree
(`reportMissingImports` on `reimbursement.schema`,
`reimbursement.agent.nodes`, `reimbursement.config`, etc.).

Root cause: `setuptools`' PEP 660 editable install generates a dynamic
`MetaPathFinder`-based finder script (`__editable___<pkg>_finder.py`, a
`MAPPING`/`NAMESPACES` dict executed at import time) to redirect the
import name onto the differently-named `src` directory. Static analyzers
don't execute that script — they resolve editable installs by walking
directory names, so an import name with no physically-matching directory
fails to resolve. `shared` (never touched by the original AD-031, always
on `uv_build`, always using a nested `src/shared/src/shared/*.py` layout)
resolved cleanly throughout, because `uv_build`'s editable install is a
plain static `.pth` path, not a dynamic finder — empirically confirmed via
an isolated repro (a throwaway `uv_build` package produced a plain `.pth`
and 0 `pyright` errors, versus the dynamic-finder case's
`reportMissingImports`).

Corrected mechanism: `api`, `publisher`, and `reimbursement` move from
`setuptools`+`package-dir` to `uv_build`, adopting the same conventional
nested src-layout `shared` already used successfully —
`packages/<pkg>/src/<pkg>/*.py`, import name matching a real physical
directory. The workspace-member container also renamed from `src/` to
`packages/`, matching uv's own documented workspace example
(`/astral-sh/uv`, `docs/concepts/projects/workspaces.md`, verified via
Context7). No dynamic remapping remains anywhere in the workspace.

Rejected alternatives:
1. Keep `src/` as the outer container name, just add the inner `<pkg>/`
   folder (`src/reimbursement/src/reimbursement/...`) — works, but doesn't
   match uv's own documented workspace convention (`packages/`, not
   `src/`); no reason to deviate when adopting the documented pattern
   costs nothing extra.
2. Flat layout (`packages/<pkg>/<pkg>/*.py`, no inner `src/`) via
   `uv_build`'s `module-root = ""` — empirically also resolves cleanly in
   Pyright and is one directory level shallower, but trades away
   src-layout's protection against a package being importable straight out
   of the project directory without being properly installed — the exact
   class of bug this feature exists to fix once. Not worth the tradeoff
   for one fewer path segment.
3. Symlink + `pyrightconfig.json` `extraPaths` workaround, keeping the
   flat `setuptools` layout on disk — more fragile (git symlink support,
   Docker `COPY -L`, cross-platform) and only patches the symptom for one
   tool rather than fixing the underlying dynamic-finder mismatch for all
   static tooling.

**Practical effect (amendment):** `src/` renamed to `packages/` repo-wide;
root `pyproject.toml`'s `testpaths`/`pythonpath`/workspace `members` now
read `packages/...`; `docker-compose.yml`, all three service Dockerfiles,
and `docs/codebase/*.md` updated to match. `uv run pytest` — 447 passed,
same count as before. `pyright` against all four packages' entry modules
(`api/main.py`, `publisher/consumer.py`, `reimbursement/agent/agent.py`,
`shared/testing.py`) — 0 `reportMissingImports`, confirming the IDE
regression is resolved. `docker compose up -d api publisher reimbursement`
— all three healthy, full `api → publisher → reimbursement` message chain
verified end-to-end via a live smoke POST.

### AD-032 — Reimbursement's LLM provider switches Ollama → Groq; per-node `ModelConfig` nesting is the project's convention for LLM-node configuration

**Date:** 2026-08-09
**Status:** Active

`reimbursement/config.py`'s `AgentConfig` replaces its flat `ollama_model`/
`ollama_base_url`/`ollama_timeout_seconds` fields with `ai: AIConfig`
(`api_key`, `timeout_seconds` — shared across every node's model) and
`models: AgentModelsConfig` (one `ModelConfig(model_name, temperature)` per
LLM node — `extract_fields` and `analysis` today). `build_graph()`
constructs two independent `init_chat_model("groq:<model>", ...)` instances,
one per node, each reading its own `ModelConfig` and sharing `AIConfig`.

**Why:** user decision — Ollama required a self-hosted GPU host with no
config-layer parity to `shared.config`'s `KafkaConfig`/`DatabaseConfig`
pattern, and both LLM nodes shared one model/temperature-less config with no
independent tuning. `model_name` deliberately has **no code-level default**
(fails fast via a `_require_env` helper if unset) — a financial-decision
agent should never silently run on an unvetted default model, unlike
Ollama's old `llama3.2` fallback. `temperature` defaults to `0.0` for both
nodes (low-variance output wanted for both structured extraction and a
pass/fail guardrail verdict). `timeout_seconds` stays on the shared
`AIConfig`, not per-node — it's an operational HTTP-call bound, not a
model-quality knob like `model_name`/`temperature`, so duplicating it per
node would gain nothing.

**Implication:** any future LLM node the reimbursement agent gains follows
this same nesting — a new `ModelConfig` field added to `AgentModelsConfig`,
sharing the existing `AIConfig`. `.env.sample` carries settled (non-blank)
placeholders for the two required model-name vars
(`EXTRACT_FIELDS_MODEL_NAME`/`ANALYSIS_MODEL_NAME` — `llama-3.3-70b-versatile`,
verified live via Groq's own model docs) so a fresh
`cp .env.sample .env` still works out of the box, even though the code
itself enforces no silent default. A real compliance-posture question was
flagged to the user during design — prompt payloads (`raw_ocr_text` in
particular) now leave the local Docker network for Groq's cloud API instead
of a host-machine Ollama instance — and the user chose to proceed without
adding scrubbing/redaction as part of this change.

---

### AD-033 — Publisher's insert+publish unit of work drops its transaction: insert commits immediately, a publish failure triggers an explicit compensating `DELETE`

**Date:** 2026-08-09
**Status:** Active
**Amends:** AD-017 (scoped — publisher's insert+publish only; AD-017's implicit-transaction pattern stays Active for every other write-plus-side-effect unit of work: `escalate_item`'s `send_human_review`, `review_reimbursement`'s `approve`/`reject` + `record_human_review_decision`)

`shared.reimbursement.use_cases.publish_pending` no longer runs inside
`async with conn.transaction():`. `insert_pending` commits immediately; the
`Reimbursement` publish is attempted after that commit; if it raises
`PublishFailed`, a new `delete_pending(conn, uuid)` (gated
`WHERE uuid = $1 AND status = 'pending'`) removes the row before the
existing `_requeue` path fires. Every branch of the delete (removed one row,
removed zero, or raised) is logged — the zero-and-removed cases via a
structured `logger.info` event, the raised case via `failure_log.write` (the
project's durable last-resort sink), per `CLAUDE.md`'s hard traceability
requirement.

**Why:** user decision, made under an explicit time constraint that ruled
out the transactional-outbox pattern `.specs/RISKS.md` R-001 already names
as the proper fix. Traced precisely during Design rather than assumed: this
is not a straight downgrade from AD-017's guarantee. Because the insert now
commits *before* the publish is even attempted, there is no window where a
`Reimbursement` message exists but its row doesn't — this closes R-001's
ghost-message case and its duplicate-via-commit-failure case outright, both
of which depended on the commit happening *after* the publish. What it opens
instead is narrower: an orphaned, un-recoverable `pending` row, possible only
if the process crashes between a publish failure and the compensating delete
completing — a compound of two independently low-probability events, not the
common-path window AD-017's transaction closed. This residual gap is
accepted knowingly, not discovered after the fact, and is why every delete
outcome is durably logged rather than silently absorbed the way a database's
own rollback would have been.

**Implication:** `docs/SCOPE.md:241-252` (Reimbursement Publisher / Error
Handling) is amended in place with an inline dated note — the "single
transaction" / "rollback the DB transaction" language it originally
specified no longer describes the shipped behavior for this path.
`.specs/RISKS.md` R-001 is updated to record the two consequences this
design closes and the narrower one it opens, distinguished explicitly rather
than merged into the same paragraph. `publish_pending`'s signature gains two
parameters (`failure_log_config: FailureLogConfig`, `retry: int`) — its one
production caller (`publisher.processing._insert_and_publish`) and its
direct-call test file (`shared/tests/reimbursement/use_cases/test_publish_pending.py`)
both update accordingly. See `.specs/features/publisher-compensating-delete/`
for the full spec/design and `is_duplicate`'s unchanged behavior (the INSERT's
own unique-constraint path is untouched by this change).

---

### AD-036 — `publish_pending`'s insert still gets its own `conn.transaction()`, closed before the publish is attempted — a savepoint boundary, not a re-introduced dual-write window

**Date:** 2026-08-09
**Status:** Amended by AD-037 (corrects this entry's "no constraint to violate" rationale and extends the same savepoint treatment to `delete_pending`; the core decision — insert keeps its own narrow transaction — is unaffected)
**Amends:** AD-033 (implementation-level refinement, discovered during that feature's Execute phase — not a reversal: AC1's actual requirement, "no transaction spans the publish call," still holds exactly)

`design.md`'s Architecture Overview describes the insert and the compensating
delete as "auto-committing statements... uninstrumented by any transaction."
Implementing that literally (a bare `insert_pending(conn, item)` call with no
`conn.transaction()` at all) surfaced a real defect during Execute: a real
Postgres `UniqueViolationError` raised by the insert, when not caught inside
its own transaction/savepoint, poisons whatever transaction context already
encloses the connection — any subsequent statement on that same connection
then fails with `InFailedSQLTransactionError` until the enclosing transaction
ends. This is not hypothetical: `packages/publisher/tests/test_processing.py`'s
`DescribeADuplicateItem::it_drops_the_item_without_storing_a_second_row`
reproduced it directly (`RealPool` shares one connection wrapped in the `db`
fixture's own outer transaction). `escalate_item.send_human_review` already
documents and defends against this identical class of failure for its own
INSERT (`processing.py:218-221`): "without it, a caught UniqueViolationError
below poisons the connection's enclosing transaction state... It acts as a
savepoint boundary, not an atomicity guard."

**Resolution:** `publish_pending` wraps only `insert_pending`'s call in
`async with conn.transaction():`, committing (or rolling back to the
savepoint, when nested inside an already-open transaction) before `publish()`
is ever called. `delete_pending` is deliberately NOT given the same
wrapper — a `DELETE` has no constraint to violate, so the realistic AC6
failure mode (a broken connection) leaves nothing for a savepoint to
protect, and adding one would be unjustified complexity for a risk that
doesn't exist.

**Why:** AD-033's actual, binding guarantee is "no transaction spans the
Kafka publish call" (spec.md AC1) — a transaction that opens and fully closes
around one single INSERT statement, strictly before the publish is attempted,
does not reopen that window; it is observably identical, at the wire level,
to a bare autocommitted statement in production (where no outer transaction
exists to nest inside). The only place the distinction is visible is when a
connection is already inside a transaction — production's `pool.acquire()`
connections never are, but the test suite's shared-connection fixtures
sometimes are, and a future caller might be too. Matching `escalate_item`'s
already-established precedent is the smaller, more consistent fix than either
leaving a real defect in place or inventing a different pattern for the same
problem.

**Implication:** `shared.reimbursement.use_cases.publish_pending`'s docstring
records this explicitly so a future reader doesn't "simplify" it back to a
bare insert call and reintroduce the poisoned-transaction defect. No spec.md
or design.md text needed correction — AC1's literal requirement was never
violated, only the Architecture Overview's more casual "uninstrumented by any
transaction" phrasing was more literal than the implementation ended up
being.

---

### AD-037 — `delete_pending`'s call also gets its own savepoint transaction; AD-036's "no constraint to violate" rationale corrected

**Date:** 2026-08-10
**Status:** Active
**Amends:** AD-036 (corrects its stated rationale for not wrapping `delete_pending`, and extends the savepoint treatment AD-036 already gives `insert_pending` to `delete_pending` too)

AD-036 justified skipping a `conn.transaction()` around `delete_pending`'s
call by stating "a `DELETE` has no constraint to violate." That claim is
factually wrong for this schema:
`packages/api/src/api/migrations/0002.create-human-review.sql:6` declares
`human_review.reimbursement_uuid REFERENCES reimbursement (uuid)
ON DELETE RESTRICT` — a `DELETE FROM reimbursement` can raise a
`ForeignKeyViolationError` in general. The real reason this is safe *today*
is a business invariant, not a schema fact: a row `delete_pending` can reach
is still at `status = 'pending'`, and nothing writes a `human_review` row
against a `uuid` the Agent has never received (the same reasoning
`spec.md`'s Edge Cases table already gives for why the `AND status='pending'`
gate is "unreachable-as-false in practice" today). That invariant is real but
unenforced by any constraint or test — a future change that allows a
`human_review` row to exist earlier in the lifecycle would silently
reintroduce, for `delete_pending`, the exact poisoned-enclosing-transaction
defect AD-034 fixed for `insert_pending`.

**Resolution:** `_compensate`'s call to `delete_pending` is now wrapped in
its own `async with conn.transaction():`, identical in shape and rationale to
`insert_pending`'s existing treatment — a savepoint boundary that commits (or
rolls back to the savepoint) before `_compensate` returns, so a real
`ForeignKeyViolationError` (or any other exception the DELETE could raise)
never poisons the connection's enclosing transaction state. This costs one
extra `BEGIN`/`COMMIT` pair on a path that only runs after a publish already
failed — negligible next to the Kafka round-trip that just failed.

**Why:** identified during PR review (`code-review` finding, PR #14) as a
real, if not-yet-triggerable, defect class left open by AD-036's own reasoning
being applied inconsistently between the two statements it covers. Confirmed
by direct inspection of the migration file, not assumed.

**Implication:** `design.md`'s Architecture Overview (prose + mermaid
diagram) is corrected to show both `insert_pending` and `delete_pending`
running inside their own narrow transaction, closed before the publish call
begins or resumes — AD-036's claim that "no spec.md or design.md text needed
correction" is itself corrected by this entry. `publish_pending.py`'s
docstring is updated accordingly. No test previously existed pinning the
`status='pending'` invariant this decision (and AD-036's) safety depends on;
none is added here either — `delete_pending`'s existing
`AND status = 'pending'` gate already defends the data itself, and the
savepoint wrapper defends the connection, independent of whether that
invariant ever changes.

---

### AD-034 — `traceability-correlation-ids` executed autonomously: sibling-feature test breakage left untouched, TRC-11 gets one dedicated test, 9 tasks run inline without a sub-agent offer

**Date:** 2026-08-09
**Status:** Active

Implementing `traceability-correlation-ids` (design.md treated as
user-confirmed per the session's own operating instructions) surfaced three
judgment calls with no user available to confirm them mid-session:

1. **Pre-existing, unrelated test breakage left alone.** `main` at this
   feature's base commit already had 1 failing test
   (`reimbursement/tests/test_config.py`'s Ollama-default assertion) and 4
   uncollectable test files (`test_agent.py`, `test_analysis.py`,
   `test_extract_fields.py`, `test_integration.py`, all
   `PLACEHOLDER_PROMPT` import errors) — both squarely AD-032's scope
   (`agent-model-config`, Designed but not yet Executed on its own branch;
   confirmed via `.specs/features/agent-model-config/tasks.md`'s T1/T4/T5/T6).
   This feature's tasks do not touch `config.py`/`prompts/*.py` or fix
   those four files — the "full suite green" gate is interpreted as "zero
   *new* failures vs. this exact pre-existing baseline," verified
   byte-identical before/after every task that touches `packages/reimbursement`.
2. **TRC-11 gets a dedicated test; TRC-01..10 do not.** spec.md's Out of
   Scope table excludes tests for "TRC-01..10" by name — TRC-11 (api's own
   `logging.basicConfig` call, without which every other requirement's new
   `.info()` line silently never emits) is arithmetically outside that
   range. Read as a deliberate signal, not an oversight, and treated
   accordingly: one unit test added in `packages/api/tests/test_main.py`,
   none added anywhere else.
3. **9 tasks executed inline, no sub-agent batch offer.** `tlc-spec-driven`'s
   own rule is "offer-then-confirm, never auto-spawn" once task count
   exceeds ~8 — this feature generated exactly 9, and no user was reachable
   to accept or decline the offer. Given every task was a small (1-4 line),
   low-risk, already-fully-read edit with no cross-task ambiguity, inline
   single-agent execution was the more reasonable default than blocking on
   an unanswerable prompt or arbitrarily under/over-splitting the work into
   sub-agent batches on the assistant's own authority.

**Why:** each call follows the session's explicit mandate to make the most
reasonable autonomous decision consistent with existing Decisions/
Conventions rather than stall for input that cannot arrive, and to record
the call here rather than silently pick one interpretation.

**Implication:** `traceability-correlation-ids` shipped with the
`reimbursement` package's pre-existing 1-failure/4-collection-error state
unchanged — whoever executes `agent-model-config`'s tasks.md next will fix
it as part of that feature, not this one. `.specs/features/traceability-correlation-ids/tasks.md`
documents all three calls inline (its "Pre-Execute Note" and Test Coverage
Matrix); `validation.md` (independent Verifier, PASS) confirms the baseline
claim was checked, not assumed.

---

### AD-038 — `.env` is mounted read-only into every uv-workspace container at runtime; `docker-compose.yml`'s `environment:` blocks are pruned to Docker-network-topology values only

**Date:** 2026-08-09
**Status:** Active

`api`, `publisher`, `reimbursement`, and `migrate` each gain a runtime
volume mount (`./.env:/app/.env:ro`) in `docker-compose.yml`, making their
existing `load_dotenv()` calls (already present at every entrypoint) the
real config-resolution mechanism inside Docker, not just on the host.
Correspondingly, each service's `environment:` block is pruned to **only**
the values that cannot be a single shared `.env` value regardless of where
the process runs — `KAFKA_BOOTSTRAP_SERVERS`, `DATABASE_URL`, and (for
`reimbursement`) `LANGFUSE_HOST`, all Docker-network hostnames that differ
from `shared.config`'s own host-default (`localhost:...`). Every other
value a service's config loader reads (secrets, model config, tuning,
SASL/TLS) reaches the container exclusively through the mounted `.env` —
`docker-compose.yml` no longer duplicates it via `${VAR}` substitution.
`.dockerignore` continues excluding `.env` from every build context, so the
mount is runtime-only and never bakes a secret into a distributable image
layer.

**Why:** user decision, made explicitly during `e2e-pipeline-flow`'s
Specify phase after the agent's first draft under-scoped the ask — the
agent had proposed only patching `docker-compose.yml`'s missing/asymmetric
`environment:` entries (keeping compose's own `${VAR}` substitution as the
delivery mechanism), which the user rejected: "I want the application to
rely on dotenv, i don't want propagating over the docker-compose file...
rely on .env always." This was the concrete trigger: `.env` is
`.dockerignore`d and never reachable inside any container today, so
`load_dotenv()` has always been a silent no-op under Docker — and that gap
had already caused real drift (`reimbursement`'s compose block was missing
`GROQ_API_KEY`/model-name vars entirely, `publisher`/`reimbursement` were
both missing the Kafka SASL/TLS vars `api` alone received).

**Implication:** this is a project-level configuration-delivery convention,
not scoped to the three services this feature touches — any future
uv-workspace service follows the same shape (mount `.env`, keep only
genuine topology values in `environment:`). `LANGFUSE_SECRET_KEY` gains its
own `.env.sample` entry (mirroring `LANGFUSE_INIT_PROJECT_SECRET_KEY`'s
placeholder) so `reimbursement` resolves it directly with no compose-level
rename. A new guard test (`packages/api/tests/test_dotenv_config_parity.py`)
asserts both directions of drift — an `environment:` block gaining an
unexpected non-topology var, or `.env.sample` missing a var a service's
loader reads — so this exact class of gap can't recur unnoticed. See
`.specs/features/e2e-pipeline-flow/design.md` for the full design and
`spec.md`'s `ENV-01..04` for the traceable requirements.

---

### AD-039 — R-011 resolved: a decision-stage failure escalates to `human-review` immediately, not retry-then-escalate or a cause-differentiated policy

**Date:** 2026-08-10
**Status:** Active

`reimbursement/validation.py`'s `_decide` — on any exception from
`agent.decide()` (an LLM call failure, a malformed structured-output
response, a genuine `apply_decision` write failure inside a node) — now
escalates the row to `human-review` via a new `_escalate_decision_failure`
helper, in addition to its existing `failure_log` write. The mechanism is
**immediate escalation**: no retry of the LLM call, no cause-differentiated
handling (an LLM failure and a DB write failure escalate identically).
`decision_reason` carries the full attempt history — any pre-existing
resolve-stage retries already on the message envelope (AD-014) plus one new
entry for this failure — rendered through the existing `render_history`
machinery, which is parameterized with a `header` kwarg
(`shared.reimbursement.use_cases.send_human_review`) so this path's wording
("Decision-stage failure...") doesn't falsely claim a retry ceiling was
reached, the way its retry-ceiling-specific default would have. `shared.models.Stage`
gains a `"decide"` value so the failure can be expressed as an
`AttemptError`. A decision computed in-memory but never durably persisted
(the failure occurs inside the write itself) is discarded in favor of the
escalation — a write that never completed is never trusted as final,
regardless of what was computed.

**Why:** this was `agent-decide-reimbursement`'s own AGD-25, explicitly
deferred as **R-011** — "the mechanism is not selected by this spec... LLM
calls are billed, unlike the resolve stage's free DB retries" (AD-030). User
decision, made during this feature's Discuss phase: immediate escalation
over retry-then-escalate, because retrying a billed LLM call has an
unbounded cost profile with no cost/tradeoff evaluation behind it yet, while
escalating a mission-critical financial row to a human reviewer is the safe
default either way. Full (unsanitized) error detail in `decision_reason`
reuses the AD-014 precedent (DB rows are inside the same trust boundary as
`original_payload`) rather than inventing new redaction — stdout logging
stays sanitized via the existing `sanitize()`, unchanged.

**Implication:** `agent-decide-reimbursement/spec.md`'s AGD-25 Assumptions
row is amended in place to point here. Any future decision-stage failure
mode this feature didn't anticipate still escalates the same way — there is
no second fallback path to keep in sync. See
`.specs/features/agent-decision-error-escalation/` for the full spec/design
and `spec.md`'s `ADE-01..08` for the traceable requirements.

---

## Handoff

**Last updated:** 2026-08-09

**Done and merged to `main`** (see git history / merged PRs #3-#11 for
detail): `db-schema-migrations`, `api-post-reimbursement`,
`publisher-consume-request`, `agent-consume-reimbursement`,
`api-get-reimbursement`, `api-put-reimbursement`, `api-reimbursement-detail`,
`agent-decide-reimbursement` (the Reimbursement Agent's full decision graph —
`reimbursement/agent/`, 5 nodes, wired via `agent.py`), `refactoring-package-namespacing`
(AD-031, `uv_build` + nested src-layout). The prior entries in this section
describing these as "in flight" are stale as of this update — superseded.

**In flight as of the `adc4309` setup commit (2026-08-09), three sibling
features executed concurrently by separate agents on separate worktrees,
each branching from the same `main` tip, each expected to merge (and
possibly conflict with one another) independently:**

- **`agent-model-config`** (AD-032) — Ollama → Groq LLM provider switch for
  the Reimbursement Agent's two LLM nodes. Design confirmed and partially
  pre-applied directly to `main` at the setup commit (`config.py`'s default,
  `prompts/{analysis,extract_fields}.py`'s public API) **without** its own
  tests updated yet — `tasks.md` (T1-T8) exists and owns fixing this;
  status as of this update: not yet known to have executed (this session
  did not touch it — see AD-034).
- **`publisher-compensating-delete`** (AD-033) — publisher's insert+publish
  unit of work drops its transaction; a publish failure now triggers an
  explicit compensating `DELETE` instead of a rollback. Design confirmed;
  execution status not observed by this session (different worktree).
- **`traceability-correlation-ids`** (this session) — **Done.** All 11
  requirements (TRC-01..11) implemented across 9 tasks (T1-T9), independent
  Verifier **PASS** (`validation.md`: 11/11 ACs spec-anchored, gate 508
  passed/1 pre-existing-unrelated-failed/4 pre-existing-unrelated-errors
  workspace-wide, sensor 1/3 killed with the other 2 an accepted consequence
  of spec.md's own no-new-tests decision — see AD-034). Lives on branch
  `feature/11-traceability-correlation-ids`, not yet merged to `main`. Three
  autonomous judgment calls made and recorded as **AD-034**.

**Next step for a future session:** once `agent-model-config` merges (fixing
`packages/reimbursement/tests/{test_config,test_agent,test_analysis,test_extract_fields,test_integration}.py`'s
pre-existing breakage), re-run `packages/reimbursement`'s full gate to
confirm `traceability-correlation-ids`' TRC-07/TRC-08/TRC-09 changes
(currently verified by code inspection only, per AD-034) are also exercised
by the now-collectible `test_agent.py`/`test_validate.py` suite. Expect a
merge conflict between `traceability-correlation-ids` and
`agent-model-config` on `reimbursement/agent/{agent.py,nodes/analysis.py}` —
resolution is the user's, per this session's own operating instructions.

**Next step:** get `agent-decide-reimbursement/spec.md` confirmed by the
user; then wait for the `feature/6_reimbursement_consumer` refactor to sync
before starting Design (which will need to decide how much of
`agent-consume-reimbursement`'s `Stage`/`AttemptError`/`MessageOutcome`/
`escalate_existing` machinery this feature reuses vs. extends, and resolve
R-011 before committing to a decision-stage error-handling mechanism).

**Untracked, not part of any session's work:** `docs/codebase/*.md` showed
as modified in git status at the start of this session (likely a concurrent
`architecture-evaluate` run) — verify their diff before trusting them if
picking this up fresh.

**Repo-wide note (kept from prior entry):** `.specs/` was untracked by git
until 2026-08-07 — a `.gitignore` pattern bug (`!.spec`/`!.spec**`) silently
kept every spec local-only. Fixed and committed on
`feature/4-create-reimbursement-endpoint`.
