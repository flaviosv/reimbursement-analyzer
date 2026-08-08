# Database Schema & Migrations Design

**Spec**: `.specs/features/db-schema-migrations/spec.md`
**Status**: Draft

**Constraints loaded:** AD-001 … AD-006 (`.specs/STATE.md`). This design
conforms to all six; one refinement to AD-001 is noted under Tech Decisions.
Confirmed lessons store: empty.

---

## Architecture Overview

Migration SQL ships **inside** the installed `api` package and is applied by a
one-shot `migrate` container that runs before any service starts. The same
image, the same path, and the same command work in dev, in prod, and as a k3s
`Job` — nothing is bind-mount-dependent.

```mermaid
flowchart TD
    up["docker compose up"] --> pg[("postgres<br/>healthcheck")]
    pg -->|service_healthy| migrate["migrate<br/>one-shot container<br/>python -m api.migrate"]
    migrate --> lock["backend.lock()<br/>advisory lock"]
    lock --> sql["0001.create-reimbursement.sql<br/>0002.create-human-review.sql"]
    sql --> db[("reimbursementanalyzer<br/>schema applied")]
    migrate -->|service_completed_successfully| api["api"]
    migrate -->|service_completed_successfully| agent["agent"]
    migrate -->|service_completed_successfully| publisher["publisher"]
    pytest["pytest"] -.->|creates + migrates| testdb[("reimbursementanalyzer_test")]
```

---

## Code Reuse Analysis

### Existing Components to Leverage

| Component | Location | How to Use |
| --------- | -------- | ---------- |
| `api` Dockerfile `dev`/`prod` targets | `src/api/Dockerfile` | Reuse verbatim — the `migrate` service builds the same image with an overridden `command`. No Dockerfile change needed (see Tech Decisions). |
| `DATABASE_URL` convention | `docker-compose.yml:286, 308` | `postgresql://reimbursementanalyzer:${POSTGRES_PASSWORD}@postgres:5432/reimbursementanalyzer` — the `migrate` service and the `api` service reuse this exact string |
| `postgres` healthcheck | `docker-compose.yml:37-41` | `migrate` gates on `condition: service_healthy`; no new wait logic |
| Module entry-point pattern | `CONVENTIONS.md:28-29` | `main()` guarded by `if __name__ == "__main__"`, run as `python -m api.migrate` |
| Env-var config pattern | `CONVENTIONS.md:30-31` | `os.environ` with local default — no settings framework |
| uv workspace + single lockfile | `pyproject.toml`, `uv.lock` | New deps go in `src/api/pyproject.toml`; dev group at the root |

### Integration Points

| System | Integration Method |
| ------ | ------------------ |
| PostgreSQL 18 (`postgres` service) | yoyo over psycopg 3; DDL only, at bootstrap |
| docker compose | New `migrate` service + `depends_on` edges on the three existing services |
| Future publisher/agent/API persistence | Consumes the schema; no code coupling — services keep using asyncpg |

---

## Components

### Migration SQL

- **Purpose**: Declare the two tables, their constraints, and their indexes.
- **Location**: `src/api/src/api/migrations/`
- **Files**:
  - `0001.create-reimbursement.sql` + `.rollback.sql`
  - `0002.create-human-review.sql` + `.rollback.sql` (header: `-- depends: 0001.create-reimbursement`)
- **Dependencies**: PostgreSQL 15+ for `UNIQUE NULLS NOT DISTINCT` (PG 18 in use)
- **Reuses**: nothing — greenfield

`0001` also creates the shared `set_updated_at()` trigger function and attaches
it to `reimbursement`. `human_review` is append-only and has no `updated_at`,
so no trigger.

### Migration runner

- **Purpose**: Apply outstanding migrations exactly once, under a lock.
- **Location**: `src/api/src/api/migrate.py`
- **Interfaces**:
  - `migrations_path() -> str` — resolves the packaged SQL directory via `importlib.resources`
  - `backend_url(database_url: str) -> str` — adapts the shared `postgresql://` DSN to yoyo's psycopg-3 scheme
  - `main() -> None` — acquires `backend.lock()`, applies `backend.to_apply(...)`
- **Dependencies**: `yoyo-migrations>=9.0.0`, `psycopg[binary]>=3.3.4`, `DATABASE_URL`
- **Reuses**: the `python -m <pkg>.<module>` entry-point convention

```python
MIGRATIONS = str(importlib.resources.files("api") / "migrations")

def main() -> None:
    backend = get_backend(backend_url(os.environ["DATABASE_URL"]))
    migrations = read_migrations(MIGRATIONS)
    with backend.lock():
        backend.apply_migrations(backend.to_apply(migrations))
```

### `migrate` compose service

- **Purpose**: Run the runner once at bootstrap, gate the three services on it.
- **Location**: `docker-compose.yml`
- **Interfaces**: `command: ["python", "-m", "api.migrate"]`; `restart: "no"`
- **Dependencies**: `postgres` (`service_healthy`)
- **Reuses**: `src/api/Dockerfile` `dev` target, the `DATABASE_URL` string, the postgres healthcheck

`api`, `agent`, and `publisher` each gain
`migrate: {condition: service_completed_successfully}`.

### Test fixtures

- **Purpose**: Provide a migrated, disposable database backed by a throwaway
  PostgreSQL container.
- **Location**: `src/api/tests/conftest.py`, `src/api/tests/helpers.py`
- **Interfaces**:
  - `server_url` (session) — starts a `postgres:18` testcontainer and yields its DSN; `TEST_DATABASE_URL` overrides it
  - `migrated_db` (session) — drops and recreates the target database on that server, applies migrations, yields its DSN
  - `conn` (function) — a psycopg connection that is rolled back after each test
- **Dependencies**: `pytest>=9.1.1`, `testcontainers[postgres]>=4.15.0`, `psycopg[binary]`, a Docker daemon
- **Reuses**: the migration runner itself — the fixture calls the same code path AC DBM-01 describes, so setup doubles as coverage

**Safety guard:** `TEST_DATABASE_URL` is rejected unless the database name ends
in `_test`. The container path needs no guard (it is disposable by
construction), but the override can point at any server.

**Import path:** `testcontainers.community.postgres`, not
`testcontainers.postgres` — 4.15.0 deprecates the latter.

### Constraint tests

- **Purpose**: Prove each `CHECK` / `UNIQUE` / `FK` rejects its violating row.
- **Location**: `src/api/tests/test_migrations.py`
- **Dependencies**: the fixtures above
- **Coverage**: one test per acceptance criterion in spec stories P1-2 and P2,
  asserting the specific PostgreSQL error class rather than a bare exception

---

## Data Models

Authoritative column list lives in the spec's Schema Definition. DDL shape:

```sql
CREATE TABLE reimbursement (
    uuid                UUID PRIMARY KEY DEFAULT uuidv7(),
    request_id          TEXT NOT NULL,
    submitted_by        TEXT,
    submitted_at        TIMESTAMPTZ,
    original_payload    JSONB NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    receipts_value      NUMERIC(14,2),
    receipts_date       DATE,
    currency            TEXT,
    decision_reason     TEXT,
    human_review_notes  TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT reimbursement_status_check CHECK (status IN (
        'pending', 'auto-approved', 'auto-rejected',
        'human-review', 'human-approved', 'human-rejected')),
    CONSTRAINT reimbursement_submitted_by_check CHECK (
        submitted_by IS NULL
        OR (submitted_by ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'
            AND length(submitted_by) <= 254)),
    CONSTRAINT reimbursement_currency_check CHECK (
        currency IS NULL OR currency ~ '^[A-Z]{3}$'),
    CONSTRAINT reimbursement_request_id_length_check
        CHECK (length(request_id) <= 64),
    CONSTRAINT reimbursement_request_submitter_key
        UNIQUE NULLS NOT DISTINCT (request_id, submitted_by)
);

CREATE TABLE human_review (
    uuid                UUID PRIMARY KEY DEFAULT uuidv7(),
    reimbursement_uuid  UUID NOT NULL
        REFERENCES reimbursement(uuid) ON DELETE RESTRICT,
    status              TEXT NOT NULL,
    reviewed_by         TEXT NOT NULL,
    reason              TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT human_review_status_check
        CHECK (status IN ('approved', 'rejected')),
    CONSTRAINT human_review_reviewed_by_check CHECK (
        reviewed_by ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'
        AND length(reviewed_by) <= 254)
);
```

`uuidv7()` is a PostgreSQL 18 core function — verified against the running
18.4 instance, no extension required. It is time-ordered, so primary-key
inserts append to the right edge of the B-tree instead of scattering pages the
way random v4 values do. The `DEFAULT` applies only when the column is
omitted, so the publisher may still generate its own UUID when it needs the
value before the insert (for tracing or retry correlation).

**Relationships**: `human_review.reimbursement_uuid` → `reimbursement.uuid`,
many-to-one, append-only. Indexes: `reimbursement(status)` and
`human_review(reimbursement_uuid, created_at DESC)`.

---

## Error Handling Strategy

| Error Scenario | Handling | Operator Impact |
| -------------- | -------- | --------------- |
| PostgreSQL unreachable at migrate time | `depends_on: postgres condition: service_healthy` prevents the case; a connection error still exits non-zero | Dependent services never start; compose reports the failed dependency |
| A migration raises mid-apply | yoyo wraps each migration in a transaction and rolls it back; the ledger stays consistent | Exit non-zero, no partial schema |
| Two `migrate` containers start at once | `backend.lock()` takes a PostgreSQL advisory lock; the second waits, then finds nothing to apply | Both exit 0, each migration applied once |
| `DATABASE_URL` unset | `os.environ[...]` raises `KeyError` immediately | Fails loudly at startup rather than connecting somewhere unintended |
| Tests pointed at a non-`_test` database | Fixture raises before any DDL runs | Dev data cannot be dropped by a test run |
| Migrations already at latest | `to_apply()` returns empty; no table locks taken | Exit 0, restart is cheap |

---

## Risks & Concerns

| Concern | Location | Impact | Mitigation |
| ------- | -------- | ------ | ---------- |
| Migrations in `src/api/migrations/` (literal AD-001 path) would be absent from the `prod` image — it copies only `/app/.venv`, never source | `src/api/Dockerfile:52-57` | Migrations unrunnable in prod / k3s; discovered only at deploy time | Place SQL inside the package (`src/api/src/api/migrations/`) so it ships in the wheel. Refinement to AD-001, recorded below |
| `migrate` inherits `restart: always` if copied from a sibling service | `docker-compose.yml:260, 278, 300` | A one-shot container would loop forever and never report completion | Explicit `restart: "no"`; verified by AC DBM-10 |
| `docker-entrypoint-initdb.d` scripts run only on a *fresh* volume | `docker-compose.yml:314` (`postgres_data`) | A test DB seeded that way would silently not exist on existing volumes | Not used. The pytest fixture creates the database itself, so it works against both fresh and existing volumes |
| Scheme rewrite `postgresql://` → `postgresql+psycopg://` is string surgery on a shared env var | `src/api/src/api/migrate.py` | A future DSN format change breaks migrations silently | Isolated in `backend_url()`, covered by a unit test that needs no database |
| The serving `api` image will carry yoyo + a DDL-capable driver | `src/api/pyproject.toml` | Larger image; migration tooling present in a request-serving container | Accepted for this scope — same credentials either way. A dedicated `migrate` build target is the follow-up if the images are ever split |
| `docs/SCOPE.md:67-114` no longer matches the schema (AD-005) | `docs/SCOPE.md` | Next reader implements against the stale contract | Out of scope here; flagged for a follow-up `SCOPE.md` edit. Tracked in AD-005 |
| Zero pre-existing tests, so no fixture/runner conventions to follow | `TESTING.md:3` | This feature sets the pattern every later test inherits | Keep the fixture surface minimal (two fixtures); document the command in `TESTING.md` as part of the work |
| `human_review.reason` is unbounded but fed directly from a caller-controlled PUT payload | `SCOPE.md:186` | An oversized `reason` lands in the audit trail unchecked; PUT has no stated body-size limit the way POST does (`SCOPE.md:125`) | Deliberate — the bound belongs in the PUT endpoint's Pydantic model, not duplicated as a `CHECK`. Carried as a required constraint on the endpoint feature so it is not lost |
| `psycopg[binary]` is discouraged by its maintainers for production | `src/api/pyproject.toml` | Bundled libpq/libssl can conflict with other native extensions | Acceptable: the alternative (`psycopg2` source build) needs a compiler in a slim image, and this process does DDL at boot, not request serving |

---

## Tech Decisions

| Decision | Choice | Rationale |
| -------- | ------ | --------- |
| Migration file location | `src/api/src/api/migrations/` (inside the package), not `src/api/migrations/` | The `prod` target copies only the venv, so anything outside the installed package is missing there. uv's build backend includes non-Python files under the module root by default (only `__pycache__`/`*.pyc`/`*.pyo` are excluded), so the SQL ships in the wheel. One path works in dev, prod, and a k3s Job — no Dockerfile edit |
| Migration granularity | One migration per table, each with a rollback file | Matches the two entities; either can be reverted independently. yoyo convention: `<name>.sql` + `<name>.rollback.sql`, ordering via `-- depends:` |
| PostgreSQL driver for yoyo | `psycopg[binary]>=3.3.4` with the `postgresql+psycopg://` scheme | yoyo's `[postgres]` extra installs psycopg2, which is maintenance-mode and needs a compiler in `python:3.14.7-slim`. psycopg 3 has cp314 wheels (verified on PyPI today) and yoyo documents the `postgresql+psycopg://` scheme |
| Test database provisioning | A `postgres:18` testcontainer per session, not the compose `postgres` service and not an init script | Init scripts run only on a fresh volume, so on an existing one the database would silently never appear. Reusing the compose service couples the suite to a running stack and to whatever state that database is already in. A container is hermetic and disposable by construction |
| Test isolation | Drop + recreate the database per session, migrate from zero, roll back per test | DDL cannot be isolated by transaction rollback alone; from-scratch also exercises DBM-01 for free, and per-test rollback keeps DML from leaking |
| Testcontainers image tag | `postgres:18`, matching `docker-compose.yml` | `uuidv7()` and `UNIQUE NULLS NOT DISTINCT` are version-sensitive; testing on a different major would prove nothing about what production runs. Asserted at runtime by a `server_version_num` test |
| Where pytest is declared | Root `pyproject.toml` `[dependency-groups] dev`, tests in `src/api/tests/` | `uv run pytest` works from the repo root across the workspace; tests sit outside the package so they never ship in the wheel |

> **Project-level decision:** the migration-path refinement is recorded as
> **AD-007** in `.specs/STATE.md`, superseding the path detail of AD-001
> (ownership and execution model are unchanged).

---

## Version Verification

Checked against PyPI and the yoyo documentation on 2026-08-07:

| Package | Version | Note |
| ------- | ------- | ---- |
| psycopg[binary] | 3.3.4 | cp314 wheels present |
| pytest | 9.1.1 | requires-python >=3.10 |
| yoyo-migrations | 9.0.0 | `[postgres]` extra pulls psycopg2; not used here |

`postgresql+psycopg://` scheme, `-- depends:` header, `<name>.rollback.sql`
convention, and `backend.lock()` all confirmed from yoyo's official docs via
Context7.

### DDL executed against PostgreSQL 18.4

The full DDL above was run on the project's own `postgres` container in a
throwaway schema. Results:

| Check | Result |
| ----- | ------ |
| Both tables create cleanly | pass |
| Publisher-shaped insert (only `request_id` + `original_payload`) | pass |
| `status` defaults to `pending` | pass |
| `uuidv7()` values ascend across sequential inserts | pass |
| Unknown `status` rejected | `reimbursement_status_check` |
| Duplicate `(request_id, NULL)` rejected | `reimbursement_request_submitter_key` — confirms `NULLS NOT DISTINCT`, which standard NULL semantics would have let through |
| Lowercase `currency` rejected | `reimbursement_currency_check` |
| Malformed `submitted_by` rejected | `reimbursement_submitted_by_check` |
| 65-char `request_id` rejected | `reimbursement_request_id_length_check` |
| Orphan `human_review` row rejected | `human_review_reimbursement_uuid_fkey` |

Storage comparison on the same instance — `TEXT`, `VARCHAR`, and
`VARCHAR(254)` each consumed 22 bytes for an identical 21-character value,
confirming that `VARCHAR(n)` buys a constraint rather than efficiency.
