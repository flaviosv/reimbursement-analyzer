# Publisher — Consume `Request`, Produce `Reimbursement` — Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and
follow its Execute flow and Critical Rules.** Do not search for skill files by
filesystem path. The skill is the source of truth for the full flow (per-task
cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user — do not proceed
without it.**

---

**Spec**: `.specs/features/publisher-consume-request/spec.md`
**Design**: `.specs/features/publisher-consume-request/design.md`
**Risks**: `.specs/RISKS.md` (R-001 … R-005)
**Status**: Complete — see `validation.md` (all 13 tasks, Verifier PASS)

---

## Test Coverage Matrix

> Generated from codebase, project guidelines, and spec — confirm before Execute.
> **Guidelines found:** `docs/codebase/TESTING.md`, `docs/codebase/CONVENTIONS.md`,
> `pyproject.toml` (`python_classes`/`python_functions`/`testpaths`/`markers`),
> `~/.claude/CLAUDE.md`. No coverage tool and no enforced threshold exist
> (`TESTING.md:37-39`), so **strong defaults apply** for depth.
>
> Style sampled from `src/api/tests/reimbursement/create/test_validation.py`,
> `test_producer.py`, `src/shared/tests/test_config.py`, `test_producer.py`,
> `src/api/tests/conftest.py`, `helpers.py`: `Describe*` classes holding `it_*`
> methods, full type hints, module-level fixtures, async tests carrying an
> explicit `pytestmark = pytest.mark.anyio` (anyio is **not** auto-mode).

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------ | -------------------- | ---------------- | ----------- |
| Wire models — pure schema (`RequestEnvelope`, `ReimbursementEnvelope`) | none | Build gate only — no `test_models.py` exists today; shape is exercised where consumed | — | build |
| Model behaviour (`AttemptError.next`, defaults, back-compat parse) | unit | All branches; the no-`errors`-field back-compat case has its own test | `src/shared/tests/test_models.py` | quick |
| Config — dataclasses + `load_config()` | unit | Every new field; the pool/concurrency refuse-to-boot invariant (R-005) | `src/shared/tests/test_config.py` | quick |
| Shared error helpers (`sanitize`) | unit | All branches; PII exclusion asserted on a real driver-shaped error | `src/shared/tests/test_errors.py` | quick |
| Failure log | unit | Never-raises, truncation, record shape — via `caplog` | `src/shared/tests/test_failure_log.py` | quick |
| Repository / data access | integration (real Postgres) | Key query paths + duplicate detection + error handling | `src/shared/tests/reimbursement/test_repository.py` | quick¹ |
| Shared use cases | integration + unit | 1:1 to spec ACs — row shape, rendered history, empty-history case | `src/shared/tests/reimbursement/use_cases/test_send_human_review.py` | quick¹ |
| Publisher business logic (`processing.py`) | unit (fakes) | All branches; 1:1 to spec ACs; every listed edge case has a test | `src/publisher/tests/test_processing.py` | quick |
| Publisher consumer config + loop | unit (fake consumer) | Config invariants + offset/loop semantics + shutdown | `src/publisher/tests/test_consumer.py` | quick |
| API envelope wrapper (modified) | unit | Existing envelope-byte assertions updated, not weakened | `src/api/tests/reimbursement/create/test_producer.py` | quick |
| End-to-end round trip | integration (`@pytest.mark.integration`) | Real Postgres + real Kafka: row committed, message consumed, offset advanced | `src/publisher/tests/test_integration.py` | full |
| Packaging — `pyproject.toml`, `Dockerfile`, layout | none | Build gate only | — | build |

¹ **Postgres-backed tests run in the Quick gate.** The `integration` marker
means "requires a container *beyond* the suite's own Postgres default"
(`pyproject.toml` marker text) — i.e. Kafka. Docker is therefore a
prerequisite for Quick as well as Full; only Build runs without it.

## Gate Check Commands

> Generated from codebase — confirm before Execute.

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | After tasks with unit and/or Postgres-backed tests | `uv run pytest -q -m "not integration"` |
| Full | After tasks with Kafka-container tests | `uv run pytest -q` |
| Build | After packaging/config-only tasks | `uv sync --all-packages && docker compose config -q` |

**Baseline: 145 passing, 2 deselected** on `-m "not integration"` as of
`1ea1eba`. Every task states its expected new total so a silent deletion fails
the gate.

---

## Execution Plan

Phases are ordered and run sequentially — each phase completes before the next
begins, and tasks within a phase execute in order.

### Phase 1: Wire contract

The envelope change that everything downstream reads. Amends shipped code.

```
T1 → T2
```

### Phase 2: Shared kernel primitives

Config, error helpers, and the last-resort log — no domain knowledge yet.

```
T3 → T4 → T5
```

### Phase 3: Shared reimbursement slice

First real database access in the project. T6 unblocks Postgres-backed tests
for `shared`.

```
T6 → T7 → T8
```

### Phase 4: The publisher service

```
T9 → T10 → T11 → T12
```

### Phase 5: End-to-end proof

```
T13
```

---

## Task Breakdown

### T1: Add attempt-error history to the wire models

**What**: `AttemptError` model, `RequestEnvelope.errors`, and the new
`ReimbursementEnvelope`.
**Where**: `src/shared/src/shared/models.py` (modify),
`src/shared/tests/test_models.py` (new)
**Depends on**: None
**Reuses**: `RequestEnvelope`/`ReimbursementRequest` at `models.py:31,49` — same
file, same conventions
**Requirement**: AD-014, AD-015 — shape for PUB-04, PUB-05, PUB-11, PUB-12

**Tools**:

- MCP: `context7` (Pydantic v2 `Literal` field + `@classmethod` constructor surface)
- Skill: NONE

**Done when**:

- [x] `AttemptError` has `attempt: int`, `occurred_at: AwareDatetime`,
      `stage: Literal["db-insert", "publish"]`, `error_type: str`, `message: str`
- [x] `AttemptError.next(errors, stage, exc)` returns the next entry with
      `attempt == len(errors) + 1` and an aware UTC `occurred_at` —
      renamed to `from_exception(attempt, stage, exc, occurred_at=None)`
      in the PR #5 comment-triage pass (Q11/A14); behaviour unchanged
- [x] `RequestEnvelope.errors: list[AttemptError] = []`
- [x] `ReimbursementEnvelope` has `uuid: UUID`, `retry: int`,
      `published_at: AwareDatetime`, `errors: list[AttemptError] = []` — and
      **no payload field** (AD-015)
- [x] A test parses an envelope JSON **without** an `errors` key and asserts it
      yields `[]` — the back-compat guarantee for messages already on the topic
- [x] A test asserts `AttemptError.next` appends rather than replaces (attempt
      numbers 1→2→3 across successive calls)
- [x] Gate check passes: `uv run pytest -q -m "not integration"`
- [x] Test count: 145 + ≥6 new, all passing (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(shared): carry attempt-error history on the message envelopes`

---

### T2: Emit an empty error history from the API envelope

**What**: Add `"errors":[],` to `build_envelope`'s spliced prefix and update the
envelope-byte assertions that cover it.
**Where**: `src/api/src/reimbursement/create/producer.py` (modify),
`src/api/tests/reimbursement/create/test_producer.py` (modify),
`src/api/tests/reimbursement/create/test_integration.py` (modify)
**Depends on**: T1
**Reuses**: `build_envelope` at `producer.py:12-20`
**Requirement**: AD-014 — makes PUB-11/PUB-12 reachable end to end

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [x] Prefix is `{"retry":0,"published_at":"…","errors":[],"payload":` — the
      byte-splice approach is **unchanged**, only the literal grows
- [x] The payload half stays byte-for-byte identical to the request body — the
      existing assertion is updated for the new prefix, **never weakened or deleted**
- [x] A test round-trips the result through `RequestEnvelope.model_validate_json`
      and asserts `errors == []`
- [x] `test_integration.py`'s broker round-trip still asserts byte-identical
      delivery
- [x] Gate check passes: `uv run pytest -q` (this task's coverage includes the
      Kafka-container test)
- [x] Test count: T1's total + ≥1 new, all passing (no silent deletions)

**Tests**: unit + integration
**Gate**: full

**Commit**: `feat(api): carry an empty error history on published envelopes`

---

### T3: Add database, failure-log and publisher config

**What**: Three frozen dataclasses, two constants, and their assembly inside the
existing cached `load_config()`.
**Where**: `src/shared/src/shared/config.py` (modify),
`src/shared/tests/test_config.py` (modify)
**Depends on**: None
**Reuses**: `KafkaConfig` + `load_config()` at `config.py:17-72` — same shape,
same `to_*_config()` idiom
**Requirement**: PUB-34, PUB-36, PUB-41, R-005

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [x] `REIMBURSEMENT_TOPIC = "Reimbursement"` and `MAX_RETRY = 3` sit with the
      other wire constants (module level, not in a dataclass)
- [x] `DatabaseConfig(dsn: str | None, pool_min_size, pool_max_size)` —
      **`os.getenv`, never `os.environ[...]`**; a test asserts `load_config()`
      succeeds with `DATABASE_URL` unset (api must still boot without it)
- [x] `FailureLogConfig(logger_name, max_message_chars)`
- [x] `PublisherConfig(consumer_group_id, item_concurrency=10, consume_timeout_seconds)`
- [x] `PublisherConfig.to_consumer_config(kafka)` sets
      `enable.auto.commit=False`, `auto.offset.reset="earliest"`, `group.id`,
      and both fetch sizes from `KAFKA_MAX_MESSAGE_BYTES`; SASL/TLS branches are
      **reused from `KafkaConfig`, not re-derived**
- [x] A test asserts `enable.auto.commit` is `False` — not merely present
- [x] A test asserts both fetch limits equal `KAFKA_MAX_MESSAGE_BYTES` (PUB-34)
- [x] No dataclass gains a `from_env()` — `load_config()` stays the only
      env-reading site (AD-023)
- [x] Gate check passes: `uv run pytest -q -m "not integration"`
- [x] Test count: T2's total + ≥8 new, all passing (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(shared): add database, failure-log and publisher config`

---

### T4: Add a PII-safe sanitiser

**Superseded (Q20/Q24, 2026-08-08):** this task originally planned a
`DuplicateRequest` exception type alongside `sanitize(exc)`. It was built,
then removed in the Verifier's round-1 fix pass as dead code —
`repository.is_duplicate(exc)` classifies the driver's own
`UniqueViolationError` by `constraint_name` directly; nothing ever needed a
purpose-built exception type to carry that classification (see
`validation.md`'s round-1 gap #9). The checklist below is edited to match
what actually shipped, rather than left describing a class that no longer
exists.

**What**: `sanitize(exc)`, which renders an exception for stdout without
leaking driver-supplied values.
**Where**: `src/shared/src/shared/errors.py` (modify),
`src/shared/tests/test_errors.py` (new)
**Depends on**: None
**Reuses**: the existing exception classes at `errors.py:1-12`
**Requirement**: PUB-15, PUB-17

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [x] ~~`DuplicateRequest` defined alongside the existing exceptions~~ —
      not built; see the superseded note above
- [x] `sanitize(exc)` returns the exception class name plus, when present, the
      constraint name — and **nothing else**
- [x] A test builds an exception carrying a Postgres-shaped `detail`
      (`Key (request_id, lower(submitted_by))=(REQ-1, ana@company.com) already
      exists`) and asserts the email is **absent** from `sanitize`'s output (PUB-15)
- [x] A test asserts `sanitize` never raises on an exception with no
      `constraint_name`/`detail` attributes
- [x] Gate check passes: `uv run pytest -q -m "not integration"`
- [x] Test count: T3's total + ≥4 new, all passing (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(shared): add pii-safe sanitiser`

---

### T5: Add the last-resort failure log

**What**: A structured JSON record emitted at critical level to a dedicated named
logger — the bottom of every fallback chain.
**Where**: `src/shared/src/shared/failure_log.py` (new),
`src/shared/tests/test_failure_log.py` (new)
**Depends on**: T3
**Reuses**: `FailureLogConfig` from T3; stdlib `logging` as used in
`api/errors.py:10`
**Requirement**: PUB-16, PUB-27, PUB-29, PUB-30

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [x] `write(config, record: dict) -> None` — **synchronous**; emits one JSON
      object to `logging.getLogger(config.logger_name)` at critical level
- [x] Writes no file and opens no path — durability is the log handler's job
      (design: "Why a logger, not a file")
- [x] **Never raises**: a test forces the underlying logger to throw and asserts
      `write` returns normally
- [x] Long values are truncated to `max_message_chars`; a test asserts the cap
- [x] A test asserts the emitted record is valid JSON and carries the fields the
      design names (`request_id`, `stage`, `errors`, outcome)
- [x] Gate check passes: `uv run pytest -q -m "not integration"`
- [x] Test count: T4's total + ≥5 new, all passing (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(shared): add last-resort failure log`

---

### T6: Promote the Postgres fixtures to a workspace-level conftest

**What**: Move `server_url` / `disposable_database` out of `src/api/tests/` so
`shared` tests share **one** container instead of starting a second.
**Where**: `conftest.py` (new, repo root), `src/api/tests/conftest.py` (modify),
`src/shared/tests/conftest.py` (modify)
**Depends on**: None
**Reuses**: `src/api/tests/conftest.py:28-70` verbatim — this is a move, not a
rewrite; `helpers.py` stays where it is
**Requirement**: Test infrastructure enabling PUB-03, PUB-17, PUB-21, PUB-23

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [x] `server_url` is session-scoped and defined **once** for the whole
      workspace; a second container is never started
- [x] **Verify, do not assume:** the root `conftest.py` can import `helpers` and
      `migrate`, which resolve only through `pythonpath`. If `pythonpath` is
      applied too late for a rootdir conftest, fall back to keeping the fixture
      in `src/api/tests/conftest.py` and having `src/shared/tests/conftest.py`
      import it — and **record the deviation in the commit body**
- [x] The `_clear_config_cache` autouse fixture is not duplicated into two
      conftests that both apply
- [x] Every existing Postgres-backed api test still passes, unchanged
- [x] A shared-side test proves the fixture is reachable from
      `src/shared/tests/` (connects and runs `SELECT 1`)
- [x] Gate check passes: `uv run pytest -q -m "not integration"`
- [x] Test count: T5's total + ≥1 new, **zero lost** — this task's whole risk is
      silently dropping api fixtures

**Tests**: integration (real Postgres)
**Gate**: quick

**Commit**: `test: promote postgres fixtures to a workspace-level conftest`

---

### T7: Add the reimbursement repository

**What**: The project's first async database access — pool lifecycle, the two
inserts, and duplicate detection.
**Where**: `src/shared/src/shared/reimbursement/__init__.py` (new),
`src/shared/src/shared/reimbursement/repository.py` (new),
`src/shared/pyproject.toml` (modify),
`src/shared/tests/reimbursement/test_repository.py` (new)
**Depends on**: T3, T4, T6
**Reuses**: `shared/producer.py`'s `@asynccontextmanager` lifecycle shape —
`managed_pool` mirrors `managed_producer`; migration schema at
`src/api/src/migrations/0001.create-reimbursement.sql`
**Requirement**: PUB-03, PUB-17, PUB-21

**Tools**:

- MCP: `context7` (asyncpg `create_pool` / `Connection.transaction` / exception surface)
- Skill: NONE

**Done when**:

- [x] `asyncpg` added to `src/shared/pyproject.toml`; `uv sync --all-packages` resolves
- [x] `managed_pool(config)` sets `min_size`/`max_size` **explicitly** — a test
      asserts they are not asyncpg's `10`/`10` default
- [x] `insert_pending(conn, item) -> UUID` populates `request_id`,
      `submitted_by`, `submitted_at`, `original_payload`; leaves `status` at its
      `pending` default (PUB-03)
- [x] `insert_human_review(conn, item, reason) -> UUID` sets
      `status='human-review'` and `decision_reason`
- [x] `is_duplicate(exc)` returns `True` **only** for a `UniqueViolationError`
      whose `constraint_name == "reimbursement_request_submitter_key"`
- [x] A test proves a primary-key violation is **not** classified as a duplicate
      — the misclassification the design calls out
- [x] A test inserts the same `(request_id, submitted_by)` twice against a real
      Postgres and asserts **no second row exists** (PUB-21), not merely that an
      exception was raised
- [x] A test proves case-varied `submitted_by` still collides (the index is on
      `lower(submitted_by)`)
- [x] Gate check passes: `uv run pytest -q -m "not integration"`
- [x] Test count: T6's total + ≥8 new, all passing (no silent deletions)

**Tests**: integration (real Postgres)
**Gate**: quick

**Commit**: `feat(shared): add reimbursement repository`

---

### T8: Add the send-human-review use case

**What**: The one shared action that preserves a request for a human, with the
full failure history rendered into `decision_reason`.
**Where**: `src/shared/src/shared/reimbursement/use_cases/__init__.py` (new),
`src/shared/src/shared/reimbursement/use_cases/send_human_review.py` (new),
`src/shared/tests/reimbursement/use_cases/test_send_human_review.py` (new)
**Depends on**: T1, T7
**Reuses**: `repository.insert_human_review` from T7; `AttemptError` from T1
**Requirement**: PUB-23, PUB-24, PUB-25

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [x] `send_human_review(conn, item, errors) -> UUID` inserts a
      `status='human-review'` row (PUB-23)
- [x] `render_history(errors)` renders **every** entry — a test with three
      distinct errors asserts all three messages **and** all three stage names
      appear (PUB-24). A count or summary fails this test
- [x] A test with four *identical* errors asserts four entries render, not one —
      a reviewer must be able to tell a permanent fault from a flapping one
- [x] With `errors == []`, `decision_reason` is non-null and states the ceiling
      was reached with no detail carried (PUB-25)
- [x] Each rendered message is truncated to `FailureLogConfig.max_message_chars`
- [x] Gate check passes: `uv run pytest -q -m "not integration"`
- [x] Test count: T7's total + ≥6 new, all passing (no silent deletions)

**Tests**: integration (real Postgres) + unit
**Gate**: quick

**Commit**: `feat(shared): add send-human-review use case`

---

### T9: Flatten `publisher` to `src/publisher/src`

**What**: Packaging only — flat layout, virtual member, Dockerfile and pytest
path updates. No behaviour change.
**Where**: `src/publisher/src/publisher/consumer.py` → `src/publisher/src/consumer.py`,
`src/publisher/pyproject.toml` (modify), `src/publisher/Dockerfile` (modify),
`pyproject.toml` (modify)
**Depends on**: None
**Reuses**: `src/api/pyproject.toml`'s `[tool.uv] package = false` block and
`src/api/Dockerfile`'s `ENV PYTHONPATH` pattern — AD-018 solved this exact
problem for `api`
**Requirement**: AD-026

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [x] `src/publisher/src/` holds loose modules; the `publisher/` package
      directory is gone
- [x] `[tool.uv] package = false`, `[build-system]` dropped — uv_build cannot
      wheel loose modules (AD-018's verified finding)
- [x] Dockerfile sets `ENV PYTHONPATH=/app/src/publisher/src`; **both** CMDs
      change from `python -m publisher.consumer` to `python -m consumer`
      (dev's `watchfiles` invocation included)
- [x] The `dev`/`prod` editable-vs-baked split collapses as it did for `api` —
      nothing is installed, every stage runs from source
- [x] Root `pythonpath` becomes
      `["src/api/tests", "src/api/src", "src/publisher/src"]`
- [x] Publisher's module names do not collide with api's bare
      `{dependencies, errors, main, migrate}` — `consumer` and `processing` are clear
- [x] `docker compose config -q` passes and `docker build --target dev` succeeds
- [x] Gate check passes: `uv sync --all-packages && docker compose config -q`,
      then `uv run pytest -q -m "not integration"` with no test lost

**Tests**: none (packaging layer — matrix says build gate only)
**Gate**: build

**Commit**: `refactor(publisher): flatten to src/publisher/src as a virtual member`

---

### T10: Add per-item processing with bounded concurrency

**What**: The normal path of the decision tree — fan-out under a semaphore, the
per-item transaction, duplicate short-circuit, and requeue.
**Where**: `src/publisher/src/processing.py` (new),
`src/publisher/tests/test_processing.py` (new)
**Depends on**: T1, T3, T4, T5, T7, T9
**Reuses**: `shared.reimbursement.repository`, `shared.producer.publish`,
`shared.failure_log`, `shared.errors.sanitize`
**Requirement**: PUB-01…PUB-21, PUB-36…PUB-42

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [x] `ItemOutcome` enum: `PUBLISHED | DUPLICATE | REQUEUED | ESCALATED | LOGGED | INVALID`
- [x] `process_item` **never raises** — a test drives an unexpected exception
      through it and asserts an outcome is returned, not propagated (PUB-38)
- [x] The publish happens **inside** `async with conn.transaction()`, so a
      publish failure rolls back with no explicit rollback call
- [x] A test asserts that after a publish failure **no row remains** (PUB-09) —
      asserting the fake was called does not satisfy this
- [x] A duplicate insert yields `DUPLICATE` with no republish, no escalation,
      no failure-log entry (PUB-18)
- [x] A duplicate drop emits a structured record carrying a stable event name,
      `request_id`, constraint name, and envelope `retry` (PUB-19)
- [x] Non-duplicate insert failure and publish failure both requeue a
      **single-item** `RequestEnvelope` with `retry+1` and exactly one new
      `AttemptError`, with the correct `stage` (PUB-10, PUB-11)
- [x] A test drives a second failure and asserts the requeued envelope carries
      **two** entries in order — appended, never truncated (PUB-12)
- [x] Requeue failure falls back to the failure log (PUB-16)
- [x] Concurrency: an instrumented fake records maximum observed in-flight; a
      500-item message asserts it **never exceeds 10** (PUB-36). Asserting a
      `Semaphore` was constructed does not satisfy this
- [x] A test asserts one item's failure leaves the other items' outcomes
      unchanged (PUB-06)
- [x] Gate check passes: `uv run pytest -q -m "not integration"`
- [x] Test count: T9's total + ≥18 new, all passing (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(publisher): add per-item processing with bounded concurrency`

---

### T11: Add retry-ceiling escalation and bad-input handling

**What**: The exceptional branches — `retry > 3`, malformed envelopes, invalid
items, empty payloads.
**Where**: `src/publisher/src/processing.py` (modify),
`src/publisher/tests/test_processing.py` (modify)
**Depends on**: T5, T8, T10
**Reuses**: `shared.reimbursement.use_cases.send_human_review` from T8;
`shared.failure_log` from T5
**Requirement**: PUB-22…PUB-33

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [x] The `retry > 3` check happens **once, before fan-out** — a test asserts no
      item is processed through the normal path for such a message (PUB-22)
- [x] Escalation produces `ESCALATED` with **no publish and no requeue**,
      whether or not the insert succeeded (PUB-26)
- [x] Escalation insert failure → failure log carrying the full history (PUB-27)
- [x] Escalation hitting the unique constraint → `DUPLICATE`, **not** the
      failure log (PUB-28)
- [x] Malformed / non-`RequestEnvelope` message → `LOGGED`, with no DB call and
      no republish (PUB-29); a test asserts the fake repository recorded zero calls
- [x] An item failing `ReimbursementRequest` validation → `INVALID`, never
      retried and never escalated (PUB-30)
- [x] `payload == []` → logged no-op, not an error (PUB-31)
- [x] A test feeds a bad message followed by a good one and asserts the good one
      still processes — the loop survives (PUB-32)
- [x] Gate check passes: `uv run pytest -q -m "not integration"`
- [x] Test count: T10's total + ≥12 new, all passing (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(publisher): add retry-ceiling escalation and bad-input handling`

---

### T12: Consume `Request` and commit offsets per message

**What**: The `AIOConsumer` lifecycle, the loop, the offset commit, graceful
shutdown, and the composition root.
**Where**: `src/publisher/src/consumer.py` (replace the placeholder),
`src/publisher/tests/test_consumer.py` (new),
`src/publisher/tests/conftest.py` (new)
**Depends on**: T3, T9, T10, T11
**Reuses**: `PublisherConfig.to_consumer_config` from T3; `managed_producer`
from `shared/producer.py`; `managed_pool` from T7
**Requirement**: PUB-07, PUB-33, PUB-34, PUB-40, PUB-41

**Tools**:

- MCP: `context7` (confluent-kafka `AIOConsumer` `consume`/`commit` surface)
- Skill: NONE

**Done when**:

- [x] The `SampleMessage` / `sample-queue` placeholder is **gone**
- [x] `AIOConsumer` is constructed **inside** the running loop, never at import
      — it calls `asyncio.get_event_loop()` (`_AIOConsumer.py:47`)
- [x] Uses `consume(num_messages=1)`, not `poll()`
- [x] The offset is committed **once per message, after every item settles** — a
      test with a fake consumer asserts the commit happens after the last item,
      and exactly once (PUB-07, PUB-40)
- [x] A test asserts no commit occurs while items are still in flight
- [x] Startup asserts `pool_max_size >= item_concurrency` and **refuses to
      boot** otherwise; a test asserts the failure is raised, with the message
      naming both values (PUB-41, R-005)
- [x] Startup fails clearly when `DATABASE_URL` is unset
- [x] A shutdown signal lets the current message finish and commit, then exits;
      a test asserts an interrupted message's offset is **not** committed (PUB-33)
- [x] Gate check passes: `uv run pytest -q -m "not integration"`
- [x] Test count: T11's total + ≥10 new, all passing (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(publisher): consume Request and commit offsets per message`

---

### T13: Round-trip a batch through Kafka and Postgres

**What**: The end-to-end proof — a real `Request` message becomes real rows and
real `Reimbursement` messages.
**Where**: `src/publisher/tests/test_integration.py` (new),
`src/publisher/tests/conftest.py` (modify)
**Depends on**: T2, T12
**Reuses**: the `KafkaContainer` fixture pattern from
`src/api/tests/reimbursement/create/conftest.py`; the workspace Postgres
fixture from T6
**Requirement**: PUB-02, PUB-07, PUB-35, PUB-40

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [x] Marked `@pytest.mark.integration` so the Quick gate stays Kafka-free
- [x] A real multi-item `Request` envelope is produced, consumed by the
      publisher, and yields exactly one committed row **and** one
      `Reimbursement` message **per item** (PUB-02)
- [x] Each consumed `Reimbursement` message carries the `uuid` of the row that
      exists in Postgres, and **no payload** (AD-015)
- [x] The source message's offset advances exactly once (PUB-07, PUB-40)
- [x] A message sized near `KAFKA_MAX_MESSAGE_BYTES` is consumed and processed —
      proving the consumer's fetch sizing, not just the producer's (PUB-35)
- [x] The Kafka container is sized **from** `KAFKA_MAX_MESSAGE_BYTES`, never a
      retyped literal
- [x] Gate check passes: `uv run pytest -q`
- [x] Test count: T12's total + ≥4 new, all passing (no silent deletions)

**Tests**: integration
**Gate**: full

**Commit**: `test(publisher): round-trip a batch through kafka and postgres`

---

## Phase Execution Map

Execution is strictly sequential T1 → T13. Arrows are **true data
dependencies**, not execution order — a task with no inbound arrow simply has no
prerequisite.

```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5

Phase 1:   T1 ──→ T2

Phase 2:   T3 ──→ T5
           T4

Phase 3:   T6 ──┐
           T3 ──┼──→ T7 ──┐
           T4 ──┘         ├──→ T8
                    T1 ───┘

Phase 4:   T9 ──┐
     T1,T3,T4,  ├──→ T10 ──→ T11 ──┐
     T5,T7 ─────┘        T5,T8 ────┤
                                    ├──→ T12
                         T3, T9 ────┘

Phase 5:   T2 ──┐
                ├──→ T13
           T12 ─┘
```

---

## Task Granularity Check

| Task | Scope | Status |
| ---- | ----- | ------ |
| T1: wire models | 1 file, one concept (error history contract) | ✅ Granular |
| T2: envelope prefix | 1 line + its assertions | ✅ Granular |
| T3: config | 1 file, 3 cohesive dataclasses + their loader | ⚠️ Cohesive — splitting yields three ~10-line commits that cannot be tested apart from `load_config()` |
| T4: errors | 1 file, 2 small additions | ✅ Granular |
| T5: failure log | 1 file, 1 function | ✅ Granular |
| T6: fixture promotion | test infrastructure, 3 conftests | ✅ Granular |
| T7: repository | 1 module + its dependency | ✅ Granular |
| T8: use case | 1 module, 2 functions | ✅ Granular |
| T9: flatten | packaging only, no behaviour | ✅ Granular |
| T10: processing — normal path | 1 module, the happy/duplicate/requeue branches | ⚠️ Cohesive — the largest task; split from T11 at the normal-vs-exceptional seam rather than by line count |
| T11: processing — exceptional paths | same module, the escalation/bad-input branches | ⚠️ Cohesive — see T10 |
| T12: consumer | 1 module + composition root | ✅ Granular |
| T13: e2e | 1 test file + fixture | ✅ Granular |

---

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| ---- | ---------------------- | ------------- | ------ |
| T1 | None | no inbound arrow | ✅ Match |
| T2 | T1 | T1 → T2 | ✅ Match |
| T3 | None | no inbound arrow | ✅ Match |
| T4 | None | no inbound arrow | ✅ Match |
| T5 | T3 | T3 → T5 | ✅ Match |
| T6 | None | no inbound arrow | ✅ Match |
| T7 | T3, T4, T6 | T3, T4, T6 → T7 | ✅ Match |
| T8 | T1, T7 | T1, T7 → T8 | ✅ Match |
| T9 | None | no inbound arrow | ✅ Match |
| T10 | T1, T3, T4, T5, T7, T9 | T1, T3, T4, T5, T7, T9 → T10 | ✅ Match |
| T11 | T5, T8, T10 | T5, T8, T10 → T11 | ✅ Match |
| T12 | T3, T9, T10, T11 | T3, T9, T10, T11 → T12 | ✅ Match |
| T13 | T2, T12 | T2, T12 → T13 | ✅ Match |

No task depends on a later phase.

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| ---- | --------------------------- | --------------- | --------- | ------ |
| T1 | Model behaviour (`AttemptError.next`) | unit | unit | ✅ OK |
| T2 | API envelope wrapper | unit | unit + integration | ✅ OK |
| T3 | Config | unit | unit | ✅ OK |
| T4 | Shared error helpers | unit | unit | ✅ OK |
| T5 | Failure log | unit | unit | ✅ OK |
| T6 | Repository/data-access enablement | integration | integration | ✅ OK |
| T7 | Repository / data access | integration | integration | ✅ OK |
| T8 | Shared use cases | integration + unit | integration + unit | ✅ OK |
| T9 | Packaging | none | none | ✅ OK |
| T10 | Publisher business logic | unit | unit | ✅ OK |
| T11 | Publisher business logic | unit | unit | ✅ OK |
| T12 | Publisher consumer config + loop | unit | unit | ✅ OK |
| T13 | End-to-end round trip | integration | integration | ✅ OK |

**T9 is the only `Tests: none`,** and it is justified by the matrix ("Packaging
— build gate only"), not by "covered in another task". Its gate still runs the
full suite, so a broken import or lost module fails it loudly.

**Pure-schema models carry no dedicated test** per the matrix, matching the
repo's existing absence of a `test_models.py` — but `AttemptError.next` and the
back-compat parse are *behaviour*, so T1 tests them.

---

## Execution Mode

**13 tasks, packing into two task-budgeted batches:**

| Batch | Phases | Tasks | Count |
| ----- | ------ | ----- | ----- |
| 1 | Phases 1–3 | T1 … T8 | 8 |
| 2 | Phases 4–5 | T9 … T13 | 5 |

More than one batch, so the sub-agent offer applies — **awaiting the user's
decision before any worker is dispatched.**

The Verifier runs automatically after T13 regardless of that choice — it is
never optional and never prompted.

---

## Notes Carried Into Execute

- **AD-016/AD-017/AD-025/AD-026 are not yet appended** to `.specs/STATE.md`.
  Append them on approval, before or during Phase 1.
- **T6 carries a genuine unknown**: whether pytest's `pythonpath` is applied
  early enough for a rootdir `conftest.py` to import `helpers`/`migrate`. The
  task states the fallback and requires recording the deviation.
- **`docs/codebase/` will be stale after this feature** — `ARCHITECTURE.md:90`
  and `CONCERNS.md:21` both state that nothing reads or writes Postgres and that
  `asyncpg` is unused. Worth an `architecture-evaluate` incremental pass once
  T13 lands.
