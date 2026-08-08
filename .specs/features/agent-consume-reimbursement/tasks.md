# Agent — Consume `Reimbursement`, Resolve by UUID — Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and
follow its Execute flow and Critical Rules.** Do not search for skill files by
filesystem path. The skill is the source of truth for the full flow (per-task
cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user — do not proceed
without it.**

---

**Spec**: `.specs/features/agent-consume-reimbursement/spec.md`
**Design**: `.specs/features/agent-consume-reimbursement/design.md`
**Risks**: `.specs/RISKS.md` (R-001, R-005, R-007)
**Status**: Draft

---

## Test Coverage Matrix

> Generated from codebase, project guidelines, and spec — confirm before Execute.
> **Guidelines found:** `docs/codebase/TESTING.md`, `docs/codebase/CONVENTIONS.md`,
> `pyproject.toml` (`python_classes`/`python_functions`/`testpaths`/`markers`),
> `~/.claude/CLAUDE.md`. No coverage tool and no enforced threshold exist
> (`TESTING.md:37-39`), so **strong defaults apply** for depth.
>
> Style and structure sampled directly from `publisher-consume-request`'s own
> shipped code — the closest possible precedent, same shape of problem one
> layer over: `src/publisher/tests/{fakes.py,conftest.py,test_processing.py,test_consumer.py,test_integration.py}`,
> `src/shared/tests/reimbursement/{test_repository.py,use_cases/test_send_human_review.py}`,
> `src/shared/tests/{test_models.py,test_config.py}`. `Describe*` classes
> holding `it_*` methods, full type hints, async tests carrying an explicit
> `pytestmark = pytest.mark.anyio`, test doubles as a `fakes.py` module (not
> conftest fixtures) when a double needs different per-test construction args.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------- | --------------------- | ----------------- | ----------- |
| `Stage` widening (`shared/models.py`) | unit | The new `"resolve"` value round-trips through `AttemptError.next` | `src/shared/tests/test_models.py` | quick |
| `AgentConfig` + `Config.agent` (`shared/config.py`) | unit | Every new field; boots with `AGENT_CONSUMER_GROUP_ID` unset (default applies) | `src/shared/tests/test_config.py` | quick |
| Repository extensions (`get_by_uuid`, `update_human_review`) | integration (real Postgres) | Key query paths: found / not-found / update-affects-0 / update-affects-1 | `src/shared/tests/reimbursement/test_repository.py` | quick¹ |
| Use-case extension (`escalate_existing`) | integration + unit | 1:1 to spec ACs — success, ghost (0 rows), rendered history reuse | `src/shared/tests/reimbursement/use_cases/test_send_human_review.py` | quick¹ |
| Agent decision tree (`agent/validation.py`) | unit (fakes) | All branches; 1:1 to spec ACs AGT-01…AGT-22; every listed edge case has a test | `src/agent/tests/test_validation.py` | quick |
| Agent consumer config + loop | unit (fake consumer) | Config invariants + offset/loop semantics + shutdown (AGT-22) | `src/agent/tests/test_consumer.py` | quick |
| End-to-end round trip | integration (`@pytest.mark.integration`) | Real Postgres + real Kafka: resolved, ghost, and stale cases each round-trip | `src/agent/tests/test_integration.py` | full |

¹ **Postgres-backed tests run in the Quick gate**, same convention
`publisher-consume-request` established — `integration` means "requires a
container *beyond* the suite's own Postgres default" (i.e. Kafka).

## Gate Check Commands

> Generated from codebase — confirm before Execute.

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | After tasks with unit and/or Postgres-backed tests | `uv run pytest -q -m "not integration"` |
| Full | After tasks with Kafka-container tests | `uv run pytest -q` |

**Baseline: 268 passed, 6 deselected** on `-m "not integration"` (274 passed on
the full run) as of `feature/5_reimbursement_publisher`'s tip
(`publisher-consume-request/validation.md`). Every task states its expected
new total so a silent deletion fails the gate.

---

## Execution Plan

Phases are ordered and run sequentially — each phase completes before the next
begins, and tasks within a phase execute in order.

### Phase 1: Shared kernel widening

The two committed-but-incomplete gaps this feature's design flagged — no
domain knowledge yet, additive only, cannot break `publisher`'s existing use.

```
T1 → T2
```

### Phase 2: Shared reimbursement slice extensions

Read-by-uuid, human-review escalation for an existing row. First real use of
`shared.reimbursement` by a second consumer.

```
T3 → T4
```

### Phase 3: The agent service

```
T5 → T6 → T7
```

### Phase 4: End-to-end proof

```
T8
```

---

## Task Breakdown

### T1: Widen `Stage` to include the agent's own failure stage

**What**: Add `"resolve"` to the `Stage` type alias.
**Where**: `src/shared/src/shared/models.py` (modify),
`src/shared/tests/test_models.py` (modify)
**Depends on**: None
**Reuses**: `Stage`/`AttemptError` at `models.py:14,53-74` — one-line change,
`AttemptError.next` itself is untouched
**Requirement**: design.md Risks & Concerns — the `Stage` widening; enables AGT-08/AGT-09

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `Stage = Literal["db-insert", "publish", "resolve"]`
- [ ] A test calls `AttemptError.next(errors, "resolve", exc)` and asserts the
      resulting `stage == "resolve"`
- [ ] Existing `"db-insert"`/`"publish"` tests are unchanged and still pass —
      this is additive only
- [ ] Gate check passes: `uv run pytest -q -m "not integration"`
- [ ] Test count: 268 + ≥1 new, all passing (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(shared): widen the AttemptError stage for the agent's resolve failures`

---

### T2: Add agent configuration

**What**: `AgentConfig` dataclass and its `Config.agent` field, assembled inside
the existing cached `load_config()`.
**Where**: `src/shared/src/shared/config.py` (modify),
`src/shared/tests/test_config.py` (modify)
**Depends on**: None
**Reuses**: `PublisherConfig` at `config.py:69-92` — same shape, same
`to_consumer_config()` idiom, minus the fetch-size overrides and
`item_concurrency` (this feature has no per-message fan-out)
**Requirement**: design.md Data Models — `AgentConfig`

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `AgentConfig(consumer_group_id: str, consume_timeout_seconds: float = 1.0)`
- [ ] `AgentConfig.to_consumer_config(kafka)` sets `enable.auto.commit=False`,
      `auto.offset.reset="earliest"`, `group.id`; SASL/TLS branches reused from
      `KafkaConfig.security_config()`, not re-derived
- [ ] **No** `fetch.max.bytes`/`max.partition.fetch.bytes` override — `Reimbursement`
      messages are small, fixed-shape envelopes (spec Assumptions); a test
      asserts these keys are absent from the built config, distinguishing this
      deliberately from `PublisherConfig`'s own override
- [ ] `Config` gains an `agent: AgentConfig` field; `load_config()` constructs
      it with `consumer_group_id=os.getenv("AGENT_CONSUMER_GROUP_ID", "agent")`
- [ ] A test asserts `load_config()` succeeds with `AGENT_CONSUMER_GROUP_ID`
      unset (default `"agent"` applies)
- [ ] A test asserts `enable.auto.commit` is `False` — not merely present
- [ ] Gate check passes: `uv run pytest -q -m "not integration"`
- [ ] Test count: T1's total + ≥5 new, all passing (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(shared): add agent configuration`

---

### T3: Extend the reimbursement repository for read-by-uuid and escalation

**What**: `get_by_uuid` (full-row read) and `update_human_review` (status
transition on an existing row).
**Where**: `src/shared/src/shared/reimbursement/repository.py` (modify),
`src/shared/tests/reimbursement/test_repository.py` (modify)
**Depends on**: None
**Reuses**: `managed_pool`/`insert_pending`/`insert_human_review`/`is_duplicate`
at `repository.py:44-84` — same file, same conventions; the migration schema
at `src/api/src/migrations/0001.create-reimbursement.sql`
**Requirement**: AGT-01, AGT-02, AGT-03, AGT-14, AGT-18

**Tools**:

- MCP: `context7` (asyncpg `fetchrow`/`execute` result-parsing surface)
- Skill: NONE

**Done when**:

- [ ] `get_by_uuid(conn, uuid) -> asyncpg.Record | None` — `SELECT * FROM
      reimbursement WHERE uuid = $1`, via `conn.fetchrow`
- [ ] A test proves a real inserted row round-trips through `get_by_uuid` with
      every column readable (`status`, `updated_at`, `original_payload` at minimum)
- [ ] A test proves a random, never-inserted `uuid` returns `None` — no
      exception raised (AGT-03's ghost case depends on this)
- [ ] `update_human_review(conn, uuid, reason) -> bool` — `UPDATE reimbursement
      SET status = 'human-review', decision_reason = $2, updated_at = now()
      WHERE uuid = $1`; returns whether exactly one row was affected, parsed
      from asyncpg's `UPDATE n` status string
- [ ] A test proves updating a real row returns `True` and the row's `status`/
      `decision_reason`/`updated_at` are genuinely changed (query it back, don't
      just trust the return value)
- [ ] A test proves updating a never-inserted `uuid` returns `False` — no
      exception raised (AGT-18 depends on this)
- [ ] Gate check passes: `uv run pytest -q -m "not integration"`
- [ ] Test count: T2's total + ≥6 new, all passing (no silent deletions)

**Tests**: integration (real Postgres)
**Gate**: quick

**Commit**: `feat(shared): add read-by-uuid and human-review update to the reimbursement repository`

---

### T4: Add human-review escalation for an existing row

**What**: `escalate_existing` — the `UPDATE`-based sibling to `send_human_review`'s
`INSERT`-based fallback, sharing `render_history`.
**Where**: `src/shared/src/shared/reimbursement/use_cases/send_human_review.py`
(modify), `src/shared/tests/reimbursement/use_cases/test_send_human_review.py`
(modify)
**Depends on**: T3
**Reuses**: `render_history`/`send_human_review` at
`send_human_review.py:15-37` — `render_history` is called unchanged; the new
function is a sibling, not a merge into one polymorphic function (design.md
Tech Decisions)
**Requirement**: AGT-14, AGT-15, AGT-16, AGT-17, AGT-18

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `escalate_existing(conn, uuid, errors: list[AttemptError]) -> UUID | None`
      — calls `render_history(errors)` then `repository.update_human_review`;
      returns `uuid` on success, `None` when the row doesn't exist
- [ ] A test proves a real row is escalated: `status == 'human-review'` and
      `decision_reason` contains every entry in a 3-error `errors` list —
      attempt number, timestamp, stage, error type, and message, not a count
      (AGT-15)
- [ ] A test proves an empty `errors` list still produces a non-null
      `decision_reason` stating the ceiling was reached with no detail carried
      (AGT-16) — this is `render_history`'s existing `_NO_HISTORY` branch,
      exercised through the new function
- [ ] A test proves escalating a never-inserted `uuid` returns `None` (AGT-18)
- [ ] Gate check passes: `uv run pytest -q -m "not integration"`
- [ ] Test count: T3's total + ≥3 new, all passing (no silent deletions)

**Tests**: integration + unit
**Gate**: quick

**Commit**: `feat(shared): add human-review escalation for an existing row`

---

### T5: Agent decision tree — parse, dispatch, and retry-ceiling escalation

**What**: `agent/validation.py`'s entry point, message parsing, and the
`retry > 3` branch — checked before the normal resolve flow, mirroring the
publisher's own per-envelope check-before-processing ordering.
**Where**: `src/agent/src/agent/validation.py` (new), `src/agent/tests/fakes.py`
(new), `src/agent/tests/conftest.py` (new), `src/agent/tests/test_validation.py`
(new)
**Depends on**: T1, T4
**Reuses**: `publisher/processing.py`'s shape (a pure decision-tree module
returning an outcome enum, never raising) and `publisher/tests/fakes.py`'s
test-double pattern (`FakeProducer`, `FakePool`/`FakeConnection`, `RealPool`) —
adapted, not copied verbatim, since this feature has no per-item fan-out to
simulate
**Requirement**: AGT-14…AGT-22 (retry-ceiling story + bad-input story)

**Tools**:

- MCP: `context7` (pydantic `ValidationError` surface, confirm current API)
- Skill: NONE

**Done when**:

- [ ] `MessageOutcome` enum: `RESOLVED | STALE | GHOST | REQUEUED | ESCALATED
      | LOGGED | INVALID`
- [ ] `async handle_message(deps, raw: bytes) -> MessageOutcome` — never raises
- [ ] A message that is not valid JSON or does not match `ReimbursementEnvelope`
      is written to `shared.failure_log` (critical) and returns `INVALID` — no
      DB attempt, no republish (AGT-20)
- [ ] `retry > 3` is checked **before** any DB read — routes to
      `escalate_item`, which calls `escalate_existing`
- [ ] `escalate_existing` returning a `uuid` (1 row affected) logs the
      escalation at `logging.ERROR` (AGT-17) and returns `ESCALATED` — no
      republish, no further action
- [ ] `escalate_existing` returning `None` (ghost + `retry > 3`, AGT-18) writes
      to `shared.failure_log` and returns `LOGGED`
- [ ] A genuine DB error during escalation (not the `None`-return case) writes
      to `shared.failure_log` and returns `LOGGED` (AGT-19)
- [ ] `src/agent/tests/fakes.py` provides `FakeProducer` and `FakePool`/
      `FakeConnection`/`RealPool`, adapted from `publisher/tests/fakes.py`
      (no in-flight/max-in-flight counters — no concurrency to observe here)
- [ ] `src/agent/tests/conftest.py` clears `load_config`'s cache (autouse),
      matching the pattern already used in `shared`/`api`/`publisher` tests
- [ ] Gate check passes: `uv run pytest -q -m "not integration"`
- [ ] Test count: T4's total + ≥8 new, all passing (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(agent): add message parsing and retry-ceiling escalation`

---

### T6: Agent decision tree — resolve by uuid, staleness, and requeue on failure

**What**: `resolve_item` — the `retry <= 3` path: `get_by_uuid`, ghost
tolerance (R-001), the staleness guard, and requeue-with-history on a
transient DB error.
**Where**: `src/agent/src/agent/validation.py` (modify),
`src/agent/tests/test_validation.py` (modify)
**Depends on**: T3, T5
**Reuses**: `AttemptError.next`, `shared.producer.publish`,
`shared.config.REIMBURSEMENT_TOPIC`; the same `handle_message` entry point T5
built
**Requirement**: AGT-01…AGT-13

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `get_by_uuid` returning `None` logs at informational level (not `ERROR`
      — R-001, a ghost is not a failure) and returns `GHOST` — no republish, no
      retry, no failure-log entry (AGT-03)
- [ ] `get_by_uuid` raising a genuine DB error republishes a new
      `ReimbursementEnvelope` for the same `uuid` with `retry` set to `retry +
      1` and `errors` gaining exactly one `AttemptError.next(errors, "resolve",
      exc)` entry; logs at `logging.ERROR` via `shared.errors.sanitize` (AGT-08,
      AGT-09, AGT-12); returns `REQUEUED`
- [ ] A second, independently-injected failure on a republished message
      carries **two** `errors` entries in order, prior entries untouched
      (AGT-10)
- [ ] The republish itself failing (`PublishFailed`) writes to
      `shared.failure_log` with the full history and returns `LOGGED` (AGT-13)
- [ ] A resolved row with `published_at < updated_at` logs informationally and
      returns `STALE` — no further action (AGT-05, AGT-07)
- [ ] A resolved row with `published_at >= updated_at` (including the exact-
      equal case) logs informationally and returns `RESOLVED` (AGT-06)
- [ ] Gate check passes: `uv run pytest -q -m "not integration"`
- [ ] Test count: T5's total + ≥8 new, all passing (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(agent): add resolve-by-uuid, staleness guard, and requeue on failure`

---

### T7: Agent consumer — composition root and the consume loop

**What**: Config, pool, producer, consumer lifecycle; the one-message-at-a-time
loop; offset commit; startup and shutdown. Replaces the `SampleMessage` /
`sample-topic` placeholder entirely.
**Where**: `src/agent/src/agent/consumer.py` (rewrite),
`src/agent/tests/test_consumer.py` (new)
**Depends on**: T2, T6
**Reuses**: `publisher/consumer.py`'s shape (`managed_pool`, `managed_producer`,
`AIOConsumer` constructed inside the running loop, `consume(num_messages=1)` +
`enable.auto.commit=False`, signal handling via `asyncio.Event`) — **minus**
the `asyncio.Semaphore` fan-out and the pool-vs-concurrency startup assertion,
neither of which apply here
**Requirement**: AGT-21, AGT-22

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `check_startup_config(config)` refuses to boot if `DATABASE_URL` is unset
      — no concurrency-vs-pool assertion (nothing to check it against)
- [ ] `managed_consumer(config)` constructs `AIOConsumer` inside the running
      loop (never at import), subscribes to `REIMBURSEMENT_TOPIC`
- [ ] `run(deps, consumer, stopping)` consumes one message at a time
      (`num_messages=1`), calls `handle_message`, commits the offset only after
      it returns — for **every** `MessageOutcome`, not just success (AGT-21)
- [ ] SIGINT/SIGTERM set an `asyncio.Event`; the loop exits between messages —
      a message already in flight finishes and its offset commits normally,
      matching the publisher's own shutdown semantics (AGT-22)
- [ ] A test using a fake consumer whose `consume()` blocks mid-message and is
      then interrupted asserts the offset is **not** committed for that message
- [ ] The old `SampleMessage`/`sample-topic` code path is fully removed —
      `agent.consumer` now only does the above
- [ ] Gate check passes: `uv run pytest -q -m "not integration"`
- [ ] Test count: T6's total + ≥6 new, all passing (no silent deletions)

**Tests**: unit (fake consumer)
**Gate**: quick

**Commit**: `feat(agent): consume Reimbursement and commit offsets per message`

---

### T8: End-to-end proof

**What**: A real Kafka + Postgres round trip proving a `Reimbursement` message
resolves, a ghost is dropped, and a stale message is ignored.
**Where**: `src/agent/tests/test_integration.py` (new),
`src/agent/tests/conftest.py` (modify — agent-local Kafka container fixture,
mirroring `publisher/tests/conftest.py`)
**Depends on**: T7
**Reuses**: the workspace-level Postgres fixture (`publisher-consume-request`
T6); `publisher/tests/conftest.py`'s `kafka_bootstrap_server` fixture shape,
duplicated agent-local since pytest resolves conftest fixtures per directory
**Requirement**: Success Criteria (spec.md) — full pipeline proof

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] Insert a real `pending` row via the repository, publish a
      `ReimbursementEnvelope` for its `uuid` against a real broker, run the
      agent's consumer for one message, and assert it logs `RESOLVED` with no
      side effect on the row
- [ ] Publish a `ReimbursementEnvelope` for a `uuid` matching no row; assert it
      is dropped with no republish and no failure-log entry
- [ ] Publish a message whose `published_at` predates the row's `updated_at`;
      assert it is ignored and the row is unchanged
- [ ] Publish a message with `retry = 4` for a real row; assert the row's
      `status` becomes `human-review`
- [ ] Every case above advances the consumer offset exactly once
- [ ] Gate check passes: `uv run pytest -q`
- [ ] Test count: T7's total + ≥5 new, all passing (no silent deletions)

**Tests**: integration (`@pytest.mark.integration`)
**Gate**: full

**Commit**: `test(agent): round-trip Reimbursement through kafka and postgres`

---

## Phase Execution Map

Visual representation of task ordering. Phases run in sequence, and tasks
within a phase run in order:

```
Phase 1 → Phase 2 → Phase 3 → Phase 4

Phase 1:  T1 ──→ T2
Phase 2:  T3 ──→ T4
Phase 3:  T5 ──→ T6 ──→ T7
Phase 4:  T8
```

Execution is strictly sequential — there is no intra-phase parallelism. Total
8 tasks fits a single batch (≤ ~8 tasks) — no sub-agent delegation needed;
execution happens inline.

---

## Task Granularity Check

| Task | Scope | Status |
| ---- | ----- | ------ |
| T1: Widen `Stage` | 1 type alias, 1 file | ✅ Granular |
| T2: Add `AgentConfig` | 1 dataclass + 1 field, 1 file | ✅ Granular |
| T3: Extend repository | 2 cohesive functions, 1 file (mirrors publisher's T7 precedent of 4 functions in one task) | ✅ Granular |
| T4: Add `escalate_existing` | 1 function, 1 file | ✅ Granular |
| T5: Parse/dispatch/escalate | 1 module's entry point + one branch, cohesive (mirrors publisher's own single-module `processing.py` task split) | ✅ Granular |
| T6: Resolve/staleness/requeue | Same module, one more branch — cohesive extension of T5 | ✅ Granular |
| T7: Consumer composition root | 1 file (rewrite) | ✅ Granular |
| T8: End-to-end test | 1 test file | ✅ Granular |

---

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| ---- | ----------------------- | -------------- | ------ |
| T1 | None | None | ✅ Match |
| T2 | None | None | ✅ Match |
| T3 | None | None | ✅ Match |
| T4 | T3 | T3 → T4 | ✅ Match |
| T5 | T1, T4 | Phase 2 → Phase 3 (T4 → T5); T1 in Phase 1 → Phase 3 | ✅ Match |
| T6 | T3, T5 | Phase 2 → Phase 3 (T3 → T6 via phase order); T5 → T6 | ✅ Match |
| T7 | T2, T6 | Phase 1 → Phase 3 (T2 → T7 via phase order); T6 → T7 | ✅ Match |
| T8 | T7 | T7 → T8 (Phase 3 → Phase 4) | ✅ Match |

No task depends on a task in a later phase.

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| ---- | ---------------------------- | ----------------- | ----------- | ------ |
| T1: Widen `Stage` | `Stage` widening | unit | unit | ✅ OK |
| T2: Add `AgentConfig` | `AgentConfig` | unit | unit | ✅ OK |
| T3: Extend repository | Repository extensions | integration | integration | ✅ OK |
| T4: Add `escalate_existing` | Use-case extension | integration + unit | integration + unit | ✅ OK |
| T5: Parse/dispatch/escalate | Agent decision tree | unit | unit | ✅ OK |
| T6: Resolve/staleness/requeue | Agent decision tree | unit | unit | ✅ OK |
| T7: Consumer composition root | Agent consumer config + loop | unit | unit | ✅ OK |
| T8: End-to-end test | End-to-end round trip | integration | integration | ✅ OK |

No task defers its tests to a later task; no `Tests: none` used where the
matrix requires a type.
