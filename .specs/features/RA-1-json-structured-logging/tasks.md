# JSON Structured Logging & Request Correlation Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user — do not proceed without it.**

---

**Spec**: `.specs/features/RA-1-json-structured-logging/spec.md`
**Design**: `.specs/features/RA-1-json-structured-logging/design.md`
**Status**: Draft

---

## Test Coverage Matrix

> Generated from codebase, project guidelines, and spec — confirm before Execute. Guidelines found: `CLAUDE.md` (pytest Describe*/it_* style), `pyproject.toml` (pytest testpaths, conftest fixtures).

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------ | -------------------- | ---------------- | ----------- |
| `shared.logging` (new module: configure_logging, ContextVar, Filter) | unit | All branches (LOG_LEVEL valid/invalid/unset); 1:1 to LOG-01..08; CORR-05/06 isolation under concurrency | `packages/shared/tests/test_logging.py` | `uv run pytest packages/shared/tests/test_logging.py` |
| `shared.config.LoggingConfig` (new dataclass + load_config extension) | unit | All paths (default, set, unset); validates extension to Config | `packages/shared/tests/test_config.py` (update existing) | `uv run pytest packages/shared/tests/test_config.py` |
| `api.middleware.CorrelationIdMiddleware` (new ASGI middleware) | unit + integration | Happy path (UUID gen + echo); empty header; absent header; response header injection; concurrent isolation | `packages/api/tests/test_middleware.py` (new) | `uv run pytest packages/api/tests/test_middleware.py` |
| `api.reimbursement.create.producer` (modified build_envelope) | unit | Byte-splice correctness (correlation_id in prefix, None serializes as null); round-trip through RequestEnvelope parse | `packages/api/tests/reimbursement/create/test_producer.py` (update existing) | `uv run pytest packages/api/tests/reimbursement/create/test_producer.py` |
| `publisher.processing.handle_message` (modified: set/reset ContextVar) | integration | ContextVar present for message processing logs; CORR-09 requeue carries forward; handler error path omits correlation_id gracefully | `packages/publisher/tests/test_processing.py` (update existing) | `uv run pytest packages/publisher/tests/test_processing.py` |
| `reimbursement.validation.handle_message` (modified: set/reset ContextVar) | integration | ContextVar present for message processing logs; handler error path omits correlation_id gracefully | `packages/reimbursement/tests/test_validation.py` (update existing) | `uv run pytest packages/reimbursement/tests/test_validation.py` |
| `reimbursement.agent.agent.decide` (modified: metadata dict) | unit | Metadata includes correlation_id when available; omits key (not None) when unavailable | `packages/reimbursement/tests/agent/test_agent.py` (update existing) | `uv run pytest packages/reimbursement/tests/agent/test_agent.py` |
| `api.main` (startup: logging.basicConfig → configure_logging) | integration | Existing `test_main.py::DescribeLoggingSetup` updated to assert on `configure_logging` call + handler presence | `packages/api/tests/test_main.py` (update existing) | `uv run pytest packages/api/tests/test_main.py::DescribeLoggingSetup` |
| `shared.logging.log_event` (refactor: extra={} with UUID coercion) | unit | UUID fields coerced to str; existing call sites' output unchanged (6 sites in api/publisher); extra={} used not message-string JSON | `packages/shared/tests/test_logging.py` (new) | `uv run pytest packages/shared/tests/test_logging.py` |
| Entity/Config files (RequestEnvelope/ReimbursementEnvelope, LoggingConfig, migrate.py, consumer.py startup) | none | — (build gate only; schema correctness validated via integration/e2e tests) | — | build gate only |

**Coverage Expectation values** — per spec acceptance criteria and edge cases:

- **LOG-01..08**: Every control flow path for `configure_logging()` (unset default, valid levels, invalid fallback + warning)
- **CORR-01..06**: Every path for header (present/absent/empty), UUID generation, concurrent requests, middleware ContextVar isolation
- **CORR-07..11**: Envelope field presence (both topics), byte-splice correctness, consumer ContextVar setting, None handling
- **CORR-12..14**: Metadata dict construction, conditional key inclusion
- **Edge Cases** (design.md): configure_logging idempotency, log_event collision handling, correlation_id absence, message parsing error paths

## Gate Check Commands

> Generated from codebase — confirm before Execute.

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | After each individual task's unit tests | `uv run pytest packages/shared/tests/test_logging.py packages/shared/tests/test_config.py packages/api/tests/test_middleware.py packages/api/tests/reimbursement/create/test_producer.py packages/reimbursement/tests/agent/test_agent.py -v` |
| Full | After Phase 4/5 consumer integration + Phase 7 agent | `uv run pytest packages/shared/tests/ packages/api/tests/ packages/publisher/tests/ packages/reimbursement/tests/ -v` |
| Build | After all tasks (final gate before Verifier) | `uv run pytest packages/api packages/publisher packages/reimbursement packages/shared tests/e2e -v && uv run ruff check packages/ && uv build` |

---

## Execution Plan

Phases are ordered and run sequentially — each phase completes before the next begins, and tasks within a phase execute in order.

### Phase 1: Core Logging Infrastructure

Foundation tasks that no other task depends on. Build the structured logging and config infrastructure.

```
T1 → T2 → T3 → T4 → T5 → T6
```

### Phase 2: HTTP Request Correlation

Middleware + service startup. Wire up the HTTP boundary to establish and propagate correlation_id.

```
T7 → T8 → T9
```

### Phase 3: Kafka Models & Producer

Model changes + producer integration. Add correlation_id to envelopes and wire up the producer to include it.

```
T10 → T11
```

### Phase 4: Consumer Integration

Update both consumer handlers to set/reset ContextVar from envelopes. Update service startup for consumers.

```
T12 → T13 → T14 → T15
```

### Phase 5: LangFuse Integration & Documentation

Agent metadata + documentation. Wire up the LangFuse metadata dict and document the conventions.

```
T16 → T17
```

---

## Task Breakdown

### T1: Add `ecs-logging` to `packages/shared/pyproject.toml`

**What**: Add `ecs-logging>=2.3.0` as a runtime dependency in the shared package, enabling all downstream services to emit ECS-formatted JSON logs.
**Where**: `packages/shared/pyproject.toml`
**Depends on**: None
**Reuses**: Existing workspace dependency pattern (AD-019/AD-025 precedent: shared-owned deps consumed transitively by api/publisher/reimbursement)

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [ ] `ecs-logging>=2.3.0` added to `dependencies` section of `packages/shared/pyproject.toml`
- [ ] `uv sync` runs without error (workspace stays coherent)
- [ ] No other changes to the file; only the dependency line is added

**Tests**: none
**Gate**: build (workspace coherence check)

**Commit**: `chore(shared): add ecs-logging dependency for structured logging`

---

### T2: Create `LoggingConfig` dataclass in `shared.config`

**What**: Add a new `LoggingConfig` dataclass holding the raw `LOG_LEVEL` env var value, and extend `Config` to include a `logging: LoggingConfig` field following the existing "plain data holder" pattern (AD-023).
**Where**: `packages/shared/src/shared/config.py`
**Depends on**: T1 (for documentation only; no runtime dependency)
**Reuses**: `@dataclass(frozen=True)` pattern, Config nesting pattern (like Config.kafka, Config.database)

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [ ] New `LoggingConfig` dataclass with `level: str` field exists (frozen, documented)
- [ ] `Config.logging: LoggingConfig` field added after Config's other domains (kafka, database, failure_log)
- [ ] `load_config()` reads `os.getenv("LOG_LEVEL", "debug")` and passes it to LoggingConfig constructor
- [ ] Existing Config test (conftest.py `_clear_config_cache`) still passes without modification
- [ ] Gate check passes: `uv run pytest packages/shared/tests/test_config.py -v`
- [ ] Test count: existing suite runs; no silent deletions

**Tests**: unit (update existing test_config.py to cover new LoggingConfig field)
**Gate**: quick

**Commit**: `feat(shared): add LoggingConfig for LOG_LEVEL environment variable`

---

### T3: Implement ContextVar and helper functions in `shared.logging`

**What**: Create the `shared.logging` module (if not already present) with the correlation_id ContextVar and its read/write accessors: `set_correlation_id()`, `reset_correlation_id()`, `get_correlation_id()`.
**Where**: `packages/shared/src/shared/logging.py` (existing file, extended)
**Depends on**: T2 (no runtime dependency, but conceptually part of the same domain setup)
**Reuses**: contextvars stdlib, logging stdlib Filter base class

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [ ] Module-level `_correlation_id: ContextVar[str | None]` created with default `None`
- [ ] `get_correlation_id() -> str | None` returns current value (read-only accessor)
- [ ] `set_correlation_id(value: str | None) -> contextvars.Token` sets context, returns token for reset
- [ ] `reset_correlation_id(token: contextvars.Token) -> None` restores previous value
- [ ] All three functions are documented with docstrings
- [ ] Unit tests verify: getting unset returns None, set/reset round-trip, Token mechanics work correctly
- [ ] Concurrent isolation test: two async tasks setting different values don't bleed into each other
- [ ] Gate check passes: `uv run pytest packages/shared/tests/test_logging.py::DescribeContextVar -v`
- [ ] Test count: at least 5 ContextVar-specific tests

**Tests**: unit
**Gate**: quick

**Commit**: `feat(shared): add correlation_id ContextVar with set/get/reset accessors`

---

### T4: Implement `CorrelationIdFilter` in `shared.logging`

**What**: Create a `logging.Filter` subclass that injects the current correlation_id into every LogRecord that passes through, only when the value is not None (omitting the attribute entirely when None, not setting it to null).
**Where**: `packages/shared/src/shared/logging.py` (same file as T3)
**Depends on**: T3
**Reuses**: logging.Filter base class, get_correlation_id() from T3

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [ ] `class CorrelationIdFilter(logging.Filter)` defined with `filter(self, record: logging.LogRecord) -> bool`
- [ ] Implementation: `if (cid := get_correlation_id()) is not None: record.correlation_id = cid` (then always `return True`)
- [ ] Attribute is omitted (not set to None) when get_correlation_id() is None — confirmed in log record (ecs_logging won't emit the key if absent)
- [ ] Unit tests verify: record has attribute when ContextVar is set; attribute is absent when ContextVar is None; filter always returns True
- [ ] Edge case test: concurrent requests' logs don't leak each other's correlation_id
- [ ] Gate check passes: `uv run pytest packages/shared/tests/test_logging.py::DescribeCorrelationIdFilter -v`
- [ ] Test count: at least 4 Filter-specific tests

**Tests**: unit
**Gate**: quick

**Commit**: `feat(shared): add CorrelationIdFilter to inject correlation_id into logs`

---

### T5: Implement `configure_logging()` with LOG_LEVEL handling in `shared.logging`

**What**: Create the `configure_logging() -> None` entrypoint that: (1) attaches an ECS JSON formatter + CorrelationIdFilter to the root logger exactly once (idempotent handler attachment); (2) reads LOG_LEVEL from config (with fallback to "debug" for invalid values); (3) resolves the level name to a logging constant and applies it to root logger; (4) emits one warning line if an invalid level was provided.
**Where**: `packages/shared/src/shared/logging.py` (same file as T3/T4)
**Depends on**: T1, T2, T3, T4
**Reuses**: `load_config()`, `logging.getLevelNamesMapping()` (Python 3.11+), ecs_logging.StdlibFormatter, CorrelationIdFilter, logging stdlib

**Tools**:
- MCP: filesystem, context7 (optional, for ecs-logging API confirmation)
- Skill: NONE

**Done when**:

- [ ] Module-level `_handler: logging.Handler | None = None` sentinel added
- [ ] `configure_logging()` loads config once via `load_config()` (already cached, no N+1 calls)
- [ ] Handler attached to root logger only if `_handler is None` (idempotent, no duplicates)
- [ ] Handler includes ecs_logging.StdlibFormatter (wrapped in a StreamHandler to stdout)
- [ ] CorrelationIdFilter added to the handler
- [ ] `logging.getLevelNamesMapping()` used for case-insensitive level-name lookup
- [ ] Valid levels (debug, info, warning, error, critical — case-insensitive) set root logger level correctly
- [ ] Invalid level name falls back to DEBUG and emits exactly one WARNING-level log line naming the invalid value
- [ ] Unset LOG_LEVEL defaults to "debug"
- [ ] `root.setLevel()` runs on every call (even if handler already attached) so tests can change LOG_LEVEL mid-process
- [ ] Unit tests verify: all 5 valid level names work; invalid value falls back + warning emitted; unset defaults to debug; handler is attached once; level changes on repeated calls
- [ ] Gate check passes: `uv run pytest packages/shared/tests/test_logging.py::DescribeConfigureLogging -v`
- [ ] Test count: at least 8 configure_logging-specific tests

**Tests**: unit
**Gate**: quick

**Commit**: `feat(shared): implement configure_logging with LOG_LEVEL resolution and fallback`

---

### T6: Refactor `log_event()` to use `extra={}` with UUID coercion in `shared.logging`

**What**: Refactor the existing `log_event()` function signature and internals to pass structured fields via `logger.log(level, message, extra={...})` instead of manually `json.dumps()`-ing them into the message string. Explicitly coerce UUID-typed field values with `str()` before adding them to extra, so ecs-logging's fallback serializer doesn't render them as `repr()` strings.
**Where**: `packages/shared/src/shared/logging.py` (same file as T3/T4/T5)
**Depends on**: T5 (configure_logging must be running for the formatter to exist)
**Reuses**: Python stdlib logging, existing UUID type hints in docstring, existing 6 call sites (api/publisher)

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [ ] `log_event()` signature **unchanged**: `log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None`
- [ ] Internals refactored to: (1) coerce any UUID field value with `str(v) if isinstance(v, UUID) else v` for each field; (2) call `logger.log(level, event, extra={"event": event, **coerced_fields})` instead of manually json.dumps-ing
- [ ] Existing broad exception handling preserved (fallback logger still catches if extra={} raises KeyError on collision)
- [ ] All 6 existing call sites continue to work **without modification** (backward compatible)
- [ ] Unit tests verify: UUID fields render as strings (not `repr()`); non-UUID fields pass through unchanged; event appears as both message and extra field; exception handling still works
- [ ] Integration test: trigger one existing call site (e.g. POST /api/v1/reimbursement) and verify fields appear as top-level JSON keys in the formatted log line, and UUID values are clean strings
- [ ] Gate check passes: `uv run pytest packages/shared/tests/test_logging.py::DescribeLogEvent -v && uv run pytest packages/api/tests -k "test_route or test_create" -v` (samples existing call sites)
- [ ] Test count: at least 5 log_event-specific tests + existing suite

**Tests**: unit + integration (by updating existing test sites)
**Gate**: quick

**Commit**: `refactor(shared): refactor log_event to use extra={} for structured fields with UUID coercion`

---

### T7: Create `CorrelationIdMiddleware` in `api/middleware.py`

**What**: Create a new raw ASGI middleware class (not `@app.middleware("http")`) that: (1) reads X-Request-ID from request headers or generates a fresh UUID7; (2) sets it into the correlation_id ContextVar for the duration of request handling; (3) wraps the send callable to inject X-Request-ID as a response header; (4) always resets the ContextVar in a finally block.
**Where**: `packages/api/src/api/middleware.py` (new file)
**Depends on**: T3 (ContextVar functions), T5 (configure_logging, though not called here)
**Reuses**: ASGI spec (scope, receive, send), uuid.uuid7, shared.logging.{set_correlation_id, reset_correlation_id}

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [ ] New file `packages/api/src/api/middleware.py` created
- [ ] `class CorrelationIdMiddleware` defined with `__init__(self, app)` and `async __call__(self, scope, receive, send)` signature
- [ ] Reads `X-Request-ID` from `scope["headers"]` (case-insensitive); generates `uuid.uuid7()` if absent or empty-string
- [ ] Sets ContextVar via `token = set_correlation_id(value)` before processing
- [ ] Wraps `send` to inject `X-Request-ID` response header on `http.response.start` event
- [ ] Always resets ContextVar in `finally: reset_correlation_id(token)` after awaiting downstream app
- [ ] Unit tests verify: UUID generated when header absent; header echoed when present; empty header treated as absent; response header injected; concurrent requests don't leak ContextVar values
- [ ] Edge case tests: malformed headers, very long values (JSON escaping handled by formatter, not middleware)
- [ ] Gate check passes: `uv run pytest packages/api/tests/test_middleware.py -v`
- [ ] Test count: at least 6 middleware-specific tests

**Tests**: unit + integration
**Gate**: quick

**Commit**: `feat(api): add CorrelationIdMiddleware for HTTP request correlation`

---

### T8: Register `CorrelationIdMiddleware` in `api/main.py` and call `configure_logging()`

**What**: (1) Import and register the new middleware in api/main.py using `app.add_middleware(CorrelationIdMiddleware)` before creating the app's routes; (2) Replace the existing `logging.basicConfig(level=logging.INFO)` call with `configure_logging()` at module top level.
**Where**: `packages/api/src/api/main.py` (2 changes)
**Depends on**: T5 (configure_logging), T7 (CorrelationIdMiddleware)
**Reuses**: Existing app construction in main.py

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [ ] Import added: `from api.middleware import CorrelationIdMiddleware`
- [ ] Import added: `from shared.logging import configure_logging`
- [ ] Module-level `logging.basicConfig(level=logging.INFO)` replaced with single line `configure_logging()`
- [ ] Middleware registered via `app.add_middleware(CorrelationIdMiddleware)` in lifespan or near app creation
- [ ] Existing test `packages/api/tests/test_main.py::DescribeLoggingSetup::it_calls_basic_config_at_info_level_on_module_import` updated to: (1) patch `shared.logging.configure_logging` instead of `logging.basicConfig`; (2) assert configure_logging was called exactly once on module reload; (3) verify handler is attached to root logger
- [ ] Integration test in test_main.py or test_middleware.py: create a TestClient(app), send a request without X-Request-ID, verify response includes X-Request-ID header with a UUID value
- [ ] Gate check passes: `uv run pytest packages/api/tests/test_main.py -v`
- [ ] Test count: existing logging test updated + 1 new middleware registration test

**Tests**: integration (update existing test + add new assertion)
**Gate**: quick

**Commit**: `feat(api): register CorrelationIdMiddleware and call configure_logging at startup`

---

### T9: Update `api/migrate.py` to call `configure_logging()`

**What**: Replace the existing `logging.basicConfig(level=logging.INFO, format=...)` call in `api/migrate.py` with `configure_logging()` to ensure migrations emit structured JSON logs.
**Where**: `packages/api/src/api/migrate.py`
**Depends on**: T5
**Reuses**: Migration runner's existing logging call site

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [ ] Import added: `from shared.logging import configure_logging`
- [ ] Existing `logging.basicConfig(...)` line replaced with `configure_logging()`
- [ ] No other changes to the migration logic
- [ ] File still runs without errors (verify by inspection; no new tests needed for config-only change)

**Tests**: none (config-only change, tested indirectly via full suite)
**Gate**: build

**Commit**: `feat(api): call configure_logging in migrate.py`

---

### T10: Add `correlation_id` field to `RequestEnvelope` and `ReimbursementEnvelope`

**What**: Add a new optional field `correlation_id: str | None = None` to both envelope models in `packages/shared/src/shared/models.py`, positioned after `published_at` and before `errors` (following the field ordering precedent).
**Where**: `packages/shared/src/shared/models.py`
**Depends on**: None (pure model change)
**Reuses**: Pydantic BaseModel, Annotated field patterns

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [ ] `RequestEnvelope` has new field: `correlation_id: str | None = None` (Optional, positioned consistently)
- [ ] `ReimbursementEnvelope` has new field: `correlation_id: str | None = None` (same shape)
- [ ] Field is placed after `published_at`, before `errors` (follows existing docstring ordering)
- [ ] Pydantic model validation succeeds (no new validators needed; plain Optional[str])
- [ ] Round-trip serialization/deserialization works: `json.dumps(envelope)` and `json.loads(..., RequestEnvelope)` both work with the field present or absent
- [ ] No type errors in mypy or the IDE
- [ ] Existing model tests still pass; no deletions

**Tests**: none (entity/schema only, tested via integration/e2e)
**Gate**: build

**Commit**: `feat(shared): add correlation_id field to RequestEnvelope and ReimbursementEnvelope`

---

### T11: Update `api/reimbursement/create/producer.py` to include `correlation_id` in byte-spliced envelope

**What**: Modify the producer's byte-splice logic to include `correlation_id` in the RequestEnvelope prefix. Update `build_envelope()` to accept a new `correlation_id: str | None` parameter, and update `publish()` to read `get_correlation_id()` and pass it to `build_envelope()`.
**Where**: `packages/api/src/api/reimbursement/create/producer.py`
**Depends on**: T3 (get_correlation_id), T10 (envelope model with field)
**Reuses**: Existing byte-splice technique (json.dumps serialization, same as `stamp` field)

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [ ] `build_envelope()` signature extended: `build_envelope(raw: bytes, published_at: datetime, correlation_id: str | None) -> bytes`
- [ ] Implementation includes: `json.dumps(correlation_id)` spliced into the prefix the same way `published_at` already is
- [ ] `publish()` internally calls `get_correlation_id()` and passes the result to `build_envelope()`
- [ ] Byte-spliced output round-trips correctly through RequestEnvelope parsing: `json.loads(prefix_bytes)` produces a RequestEnvelope with correlation_id field set
- [ ] Unit tests verify: build_envelope produces valid JSON; correlation_id serializes correctly (None → null, string → quoted); round-trip through pydantic works; concurrent requests' IDs don't mix
- [ ] Test count: at least 4 build_envelope tests (including None case and concurrent isolation)
- [ ] Gate check passes: `uv run pytest packages/api/tests/reimbursement/create/test_producer.py -v`

**Tests**: unit
**Gate**: quick

**Commit**: `feat(api): include correlation_id in byte-spliced RequestEnvelope prefix`

---

### T12: Update `publisher/processing.py` `handle_message()` to set/reset ContextVar from envelope

**What**: Wrap the post-parse body of `handle_message()` with context management for correlation_id. After parsing the envelope successfully, set the ContextVar to the envelope's correlation_id value; reset it in a finally block. Ensure concurrent messages don't bleed ContextVar values into each other (messages are processed sequentially in one coroutine, so reset is essential between iterations).
**Where**: `packages/publisher/src/publisher/processing.py`
**Depends on**: T3 (set_correlation_id, reset_correlation_id), T10 (envelope.correlation_id field)
**Reuses**: Existing handle_message control flow, existing error handling

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [ ] `handle_message()` is modified to: after successful parse, call `token = set_correlation_id(envelope.correlation_id)` before processing logic
- [ ] `finally: reset_correlation_id(token)` always runs, even on exception (so next message doesn't inherit previous one's value)
- [ ] Malformed message path (parse error before ContextVar is set) proceeds unchanged — no correlation_id available, logging proceeds without it
- [ ] Integration tests verify: message processing logs carry the envelope's correlation_id; a second message's logs don't leak the first message's correlation_id (reset works); None correlation_id from old messages doesn't crash (omits field gracefully)
- [ ] Requeue path tested separately in T13
- [ ] Test count: at least 4 handle_message tests covering good message, parse error, requeue, sequential messages
- [ ] Gate check passes: `uv run pytest packages/publisher/tests/test_processing.py -v`

**Tests**: integration (update existing test file)
**Gate**: full

**Commit**: `feat(publisher): set correlation_id ContextVar in handle_message from envelope`

---

### T13: Update `publisher/processing.py` `_requeue()` to carry forward `correlation_id`

**What**: In the requeue path where a new `RequestEnvelope(...)` is constructed, explicitly pass `correlation_id=envelope.correlation_id` to preserve the id across retry iterations. Verify in test that the requeued envelope's correlation_id matches the original (CORR-09 requirement).
**Where**: `packages/publisher/src/publisher/processing.py` (same file as T12, `_requeue()` method)
**Depends on**: T10 (envelope model), T12 (handle_message sets ContextVar)
**Reuses**: Existing model construction pattern

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [ ] `_requeue()` constructs the new envelope with explicit `correlation_id=envelope.correlation_id` parameter
- [ ] This prevents the default `None` from silently dropping the value on every requeue
- [ ] Test added: verify a requeued envelope's correlation_id matches the original (integration test, part of T12's suite)
- [ ] No behavior change to the requeue logic itself; only the one parameter is added to the constructor call

**Tests**: integration (included in T12 test updates)
**Gate**: full

**Commit**: `feat(publisher): carry forward correlation_id on requeue`

---

### T14: Update `reimbursement/validation.py` `handle_message()` to set/reset ContextVar from envelope

**What**: Apply the same ContextVar set/reset pattern to `reimbursement/validation.py`'s `handle_message()` as T12 does for publisher. After parsing the envelope, set ContextVar to envelope.correlation_id; reset in finally.
**Where**: `packages/reimbursement/src/reimbursement/validation.py`
**Depends on**: T3 (set_correlation_id, reset_correlation_id), T10 (envelope.correlation_id field)
**Reuses**: Existing handle_message control flow

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [ ] `handle_message()` modified to set/reset ContextVar the same way as T12
- [ ] After successful parse, call `token = set_correlation_id(envelope.correlation_id)` before processing
- [ ] `finally: reset_correlation_id(token)` always runs
- [ ] Malformed message path (parse error) proceeds unchanged
- [ ] Integration tests verify: message processing logs carry correlation_id; sequential messages don't leak; None correlation_id omits field gracefully
- [ ] Test count: at least 3 validation-specific tests (good message, parse error, sequential isolation)
- [ ] Gate check passes: `uv run pytest packages/reimbursement/tests/test_validation.py -v`

**Tests**: integration (update existing test file)
**Gate**: full

**Commit**: `feat(reimbursement): set correlation_id ContextVar in handle_message from envelope`

---

### T15: Update `publisher/consumer.py` and `reimbursement/consumer.py` to call `configure_logging()`

**What**: Replace the existing `logging.basicConfig(...)` calls in both consumer startup files with `configure_logging()`.
**Where**: `packages/publisher/src/publisher/consumer.py` and `packages/reimbursement/src/reimbursement/consumer.py` (2 files, same change applied)
**Depends on**: T5
**Reuses**: Existing logging call sites

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [ ] Import added to both files: `from shared.logging import configure_logging`
- [ ] Existing `logging.basicConfig(...)` line replaced with `configure_logging()` in both files
- [ ] No other changes to either file
- [ ] Both services still start without errors (verified by inspection)

**Tests**: none (config-only changes)
**Gate**: build

**Commit**: `feat(publisher,reimbursement): call configure_logging at consumer startup`

---

### T16: Update `reimbursement/agent/agent.py` to add `correlation_id` to LangFuse metadata

**What**: In the `decide()` function, extend the `metadata` dict that is passed to the LangGraph invocation to include `"correlation_id"` when available. Read the value from the current process's correlation_id ContextVar; omit the key (not pass None) if unavailable.
**Where**: `packages/reimbursement/src/reimbursement/agent/agent.py`, `decide()` function (~lines 140-156)
**Depends on**: T3 (get_correlation_id)
**Reuses**: Existing metadata dict construction (already has `langfuse_session_id`), _langfuse_handlers() pattern

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [ ] `decide()`'s signature is **unchanged** (CORR-14: no new parameters)
- [ ] Inside decide(), after `metadata = {"langfuse_session_id": str(reimbursement.uuid)}`, add: `if (cid := get_correlation_id()) is not None: metadata["correlation_id"] = cid`
- [ ] Metadata dict is passed to LangGraph invocation as before
- [ ] Unit tests verify: metadata includes both keys when correlation_id is available; metadata includes only langfuse_session_id when correlation_id is None; config["configurable"]["metadata"] is correctly wired
- [ ] Test count: at least 2 metadata dict tests (with and without correlation_id)
- [ ] Gate check passes: `uv run pytest packages/reimbursement/tests/agent/test_agent.py -v`

**Tests**: unit (update existing agent test file)
**Gate**: quick

**Commit**: `feat(reimbursement): add correlation_id to LangFuse metadata`

---

### T17: Add "Logging" section to root `CLAUDE.md`

**What**: Document this feature's conventions in the root `CLAUDE.md` so future contributors follow the logging and correlation-id patterns consistently. Document: calling `configure_logging()` at every service entrypoint, the LOG_LEVEL env var and its default/fallback behavior, and the correlation_id propagation chain (HTTP header → ContextVar → Kafka envelope field → LangFuse metadata).
**Where**: `/CLAUDE.md` (root repo file)
**Depends on**: All prior tasks (implementation must be complete before documenting conventions)
**Reuses**: Existing CLAUDE.md structure and conventions

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [ ] New "Logging" section added to CLAUDE.md (after existing sections, before or after as makes sense contextually)
- [ ] Section covers: (1) `configure_logging()` is called once at every service entrypoint (api/main.py, api/migrate.py, publisher/consumer.py, reimbursement/consumer.py); (2) `LOG_LEVEL` env var (default: "debug", fallback on invalid); (3) correlation_id propagation chain documented end-to-end with field names at each step
- [ ] Examples provided for how new services or integrations should follow the same patterns
- [ ] No merge conflicts with existing content; formatting consistent with rest of file
- [ ] File parses cleanly (no syntax errors)

**Tests**: none (documentation only)
**Gate**: build (markdown lint, if any)

**Commit**: `docs(root): add Logging section to CLAUDE.md conventions`

---

## Phase Execution Map

Visual representation of task ordering. Phases run in sequence, and tasks within a phase run in order:

```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5

Phase 1:  T1 ──→ T2 ──→ T3 ──→ T4 ──→ T5 ──→ T6
Phase 2:  T7 ──→ T8 ──→ T9
Phase 3:  T10 ──→ T11
Phase 4:  T12 ──→ T13 ──→ T14 ──→ T15
Phase 5:  T16 ──→ T17
```

Execution is strictly sequential — there is no intra-phase parallelism. A single agent (or batch worker) works one task at a time, in order.

---

## Task Granularity Check

Before approving tasks, verify they are granular enough:

| Task | Scope | Depends On | Status |
| ---- | ----- | ---------- | ------ |
| T1: Add ecs-logging dependency | 1 file, 1 line | None | ✅ Granular |
| T2: Create LoggingConfig + extend load_config() | 1 file, 2-3 locations | T1 | ✅ Granular |
| T3: Implement ContextVar + accessors | 1 file, 3 functions | T2 | ✅ Granular |
| T4: Implement CorrelationIdFilter | 1 file, 1 class | T3 | ✅ Granular |
| T5: Implement configure_logging() | 1 file, 1 function | T1-T4 | ✅ Granular |
| T6: Refactor log_event() | 1 file, 1 function + 6 call sites | T5 | ✅ Granular |
| T7: Create CorrelationIdMiddleware | 1 file, 1 class | T3 | ✅ Granular |
| T8: Register middleware + call configure_logging() | 1 file, 2 changes + test update | T5, T7 | ✅ Granular |
| T9: Update migrate.py | 1 file, 1 line | T5 | ✅ Granular |
| T10: Add correlation_id to envelopes | 1 file, 2 fields | None | ✅ Granular |
| T11: Update producer byte-splice | 1 file, 2 functions | T3, T10 | ✅ Granular |
| T12: Update publisher handle_message() | 1 file, 1 function + tests | T3, T10 | ✅ Granular |
| T13: Update publisher _requeue() | 1 file, 1 function | T10, T12 | ✅ Granular |
| T14: Update reimbursement handle_message() | 1 file, 1 function + tests | T3, T10 | ✅ Granular |
| T15: Update consumer.py files | 2 files, 1 line each | T5 | ✅ Granular |
| T16: Update agent metadata | 1 file, 1 function | T3 | ✅ Granular |
| T17: Add CLAUDE.md section | 1 file, 1 new section | All | ✅ Granular |

**Granularity validation**: All tasks are ≤2 files and 1-3 functions/changes. Each represents one deliverable per the tlc-spec-driven definition.

---

## Diagram-Definition Cross-Check

Verify the execution diagram matches every task's `Depends on` field:

| Task | Depends On (task body) | Diagram Shows | Status |
| ---- | ---------------------- | ------------- | ------ |
| T1 | None | No predecessors | ✅ Match |
| T2 | T1 | T1 → T2 | ✅ Match |
| T3 | T2 | T2 → T3 | ✅ Match |
| T4 | T3 | T3 → T4 | ✅ Match |
| T5 | T1-T4 | T4 → T5 | ✅ Match (T1-T4 all complete before T5) |
| T6 | T5 | T5 → T6 | ✅ Match |
| T7 | T3 | Starts Phase 2 after Phase 1 complete | ✅ Match |
| T8 | T5, T7 | T7 → T8 | ✅ Match |
| T9 | T5 | T8 → T9 | ✅ Match |
| T10 | None | Starts Phase 3 after Phase 2 complete | ✅ Match |
| T11 | T3, T10 | T10 → T11 | ✅ Match |
| T12 | T3, T10 | Starts Phase 4 after Phase 3 complete | ✅ Match |
| T13 | T10, T12 | T12 → T13 | ✅ Match |
| T14 | T3, T10 | T13 → T14 | ✅ Match |
| T15 | T5 | T14 → T15 | ✅ Match |
| T16 | T3 | Starts Phase 5 after Phase 4 complete | ✅ Match |
| T17 | All | T16 → T17 | ✅ Match |

**Cross-check result**: All dependencies and diagram arrows align. No task depends on a later phase; no forward dependencies.

---

## Test Co-location Validation

Verify EVERY task's `Tests` field is consistent with the **Test Coverage Matrix** generated above:

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| ---- | --------------------------- | --------------- | --------- | ------ |
| T1 | pyproject.toml | none | none | ✅ OK |
| T2 | shared.config.LoggingConfig | unit | unit | ✅ OK |
| T3 | shared.logging (ContextVar functions) | unit | unit | ✅ OK |
| T4 | shared.logging.CorrelationIdFilter | unit | unit | ✅ OK |
| T5 | shared.logging.configure_logging() | unit | unit | ✅ OK |
| T6 | shared.logging.log_event() | unit | unit + integration | ✅ OK (integration via existing call sites) |
| T7 | api.middleware.CorrelationIdMiddleware | unit + integration | unit + integration | ✅ OK |
| T8 | api.main, api.middleware wiring | integration | integration | ✅ OK |
| T9 | api.migrate.py | none | none | ✅ OK |
| T10 | shared.models (RequestEnvelope, ReimbursementEnvelope) | none | none | ✅ OK |
| T11 | api.reimbursement.create.producer | unit | unit | ✅ OK |
| T12 | publisher.processing.handle_message() | integration | integration | ✅ OK |
| T13 | publisher.processing._requeue() | integration | integration (combined with T12) | ✅ OK |
| T14 | reimbursement.validation.handle_message() | integration | integration | ✅ OK |
| T15 | publisher.consumer, reimbursement.consumer | none | none | ✅ OK |
| T16 | reimbursement.agent.agent.decide() | unit | unit | ✅ OK |
| T17 | CLAUDE.md | none | none | ✅ OK |

**Test co-location result**: All tasks include required test types per the coverage matrix. No task defers testing to a later task. Test coverage aligns with code layers.

---

## Tips

- **Phases are ordered** — Each phase completes before the next; tasks run in order within a phase
- **Reuses = Token saver** — Always reference existing code (logging patterns, config loaders, model construction)
- **Tools per task** — Mostly filesystem; context7 optional for ecs-logging API confirmation
- **Dependencies are gates** — T2 gates on T1; T5 gates on T1-T4; etc.
- **Done when = Testable** — Each task's "Done when" list is binary pass/fail
- **Requirement ID = Traceable** — Every task maps to spec ACs (LOG-01..08, CORR-01..14, DOC-01)
- **One commit per task** — Conventional commit format, scoped by package/subsystem
- **Gate commands are definitive** — Use them, not "run tests in my head"

---

## Task Verification Standards

Every task MUST follow the `Done when` + `Tests` + `Gate` fields defined above. Each `Done when` entry is specific, testable (binary pass/fail), and references the gate check command. Test counts are included to prevent silent deletions.

**Sub-agent execution note**: If this feature is packed into > 1 task-budgeted batch, the orchestrator will offer sub-agents. Each worker executes its assigned phase-batch in order, reports a compact summary (tasks done, commits, test counts), then the next worker starts. Batches run sequentially — the Verifier does not run until all tasks in all batches are complete. See references/sub-agents.md for the full worker contract.
