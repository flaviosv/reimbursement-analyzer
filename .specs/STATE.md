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
**Status:** Active

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
**Status:** Active

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
**Status:** Active

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

---

## Handoff

**Last updated:** 2026-08-07

**Done:** `db-schema-migrations` — merged to `main` via PR #3
(`9799607`). `api-post-reimbursement` — all 11 tasks complete, standalone
Verifier PASS (`.specs/features/api-post-reimbursement/validation.md`),
branch `feature/4-create-reimbursement-endpoint`. Next: push, open a draft
PR against `main`, run `/code-review` + `/tests-code-review`, post findings,
wait for manual validation.

**In flight:** `publisher-consume-request` — spec + context drafted, status
"Ready for design." No `design.md`/`tasks.md`/code yet. This spec existed
locally, untracked, before `.specs/` was ever committed to git (see the
`.gitignore` fix below) — worth confirming nothing in it was lost or is
stale relative to `api-post-reimbursement` landing.

**Repo-wide note:** `.specs/` was untracked by git until this session — a
`.gitignore` pattern bug (`!.spec`/`!.spec**`, which doesn't match `.specs/`
and can't un-ignore a directory via `**` anyway) silently kept every spec
local-only. Fixed to `!.specs`/`!.specs/**` and the whole directory committed
for the first time on `feature/4-create-reimbursement-endpoint`.

**Branch:** `feature/4-create-reimbursement-endpoint`, based on `main` at
`9799607` (post PR #3 merge).
