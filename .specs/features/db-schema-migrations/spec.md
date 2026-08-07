# Database Schema & Migrations Specification

## Problem Statement

`docs/SCOPE.md` mandates that the `Reimbursement` and `HumanReview` schemas be
created through migrations, and that bootstrap ensure "PostgreSQL DB structure
must be created / updated" (`SCOPE.md:55`). Today no schema, no migration tool,
and no SQL exist — `docker-compose.yml` provisions an empty `reimbursementanalyzer`
database and nothing else (`CONCERNS.md:19-28`). Every downstream feature
(publisher persistence, agent decisions, the GET/PUT endpoints) is blocked on
this.

## Goals

- [ ] Two tables — `reimbursement` and `human_review` — created by versioned,
      repeatable migrations, with every constraint in `SCOPE.md:89-94` and
      `SCOPE.md:111-114` enforced by the database rather than by convention.
- [ ] Migrations applied exactly once at stack bootstrap, before any service
      accepts traffic, with no dependence on a service's startup path.
- [ ] The stored schema is sufficient to reconstruct any decision after the
      fact, satisfying the audit-trail requirement (`SCOPE.md:15`, `SCOPE.md:21`).

## Out of Scope

| Feature | Reason |
| ------- | ------ |
| Application data-access code (repositories, queries, connection pooling) | This spec delivers schema only; persistence logic belongs to the publisher/agent/API features |
| Kafka topic creation at bootstrap | Separate bootstrap concern (`SCOPE.md:54`); unrelated to DB structure |
| Reimbursement/HumanReview Pydantic models in `shared` | Consumed by the services, not by migrations; belongs to the feature that first needs them |
| Seed / fixture data | No requirement in `SCOPE.md`; `docs/original/sample.json` is agent test input, not schema content |
| Authentication-derived reviewer identity | Deliberate scope decision (`SCOPE.md:295-297`); `reviewed_by` stays a plain email column |
| Backfill or data-migration logic | Greenfield database — no existing rows to migrate |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here — nothing is left silently unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | -------------- | --------- | ---------- |
| Migration file location | `src/api/migrations/*.sql` | User-directed: the API layer owns migrations | y |
| Migration tool | yoyo-migrations (plain SQL, advisory lock, rollback) | Matches the repo's raw-SQL / no-ORM convention; Alembic would drag SQLAlchemy into a codebase that deliberately has no ORM | y |
| Execution point | One-shot compose `migrate` service reusing the api image; `api`/`agent`/`publisher` gate on `service_completed_successfully` | Running DDL in the FastAPI lifespan races across replicas and blocks readiness; a one-shot job maps 1:1 to a k3s Job / initContainer | y |
| `Published` field (`SCOPE.md:84`) | Dropped entirely | User-directed. The agent's staleness rule (`SCOPE.md:229`) compares a published date carried **on the Kafka message** against the row's `updated_at`, so no column is needed | y |
| Initial status for a freshly-inserted row | Sixth value `pending`, `NOT NULL DEFAULT 'pending'` | The publisher inserts with only UUID + payload (`SCOPE.md:210`); all five statuses in `SCOPE.md:72-76` describe outcomes. Nullable status would defeat the constraint | y |
| `Decision` / `Decision Layer` / `Layer` (`SCOPE.md:83`, `:93`, `:307`) | Dropped entirely | User-directed. Also structurally redundant: the probabilistic layer never decides, it only extracts (`SCOPE.md:248-249`, `:259`), so the column would read `deterministic` on every auto-decided row | y |
| `receipts_date` column (gap #8) | Added, `DATE NULL` | Sole input to the 90-day reject rule; without it a rejection is not reconstructable from stored data, breaking `SCOPE.md:15` | y |
| `decision_reason` column (gap #7) | Added, `TEXT NULL` | The PDF requires justification for all three outcomes; today only `human_review` carries a reason, so auto-decisions record no "why" | y |
| `llm_assisted` column | Not added | User-directed. LLM involvement remains recoverable from `decision_reason` free text and from LangFuse traces; a dedicated queryable column was judged unnecessary | y |
| Test strategy | pytest against a throwaway `postgres:18` container started by testcontainers, with `TEST_DATABASE_URL` as an escape hatch for a caller-supplied server | User-directed (superseded an earlier decision to reuse the compose `postgres` service — see AD-008). An unenforced `CHECK` reads as a guarantee, so every constraint must be proven to reject bad rows, which requires a real server. Testcontainers makes the suite hermetic: no running stack, nothing shared between runs, and no path to a real database. Cost: a Docker daemon is required. This is the repo's first test infrastructure (`TESTING.md:3`) | y |
| Test isolation | Session fixture drops and recreates the test database, then applies migrations from zero; each test runs in a transaction that is rolled back | Migration tests are DDL-heavy and cannot be isolated by transaction rollback alone; a from-scratch database per session makes every run deterministic and also exercises AC DBM-01 for free. Per-test rollback then keeps DML from leaking between tests | y |
| Guard on the escape hatch | `TEST_DATABASE_URL` is rejected unless the database name ends in `_test` | The suite drops and recreates its database. With testcontainers the target is inherently disposable, but the override can point anywhere — including a dev or production server | y |
| Status value casing | Kebab-case (`auto-approved`) | Matches the GET filter values at `SCOPE.md:156` verbatim, so no mapping layer is needed between query string and column |  y |
| Status/enum representation | `TEXT` + `CHECK` constraint, not native PG `ENUM` | Adding a value later is a one-line migration; `ALTER TYPE ... ADD VALUE` is effectively irreversible | y |
| Status enum membership | The canonical five from `SCOPE.md:72-76` plus `pending` | `SCOPE.md:156` lists `human-approved` twice and omits `human-rejected` (gap #10) — the field list is authoritative over the filter list | y |
| UUID generation | `DEFAULT uuidv7()` on both tables | Verified native in PG 18.4 (no extension). v7 is time-ordered, so index inserts stay sequential instead of scattering B-tree pages the way random v4 does — material for an append-heavy audit table. The default applies only when the column is omitted, so a service may still supply its own UUID when it needs to know the value before insert | y |
| String columns | `TEXT` everywhere, bounded by explicit `CHECK (length(col) <= n)` where a bound is meaningful — never `VARCHAR(n)` | Verified on PG 18.4: `TEXT`, `VARCHAR`, and `VARCHAR(254)` store an identical 22 bytes for the same value — `VARCHAR(n)` is a constraint, not an optimization. A `CHECK` can be added `NOT VALID` then validated without blocking, whereas shrinking a `VARCHAR(n)` rewrites the table. It also co-locates with the regex checks already on those columns | y |
| Length bounds | `request_id` ≤ 64; `submitted_by`/`reviewed_by` ≤ 254 (RFC 5321 max). Free-text columns (`decision_reason`, `human_review_notes`, `human_review.reason`) are **unbounded** | User-directed. Identifier and email columns have externally-defined maximum lengths, so a bound encodes a real fact. The free-text columns do not: `decision_reason` and `human_review_notes` hold LLM output already bounded by the model's token limit, and any bound on them would be arbitrary. `status` needs no bound — its `CHECK` enumerates the legal values | y |
| `human_review.reason` input bound | Enforced at the API layer, not the database | It is the one free-text column fed directly from a caller-controlled payload (`SCOPE.md:186`). Bounding untrusted input belongs in the PUT endpoint's Pydantic model; duplicating it as a `CHECK` would put the same rule in two places that can drift | y |
| `currency` type | `TEXT` + `CHECK (currency ~ '^[A-Z]{3}$')`, not `CHAR(3)` | `CHAR(n)` is blank-padded and appears on PostgreSQL's own "Don't Do This" list; it also fails to enforce the intent, accepting `'1x!'`. The regex enforces exactly three uppercase letters | y |
| Money type | `NUMERIC(14,2)` | Binary floats cannot represent decimal currency exactly; unacceptable in a financial domain | y |
| Timestamp type | `TIMESTAMPTZ` | Payload timestamps are UTC-offset (`submitted_at: "2026-04-10T09:15:00Z"`); naive timestamps would silently drop the offset | y |
| `receipts_date` type | `DATE`, not `TIMESTAMPTZ` | Receipts carry a calendar date (`DATE 09/04/2026`), and the 90-day rule is a day-granularity comparison | y |
| Original payload type | `JSONB` | Queryable and indexable; the agent must read arbitrary fields from payloads it could not parse deterministically | y |
| Unique constraint (gap #14) | `UNIQUE NULLS NOT DISTINCT (request_id, submitted_by)` | `SCOPE.md:91` says "together" — composite. `NULLS NOT DISTINCT` (PG 15+, so PG 18 is fine) is required because `submitted_by` is nullable and standard NULL semantics would let unlimited duplicate rows through | y |
| Required (`NOT NULL`) columns | `uuid`, `original_payload`, `request_id` only | Exactly the three listed at `SCOPE.md:95-99`. Everything the agent later extracts is nullable at insert time | y |
| Email validation | `CHECK` regex, deliberately permissive, allowing `NULL` on `reimbursement.submitted_by` | `SCOPE.md:94` and `:114` require DB-level validation. Strict RFC 5322 in a `CHECK` is unmaintainable and rejects valid addresses; this catches structural garbage. Must allow NULL since `submitted_by` is not a required field | y |
| `updated_at` maintenance | DB trigger, not application-set | The agent's staleness rule (`SCOPE.md:229`) reads `updated_at` to decide whether to skip a message; an application that forgets to set it causes silent reprocessing | y |
| `human_review_notes` on `reimbursement` | `TEXT NULL` | The LLM advisory side-note from `SCOPE.md:267`, distinct from `human_review.reason` which is the reviewer's own justification | y |
| `human_review.reason` nullability | `NOT NULL` | Stricter than `SCOPE.md`, which does not mark it required. Justified by `SCOPE.md:15` — a recorded human decision with no reason is an unauditable decision | y |
| `human_review` foreign key | `REFERENCES reimbursement(uuid) ON DELETE RESTRICT` | Append-only audit data must not disappear as a side effect of deleting a parent row | y |
| `human_review` mutability | Append-only; no `updated_at` column | `SCOPE.md:178` — a new review creates a new row rather than overwriting; `SCOPE.md:109` lists only `Created At` | y |
| Postgres schema namespace | `public` | "Two data schemas" in the request means two entity definitions (two tables), not two PostgreSQL namespaces | y |
| Down migrations | Written for every migration | yoyo supports rollback; a migration that cannot be reversed is unsafe to apply to a shared environment | y |

**Open questions:** none — every row above is confirmed.

---

## User Stories

### P1: Schema created by versioned migrations ⭐ MVP

**User Story**: As a service developer, I want the `reimbursement` and
`human_review` tables created by versioned migrations, so that every
environment converges on an identical, reviewable schema instead of
hand-applied SQL.

**Why P1**: Nothing can be persisted until the tables exist. Every other
feature in `SCOPE.md` depends on this.

**Acceptance Criteria**:

1. WHEN migrations are applied to an empty `reimbursementanalyzer` database THEN the
   system SHALL create tables `reimbursement` and `human_review` with the
   columns and types defined in the Schema Definition section below.
2. WHEN migrations are applied a second time against an already-migrated
   database THEN the system SHALL apply zero migrations and exit successfully.
3. WHEN migrations are applied THEN the system SHALL record each applied
   version in a yoyo-managed ledger table.
4. WHEN a migration is rolled back THEN the system SHALL drop the objects that
   migration created, leaving the database at the prior version.
5. WHEN two migration processes start concurrently against the same database
   THEN the system SHALL serialize them via `backend.lock()` and apply each
   migration exactly once.

**Independent Test**: Apply migrations against a throwaway PG 18 instance,
assert both tables exist with expected columns; re-run, assert zero applied;
roll back, assert tables gone.

---

### P1: Constraints enforced by the database ⭐ MVP

**User Story**: As an auditor, I want every rule in `SCOPE.md:89-94` and
`SCOPE.md:111-114` enforced by PostgreSQL, so that no application bug can
write a row that violates the reimbursement policy contract.

**Why P1**: `SCOPE.md:19-21` makes this mission-critical and financial. A
constraint enforced only in application code is a constraint that will
eventually be bypassed by a retry path, a migration script, or a second
service.

**Acceptance Criteria**:

1. WHEN a row is inserted into `reimbursement` with a `status` outside
   {`pending`, `auto-approved`, `auto-rejected`, `human-review`,
   `human-approved`, `human-rejected`} THEN the database SHALL reject it with
   a check-constraint violation.
2. WHEN a row is inserted into `human_review` with a `status` outside
   {`approved`, `rejected`} THEN the database SHALL reject it with a
   check-constraint violation.
3. WHEN a second `reimbursement` row is inserted with the same
   (`request_id`, `submitted_by`) pair as an existing row THEN the database
   SHALL reject it with a unique-violation.
4. WHEN a second `reimbursement` row is inserted with the same `request_id`
   and `submitted_by IS NULL`, and an existing row also has `submitted_by IS
   NULL` THEN the database SHALL reject it with a unique-violation
   (`NULLS NOT DISTINCT`).
5. WHEN a row is inserted with a structurally invalid `submitted_by` or
   `reviewed_by` (no `@`, no dot in the domain, or embedded whitespace) THEN
   the database SHALL reject it with a check-constraint violation.
6. WHEN a `reimbursement` row is inserted with `submitted_by` omitted THEN the
   database SHALL accept it, since only `uuid`, `original_payload`, and
   `request_id` are required (`SCOPE.md:95-99`).
7. WHEN a `human_review` row references a `reimbursement_uuid` that does not
   exist THEN the database SHALL reject it with a foreign-key violation.
8. WHEN a `reimbursement` row is deleted while `human_review` rows reference
   it THEN the database SHALL reject the delete (`ON DELETE RESTRICT`).
9. WHEN a `reimbursement` row is inserted without a `status` THEN the database
   SHALL default it to `pending`.
10. WHEN a row is inserted into either table without a `uuid` THEN the database
    SHALL generate a time-ordered UUIDv7, and two rows inserted in sequence
    SHALL have ascending `uuid` values.
11. WHEN a row is inserted with a `currency` that is not exactly three
    uppercase letters THEN the database SHALL reject it with a
    check-constraint violation.
12. WHEN a row is inserted with `request_id` longer than 64 characters or an
    email column longer than 254 THEN the database SHALL reject it with a
    check-constraint violation.
13. WHEN a row is inserted with a long `decision_reason`,
    `human_review_notes`, or `human_review.reason` THEN the database SHALL
    accept it — these columns are deliberately unbounded.

**Independent Test**: For each criterion, execute the offending statement
against a migrated database and assert the specific PostgreSQL error class is
raised.

---

### P1: Migrations applied at bootstrap before services start ⭐ MVP

**User Story**: As an operator, I want the schema created automatically when
the stack boots, so that `docker compose up` yields a working system without a
manual migration step.

**Why P1**: `SCOPE.md:55` states DB structure must be created/updated on
bootstrap. Without this, every service starts against an empty database.

**Acceptance Criteria**:

1. WHEN `docker compose up` runs against empty volumes THEN the system SHALL
   run a one-shot `migrate` service that applies all migrations and exits 0.
2. WHEN the `migrate` service has not completed successfully THEN `api`,
   `agent`, and `publisher` SHALL NOT start
   (`depends_on: {condition: service_completed_successfully}`).
3. WHEN the `migrate` service starts before PostgreSQL is accepting
   connections THEN it SHALL wait for the existing `postgres` healthcheck
   before running (`condition: service_healthy`).
4. WHEN migrations fail THEN the `migrate` service SHALL exit non-zero and the
   dependent services SHALL NOT start.
5. WHEN the stack is restarted against existing volumes THEN the `migrate`
   service SHALL apply zero migrations and exit 0.

**Independent Test**: `docker compose down -v && docker compose up -d`, assert
`migrate` exits 0, assert both tables exist in the `reimbursementanalyzer` database,
assert the three services reach running state.

---

### P2: Decision reconstructable from stored data

**User Story**: As an auditor, I want the inputs and justification of every
decision persisted on the row, so that a decision can be reconstructed months
later without replaying Kafka or trusting an external tracing system.

**Why P2**: Not required to make the tables exist, but `SCOPE.md:15` and
`SCOPE.md:21` make it a stated requirement, and retrofitting columns after
rows exist is strictly more expensive than including them now.

**Acceptance Criteria**:

1. WHEN a decision is recorded THEN the schema SHALL provide a `receipts_date`
   column holding the date the 90-day rule was evaluated against.
2. WHEN a decision is recorded THEN the schema SHALL provide a
   `decision_reason` column holding the justification for that outcome.
3. WHEN a `reimbursement` row is inserted without `receipts_date` or
   `decision_reason` THEN the database SHALL accept it, since both are
   populated by the agent after the row is created by the publisher.

**Independent Test**: Insert an auto-rejected row with both columns populated
and read them back unchanged; insert a publisher-shaped row omitting both and
assert it succeeds.

---

### P2: Query support for documented read paths

**User Story**: As an API developer, I want indexes matching the read patterns
in `SCOPE.md`, so that the documented endpoints do not degrade to sequential
scans.

**Why P2**: Correctness does not depend on it, but the indexes are cheap now
and the read paths are already specified.

**Acceptance Criteria**:

1. WHEN `GET /api/v1/reimbursement` filters by `status` (`SCOPE.md:154-158`)
   THEN an index on `reimbursement(status)` SHALL exist.
2. WHEN the API fetches the most recent review for a reimbursement
   (`SCOPE.md:159`) THEN an index on
   `human_review(reimbursement_uuid, created_at DESC)` SHALL exist.

**Independent Test**: Query `pg_indexes` after migration and assert both
indexes are present.

---

## Schema Definition

### `reimbursement`

| Column | Type | Null | Default | Notes |
| ------ | ---- | ---- | ------- | ----- |
| `uuid` | `UUID` | no | `uuidv7()` | Primary key; time-ordered |
| `created_at` | `TIMESTAMPTZ` | no | `now()` | |
| `currency` | `TEXT` | yes | — | `CHECK ~ '^[A-Z]{3}$'`; agent hardcodes `BRL` (`SCOPE.md:245`) |
| `decision_reason` | `TEXT` | yes | — | Gap #7; unbounded |
| `human_review_notes` | `TEXT` | yes | — | LLM advisory note (`SCOPE.md:267`); unbounded |
| `original_payload` | `JSONB` | no | — | Required (`SCOPE.md:97`) |
| `receipts_date` | `DATE` | yes | — | Gap #8 |
| `receipts_value` | `NUMERIC(14,2)` | yes | — | |
| `request_id` | `TEXT` | no | — | Required (`SCOPE.md:98`); `CHECK length <= 64` |
| `status` | `TEXT` | no | `'pending'` | `CHECK` over the six values |
| `submitted_at` | `TIMESTAMPTZ` | yes | — | |
| `submitted_by` | `TEXT` | yes | — | `CHECK` email-or-NULL, `length <= 254` |
| `updated_at` | `TIMESTAMPTZ` | no | `now()` | Maintained by trigger |

Constraints: `UNIQUE NULLS NOT DISTINCT (request_id, submitted_by)`;
`CHECK` on `status`; `CHECK` on `submitted_by`. Index on `status`.

### `human_review`

| Column | Type | Null | Default | Notes |
| ------ | ---- | ---- | ------- | ----- |
| `uuid` | `UUID` | no | `uuidv7()` | Primary key; time-ordered |
| `created_at` | `TIMESTAMPTZ` | no | `now()` | |
| `reason` | `TEXT` | no | — | Reviewer's justification; unbounded (length owned by the PUT endpoint) |
| `reimbursement_uuid` | `UUID` | no | — | FK → `reimbursement(uuid)`, `ON DELETE RESTRICT` |
| `reviewed_by` | `TEXT` | no | — | `CHECK` email, `length <= 254` |
| `status` | `TEXT` | no | — | `CHECK IN ('approved','rejected')` |

Append-only — no `updated_at`. Index on `(reimbursement_uuid, created_at DESC)`.

---

## Edge Cases

- WHEN `submitted_by` is `NULL` on two rows sharing a `request_id` THEN the
  unique constraint SHALL still reject the second row (`NULLS NOT DISTINCT`).
- WHEN `receipts_value` is supplied with more than two decimal places THEN
  PostgreSQL SHALL round to scale 2 rather than error — accepted, since
  `NUMERIC(14,2)` is the currency contract.
- WHEN `original_payload` is a 25 MB document (`SCOPE.md:125`) THEN the insert
  SHALL succeed; `JSONB` is TOAST-backed and well within the 1 GB field limit.
- WHEN a migration is interrupted mid-apply THEN yoyo's per-migration
  transaction SHALL roll it back, leaving the ledger consistent.
- WHEN `updated_at` is explicitly set by an application THEN the trigger SHALL
  overwrite it with `now()`, so the agent's staleness comparison cannot be
  poisoned by a caller.
- WHEN the `migrate` service runs against a database already at the latest
  version THEN it SHALL exit 0 without acquiring a write lock on the tables.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| DBM-01 | P1: Schema created by versioned migrations | Design | Pending |
| DBM-02 | P1: Schema created by versioned migrations (idempotent re-run) | Design | Pending |
| DBM-03 | P1: Schema created by versioned migrations (rollback) | Design | Pending |
| DBM-04 | P1: Schema created by versioned migrations (concurrency lock) | Design | Pending |
| DBM-05 | P1: Constraints enforced by the database (status enums) | Design | Pending |
| DBM-06 | P1: Constraints enforced by the database (composite unique + NULLS NOT DISTINCT) | Design | Pending |
| DBM-07 | P1: Constraints enforced by the database (email checks) | Design | Pending |
| DBM-08 | P1: Constraints enforced by the database (FK + RESTRICT) | Design | Pending |
| DBM-09 | P1: Constraints enforced by the database (required vs nullable columns) | Design | Pending |
| DBM-10 | P1: Migrations applied at bootstrap (one-shot migrate service) | Design | Pending |
| DBM-11 | P1: Migrations applied at bootstrap (service gating) | Design | Pending |
| DBM-12 | P1: Migrations applied at bootstrap (failure blocks startup) | Design | Pending |
| DBM-13 | P2: Decision reconstructable from stored data | Design | Pending |
| DBM-14 | P2: Query support for documented read paths | Design | Pending |
| DBM-15 | P1: `updated_at` trigger integrity | Design | Pending |

**Coverage:** 15 total, 0 mapped to tasks, 15 unmapped ⚠️ (Tasks phase pending)

---

## Success Criteria

- [ ] `docker compose down -v && docker compose up -d` yields both tables with
      every documented constraint, with no manual step.
- [ ] Every `CHECK`, `UNIQUE`, and `FOREIGN KEY` in the Schema Definition has a
      pytest case that proves it rejects the violating row.
- [ ] `uv run pytest` passes with no stack running — the suite provisions its
      own PostgreSQL and depends on nothing external but a Docker daemon.
- [ ] The test and migration commands are documented as the repo's first gate
      check.
- [ ] Re-running migrations applies zero migrations and exits 0.
- [ ] Rolling back returns the database to a clean state.
- [ ] No column in `SCOPE.md`'s two schemas is unaccounted for — each is either
      present, or listed in Assumptions with the reason it was dropped.
