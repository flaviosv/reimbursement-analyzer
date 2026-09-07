# Adding Tracing Support Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user — do not proceed without it.**

---

**Design**: `.specs/features/RA-2-adding-tracing-support/design.md`
**Status**: Draft

---

## Test Coverage Matrix

> Generated from codebase sampling and project guidelines in `docs/codebase/TESTING.md` and `CONVENTIONS.md`. Guidelines found: `docs/codebase/TESTING.md::Test Frameworks`, `docs/codebase/TESTING.md::Test Organization`, `docs/codebase/CONVENTIONS.md::Code Organization`.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------ | -------------------- | ---------------- | ----------- |
| Tracing module (`shared.tracing`, inject/extract helpers) | Unit | All functions tested: `init_tracer`, `shutdown_tracer`, `inject_headers`, `extract_context`, `traced_message_span` — span creation, attribute presence, header serialization/deserialization; edge case: message with no headers extracts to empty context | `packages/shared/tests/test_tracing.py` | `uv run pytest -m "not integration and not e2e"` |
| Config extension (`shared.config.TracingConfig`) | Unit | Config loading (`load_config()` reads `OTEL_EXPORTER_OTLP_ENDPOINT`, defaults correctly), env fallback, type safety | `packages/shared/tests/test_config.py` (extend existing) | `uv run pytest -m "not integration and not e2e"` |
| Producer update (`shared.producer.publish` with header injection) | Unit | Produces to sync path, injects headers on every call, preserves error handling (raises `PublishFailed` on timeout/broker error), maintains backward-compatible signature | `packages/shared/tests/test_producer.py` (rewrite fakes) | `uv run pytest -m "not integration and not e2e"` |
| API instrumentation (`api/main.py` tracer init/shutdown, FastAPIInstrumentor) | Unit (via TestClient) | Lifespan hooks execute, tracer provider lifecycle, no startup errors when APM unreachable; integration: tracer is global and discoverable via `trace.get_tracer` | `packages/api/tests/test_main.py` (extend existing) | `uv run pytest -m "not integration and not e2e"` |
| API route span enrichment (GET/PUT uuid stamping) | Unit (via TestClient) | Both routes stamp `reimbursement.uuid` attribute; verify with `trace.get_current_span()` mock or in-memory exporter | `packages/api/tests/reimbursement/{get,update}/test_route.py` (extend existing) | same |
| Publisher consumer span wrapper | Unit | Message with headers extracts context and starts child span; message without headers starts root span; span includes topic/partition/offset attributes; uuid stamped once known | `packages/publisher/tests/test_consumer.py` (extend existing) | `uv run pytest -m "not integration and not e2e"` |
| Reimbursement consumer span wrapper | Unit | Same as publisher: extraction, child/root span, attributes, uuid stamping | `packages/reimbursement/tests/test_consumer.py` (extend existing) | same |
| End-to-end message routing (api → publisher → reimbursement trace context propagation) | Integration (real Kafka via testcontainers) | Single trace ID across all three services' spans; header injection/extraction round-trips correctly through Kafka; uuid and topic/partition/offset attributes present | `packages/api/tests/reimbursement/create/test_integration.py` and/or `packages/publisher/tests/test_integration.py` (extend) | `uv run pytest` (includes containers) |
| Real-stack end-to-end (e2e, optional) | E2E (`@pytest.mark.e2e`) | Real `POST /api/v1/reimbursement` against docker-compose stack, spans land in Elastic APM Server, trace ID queryable | `tests/e2e/test_tracing_e2e.py` (new, optional — can be built post-feature as a separate doc/verification suite) | `uv run pytest -m e2e` |

**Coverage Expectation values** — justified by spec acceptance criteria (P1 AC1–8 and P2 AC1–3 are testable at unit/integration scope; P3 is hygiene/repository work, tested by inspection not executable tests). Existing patterns confirm depth: `test_producer.py` uses fake producers for header/delivery testing without live brokers; `test_consumer.py` tests message handling wrapping with fakes.

## Gate Check Commands

> Generated from codebase root `pyproject.toml` and observed test execution patterns.

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | After each task with unit or route-level tests only (no Kafka containers) | `uv run pytest -m "not integration and not e2e"` |
| Full | After all tasks complete; before PR review | `uv run pytest` (includes container-backed integration tests; excludes e2e unless explicitly requested) |
| E2E | After full gate passes; verification that traces land in real APM Server (optional, separate verification task) | `uv run pytest -m e2e` |

---

## Execution Plan

Phases are ordered and run sequentially — each phase completes before the next begins, and tasks within a phase execute in order.

### Phase 1: Foundation — Dependencies & Config

Tasks establishing OTel infrastructure and configuration.

```
T1 → T2 → T3
```

### Phase 2: Core Tracing Infrastructure

Building `shared.tracing` and updating `shared.producer`.

```
T4 → T5 → T6
```

### Phase 3: API Service Integration

Instrumenting the FastAPI service.

```
T7 → T8
```

### Phase 4: Publisher Service Integration

Instrumenting Kafka message processing in publisher.

```
T9 → T10
```

### Phase 5: Reimbursement Service Integration

Instrumenting Kafka message processing in reimbursement.

```
T11 → T12
```

### Phase 6: Test Rewrites & Repository Hygiene

Updating test fakes, removing stale docs, recording decisions.

```
T13 → T14 → T15
```

---

## Task Breakdown

### T1: Add OTel Dependencies to Package `pyproject.toml` Files

**What**: Add `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http` (current stable, >=1.44.0 for both) to `packages/shared/pyproject.toml` dependencies; add `opentelemetry-instrumentation-fastapi` (>=0.65b0) to `packages/api/pyproject.toml` only. Verify all three are compatible with Python 3.14.7 (no upper-bound version constraints).

**Where**: `packages/shared/pyproject.toml`, `packages/api/pyproject.toml`

**Depends on**: None

**Reuses**: Existing `pyproject.toml` dependency-declaration pattern (e.g., `pydantic>=2.0.0`, never `==`); existing Python version constraint.

**Requirement**: OTEL-12

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [x] `packages/shared/pyproject.toml` declares `opentelemetry-sdk>=1.44.0` and `opentelemetry-exporter-otlp-proto-http>=1.44.0` in `dependencies`
- [x] `packages/api/pyproject.toml` declares `opentelemetry-instrumentation-fastapi>=0.65b0` in `dependencies`
- [x] `uv sync` succeeds without version conflicts
- [x] No other packages modified
- [x] No unexpected transitive dependency tree changes (verified by reviewing `uv lock` diff if it changes size)

**Tests**: none (dependency declarations are not executable code; verified by build gate)

**Gate**: build (quick gate command applies; build phase includes dependency resolution)

---

### T2: Extend `shared.config` with `TracingConfig`

**What**: Add a new `TracingConfig` dataclass to `shared/src/shared/config.py` following the pattern of `KafkaConfig` and `DatabaseConfig`. Read `OTEL_EXPORTER_OTLP_ENDPOINT` from env; default to `http://apm-server.shared-services.svc.cluster.local:8200`. Extend the top-level `Config` dataclass with a `tracing: TracingConfig` field. Extend the existing `load_config()` function to initialize `tracing`.

**Where**: `packages/shared/src/shared/config.py`

**Depends on**: T1 (dependencies must be available)

**Reuses**: `Config`, `KafkaConfig`, `DatabaseConfig` patterns; existing `load_config()` function body.

**Requirement**: OTEL-12

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [x] `TracingConfig` dataclass created with `otlp_endpoint: str` field, `@dataclass(frozen=True)`
- [x] `Config` dataclass includes `tracing: TracingConfig` field
- [x] `load_config()` reads `OTEL_EXPORTER_OTLP_ENDPOINT` env var and defaults correctly
- [x] Existing unit tests (`test_config.py`) still pass
- [x] New test: `DescribeTracingConfig` test class added to `packages/shared/tests/test_config.py` with `it_defaults_otlp_endpoint_when_unset` and `it_reads_otlp_endpoint_from_env`
- [x] Quick gate passes: `uv run pytest -m "not integration and not e2e"` passes

**Tests**: unit

**Gate**: quick

---

### T3: Create `shared.tracing` Module with OTel Helpers

**What**: Create `packages/shared/src/shared/tracing.py` with four functions:
1. `init_tracer(service_name: str, otlp_endpoint: str) -> TracerProvider` — builds Resource, OTLPSpanExporter (with explicit `/v1/traces` suffix), BatchSpanProcessor, sets global provider.
2. `shutdown_tracer(provider: TracerProvider) -> None` — calls `provider.shutdown()`.
3. `inject_headers() -> list[tuple[str, bytes]]` — current context → dict → encode as bytes tuples (Kafka wire format).
4. `extract_context(headers: list[tuple[str, bytes]] | None) -> Context` — Kafka headers → dict → OTel context (empty headers → empty context).
5. `traced_message_span(tracer: Tracer, message: Message, span_name: str = "process_message") -> AbstractContextManager[Span]` — context manager wrapping message handling: extract headers, start span with topic/partition/offset attributes.

Import `opentelemetry.trace`, `opentelemetry.sdk.trace`, `opentelemetry.sdk.trace.export`, `opentelemetry.exporter.otlp.proto.http.trace_exporter`, `opentelemetry.propagate`.

**Where**: `packages/shared/src/shared/tracing.py` (new file)

**Depends on**: T2 (config must be importable); T1 (dependencies must be available)

**Reuses**: none (genuinely new module)

**Requirement**: OTEL-01, OTEL-04, OTEL-05, OTEL-09

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [x] `init_tracer` creates Resource with `service.name`, OTLPSpanExporter pointed at `{otlp_endpoint}/v1/traces`, BatchSpanProcessor registered on a TracerProvider, provider returned and set globally
- [x] `shutdown_tracer` calls `provider.shutdown()` — no raise on already-shutdown provider
- [x] `inject_headers` uses `propagate.inject(carrier)` against current context, returns bytes-tuple list
- [x] `extract_context` deserializes bytes-tuple headers, uses `propagate.extract(carrier)`, returns Context (empty list → empty context, no parent span)
- [x] `traced_message_span` is a context manager (via `@contextmanager` or `AsyncExitStack`); extracts context from message headers, starts span with name, attributes (topic, partition, offset)
- [x] Unit tests in `packages/shared/tests/test_tracing.py`: DescribeInitTracer, DescribeShutdownTracer, DescribeInjectHeaders, DescribeExtractContext, DescribeTracedMessageSpan — each with multiple `it_*` test methods covering happy path and edge cases (empty headers, None headers)
- [x] Tests use `InMemorySpanExporter` for assertion without live APM Server
- [x] Quick gate passes

**Tests**: unit

**Gate**: quick

---

### T4: Update `shared.producer.publish` to Use Sync Producer Path & Inject Headers

**What**: Rewrite `publish()` in `packages/shared/src/shared/producer.py` to: (1) call `shared.tracing.inject_headers()` to get trace-context headers; (2) bypass `AIOProducer.produce()` (which raises `NotImplementedError` for headers at batch_size=1), instead drive `producer._producer` (sync) and `producer.executor` (ThreadPoolExecutor) directly via `loop.run_in_executor`; (3) call sync `produce()` with headers, then `flush()`, in the executor thread; (4) raise `PublishFailed` on any error or timeout, preserving today's error contract. Signature unchanged at call sites; inject happens unconditionally inside the function.

**Where**: `packages/shared/src/shared/producer.py` (modify `publish` function body)

**Depends on**: T3 (tracing helpers must exist); T1 (dependencies must be available)

**Reuses**: `AIOProducer.executor`, `AIOProducer._producer` (private attributes, per design risk mitigation); `PublishFailed` exception.

**Requirement**: OTEL-04

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [x] `publish()` internally calls `shared.tracing.inject_headers()` unconditionally
- [x] `publish()` uses `loop.run_in_executor(producer.executor, sync_produce_and_flush)` to drive `producer._producer.produce(headers=...)` and `.flush(...)`
- [x] Error handling unchanged: `PublishFailed` raised on delivery failure or timeout
- [x] Call sites (api route, publisher processing, reimbursement validation requeue) require zero changes — signature backward-compatible
- [x] Guard/smoke test in `packages/shared/tests/test_producer.py`: verify `hasattr(producer, "executor")` and `isinstance(producer._producer, confluent_kafka.Producer)` against real `managed_producer()`
- [x] All existing `test_producer.py` tests rewritten (see T13)
- [x] Quick gate passes

**Tests**: unit (fake producer must be rewritten; see T13)

**Gate**: quick (once T13 fakes are ready)

---

### T5: Rewrite Test Fakes in `shared/tests/test_producer.py`

**What**: Replace `_ImmediateFakeProducer`, `_NeverResolvesFakeProducer`, `_OutOfOrderFakeProducer` with new fakes shaped like the sync `Producer` / executor pattern. New fakes must model: `._producer` (a fake sync `Producer` with `.produce(topic, value, headers, on_delivery)` and `.flush(timeout_seconds)`) and `.executor` (a `ThreadPoolExecutor` or mock executor). Preserve all four behavioral guarantees: (1) immediate resolution on ack, (2) raise on broker error, (3) raise on timeout, (4) per-call isolation under concurrent out-of-order resolution.

**Where**: `packages/shared/tests/test_producer.py` (rewrite fake producer classes and all four `DescribePublish::it_*` test methods)

**Depends on**: T4 (new producer implementation must be understood)

**Reuses**: Test behavioral contracts (four guarantees) already enforced by existing test names.

**Requirement**: (implicit quality gate for T4)

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [x] New `_FakeSyncProducer` class models `produce()` and `flush()` with on-delivery callback
- [x] New `_FakeExecutor` or mock executor can be injected
- [x] All four existing test methods (`it_raises_on_broker_error`, `it_raises_on_timeout`, `it_resolves_on_ack`, `it_isolates_concurrent_calls` — names inferred from existing test structure) rewritten to use new fakes and still pass
- [x] Test coverage: header injection verified (new assertion: `assert headers in captured_produce_call`)
- [x] All existing assertions still hold (PublishFailed contract, error message content)
- [x] Quick gate passes

**Tests**: unit

**Gate**: quick

---

### T6: Verify Producer & Config Tests Pass Together

**What**: Run quick gate on T4 and T5 changes together to confirm no surprises when `shared.tracing`, `shared.config`, and `shared.producer` are all integrated.

**Where**: N/A (verification task)

**Depends on**: T1, T2, T3, T4, T5 (all prior foundation work)

**Reuses**: existing test patterns

**Requirement**: (quality gate)

**Tools**:
- MCP: (tests only)
- Skill: NONE

**Done when**:

- [x] `uv run pytest packages/shared/tests/test_tracing.py packages/shared/tests/test_config.py packages/shared/tests/test_producer.py -m "not integration and not e2e"` — all pass, no flakes
- [x] No import errors between modules

**Tests**: none (integration of existing tests)

**Gate**: quick

---

### T7: Instrument `api/main.py` — TracerProvider Init/Shutdown & FastAPIInstrumentor

**What**: In `packages/api/src/api/main.py`, (1) at module scope after logging setup and before `app = FastAPI(...)`, initialize tracer: `_tracer_provider = init_tracer("reimbursement-analyzer-api", load_config().tracing.otlp_endpoint)`; (2) immediately after `app = FastAPI(lifespan=lifespan)`, call `FastAPIInstrumentor.instrument_app(app)` exactly once; (3) in the `lifespan` context manager, after `yield` and before exiting the innermost `async with` block, call `shutdown_tracer(_tracer_provider)`.

**Where**: `packages/api/src/api/main.py`

**Depends on**: T3 (tracing module), T2 (config), T1 (FastAPI instrumentation dependency)

**Reuses**: existing `lifespan` structure, `load_config()`, existing imports.

**Requirement**: OTEL-01, OTEL-02, OTEL-03

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [x] `init_tracer` called at module scope, `_tracer_provider` assigned
- [x] `FastAPIInstrumentor.instrument_app(app)` called exactly once, immediately after app creation
- [x] `shutdown_tracer(_tracer_provider)` called in lifespan after `yield`, still inside the managed `async with` block (matches AC2 wording exactly)
- [x] No per-route span creation code added (AC3 scope-out respected)
- [x] Existing `test_main.py` extended with `DescribeTracingIntegration::it_initializes_tracer_on_startup` (verifies `_tracer_provider` is set), `it_shuts_down_tracer_on_shutdown` (mock shutdown call), `it_instruments_app_with_fastapi_instrumentor` (mock verify instrumentation)
- [x] Route-level tests (test_route.py for GET/PUT) still pass with new spans auto-created
- [x] No startup errors when APM Server unreachable (verify by temporarily setting bad endpoint in a test env override)
- [x] Quick gate passes

**Tests**: unit (via TestClient mocks for tracer provider)

**Gate**: quick

---

### T8: Stamp `reimbursement.uuid` on API GET & PUT Spans

**What**: In `packages/api/src/api/reimbursement/get/route.py` (`get_reimbursement_by_uuid`) and `packages/api/src/api/reimbursement/update/route.py` (the PUT handler), add one line at the start of each handler body: `trace.get_current_span().set_attribute("reimbursement.uuid", str(uuid))`. Import `from opentelemetry import trace` at the top of each file.

**Where**: `packages/api/src/api/reimbursement/get/route.py`, `packages/api/src/api/reimbursement/update/route.py` (each gets one line)

**Depends on**: T7 (tracer must be initialized so spans exist)

**Reuses**: existing route signature (uuid already a parameter), existing function bodies unchanged.

**Requirement**: OTEL-10

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [x] Both files import `from opentelemetry import trace`
- [x] GET handler sets attribute before any other logic
- [x] PUT handler sets attribute before any other logic
- [x] Existing route tests (test_route.py for both) still pass
- [x] New test assertions in existing test files: mock `trace.get_current_span()` and verify `.set_attribute("reimbursement.uuid", str(uuid))` was called with the correct uuid
- [x] Quick gate passes

**Tests**: unit (via TestClient mock verification of span attribute)

**Gate**: quick

---

### T9: Instrument `publisher/_serve()` — TracerProvider Init/Shutdown

**What**: In `packages/publisher/src/publisher/app.py` (or equivalent `_serve()` location), add (1) `_tracer_provider = init_tracer("reimbursement-analyzer-publisher", config.tracing.otlp_endpoint)` near the top, after `config = load_config()`; (2) `shutdown_tracer(_tracer_provider)` right after `await run(deps, consumer, stopping)` returns, still inside the `async with` block managing producer/consumer/pool resources (matches AC2 exactly).

**Where**: `packages/publisher/src/publisher/app.py` (or equivalent `_serve()` function)

**Depends on**: T3 (tracing module), T2 (config)

**Reuses**: existing `_serve()` structure, existing `async with` block.

**Requirement**: OTEL-01, OTEL-02

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [x] `init_tracer("reimbursement-analyzer-publisher", config.tracing.otlp_endpoint)` called, result stored
- [x] `shutdown_tracer(provider)` called after `await run()` returns, inside managed `async with` block
- [x] Existing unit tests (test_consumer.py, test_integration.py) still pass
- [x] No startup errors when APM Server unreachable
- [x] Quick gate passes

**Tests**: unit (existing consumer tests still pass; new tracer initialization test similar to T7)

**Gate**: quick

---

### T10: Wrap Publisher Message Processing in Traced Span

**What**: In `packages/publisher/src/publisher/consumer.py`'s `run()` function, wrap the message handling section (`error = message.error()` through `await consumer.commit(...)`) inside `with traced_message_span(tracer, message):`. The span wraps existing logic without changing order or control flow. Also, in `publisher/processing.py`'s `handle_message` function, add one line once the `reimbursement.uuid` is extracted from the envelope: `trace.get_current_span().set_attribute("reimbursement.uuid", str(uuid))`. Get the tracer via `trace.get_tracer(__name__)` in consumer.py.

**Where**: `packages/publisher/src/publisher/consumer.py` (modify run loop), `packages/publisher/src/publisher/processing.py` (add attribute stamp)

**Depends on**: T3 (traced_message_span helper), T9 (tracer initialized)

**Reuses**: existing message handling logic, existing handle_message signature.

**Requirement**: OTEL-05, OTEL-06, OTEL-09, OTEL-10

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [x] `run()` loop wraps message handling (from `error = message.error()` to `await consumer.commit(...)`) inside `with traced_message_span(tracer, message):`, no change to logic
- [x] Span name is "process_message"
- [x] Span includes attributes: `messaging.kafka.topic`, `messaging.kafka.partition`, `messaging.kafka.offset` (from message object)
- [x] `processing.handle_message` adds `trace.get_current_span().set_attribute("reimbursement.uuid", str(uuid))` once uuid is known
- [x] Existing unit tests (test_consumer.py, test_processing.py) still pass
- [x] New test in test_consumer.py: `DescribeTracedMessageProcessing::it_wraps_handling_in_span` (mock tracer, verify span was started and stopped)
- [x] New test: verify span attributes are set from message (topic/partition/offset)
- [x] New test in test_processing.py: verify uuid attribute is stamped on current span
- [x] Integration test (test_integration.py): message round-trip with header injection (from api via Kafka) extracts context and creates child span
- [x] Quick gate passes

**Tests**: unit + integration

**Gate**: quick

---

### T11: Wrap Reimbursement Message Processing in Traced Span

**What**: In `packages/reimbursement/src/reimbursement/consumer.py`'s `run()` function, wrap message handling in `with traced_message_span(tracer, message):`, same as publisher. In `reimbursement/validation.py`'s `handle_message` function, add `trace.get_current_span().set_attribute("reimbursement.uuid", str(uuid))` once uuid is extracted.

**Where**: `packages/reimbursement/src/reimbursement/consumer.py` (modify run loop), `packages/reimbursement/src/reimbursement/validation.py` (add attribute)

**Depends on**: T3 (traced_message_span), T8 (tracer initialized in `_serve()` — T8 is publisher/reimbursement parallel work, both depend on T3)

**Reuses**: existing message handling, existing handle_message signature.

**Requirement**: OTEL-05, OTEL-06, OTEL-09, OTEL-10, OTEL-11

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [x] `run()` loop wraps message handling inside `with traced_message_span(tracer, message):`, no logic changes
- [x] Span includes topic/partition/offset attributes
- [x] `validation.handle_message` adds uuid attribute once extracted
- [x] Existing unit tests (test_consumer.py, test_validation.py, test_integration.py) still pass
- [x] New tests: same as T10 (span wrapping, attribute presence, integration round-trip)
- [x] LangGraph decision graph (agent.py) continues to emit `langfuse_session_id` unchanged; it now runs inside an already-active OTel span (P2 AC3)
- [x] Quick gate passes

**Tests**: unit + integration

**Gate**: quick

---

### T12: Full Integration Test — Trace Context Propagation (api → publisher → reimbursement)

**What**: Extend or create an integration test that submits a real `POST /api/v1/reimbursement` request through the real local Kafka stack (via testcontainers) and confirms: (1) the same trace ID appears in spans from all three services; (2) header injection happens on publish (api → publisher); (3) header extraction happens on consume (publisher receives context, starts child span, republishes to reimbursement); (4) reimbursement consumes with extracted context, starts child span; (5) span attributes (reimbursement.uuid, topic/partition/offset) are present. Use `InMemorySpanExporter` on all three services' tracer providers for assertions (or query a shared exporter configured during test setup).

**Where**: New or extended integration test, likely `packages/api/tests/reimbursement/create/test_integration.py` or a separate coordination test across packages

**Depends on**: T7, T8, T10, T11 (all three services instrumented)

**Reuses**: existing testcontainers Kafka setup, existing payload builders.

**Requirement**: OTEL-07, OTEL-08 (full lifecycle with correct attributes)

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [x] Test creates real Kafka testcontainer
- [x] Test submits real POST request through the three-service chain
- [x] All three services configured to export spans to a shared `InMemorySpanExporter`
- [x] Assertions: single trace ID across all spans; child spans correctly linked via extracted context; spans with no headers start as root (P1 AC8)
- [x] Attributes present: reimbursement.uuid on all three, topic/partition/offset on publisher and reimbursement
- [x] Full gate passes: `uv run pytest` (includes this integration test)

**Tests**: integration

**Gate**: full

---

### T13: Remove `docker-compose.yml` and Update `README.md`

**What**: Delete `docker-compose.yml` from the repo root. Update `README.md`: remove the entire `## Run` section (stack startup, service port table, health-check, `docker compose down -v` command); remove from "Database migrations" section the line "Applying them happens automatically on `docker compose up`"; remove from "End-to-end suite" section the `docker compose up -d` step and its "stack must already be up" framing.

**Where**: `docker-compose.yml` (delete), `README.md` (modify)

**Depends on**: None (repository hygiene, can happen anytime; scheduled after implementation for clarity)

**Reuses**: Nothing.

**Requirement**: OTEL-14

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [x] `docker-compose.yml` deleted (confirmed via `git status` or `git diff`)
- [x] `README.md` no longer contains `## Run` section
- [x] Database migrations section no longer mentions docker-compose automatic application
- [x] End-to-end suite section no longer instructs `docker compose up -d`
- [x] `git grep "docker-compose" README.md` returns nothing (no stray references in README)
- [x] `git grep "docker-compose" .` (excluding `.specs/`) returns nothing or only spec/decision record references
- [x] No other files updated (e.g., CI workflows still refer to it if they do — flagged but out-of-scope per spec)

**Tests**: none (documentation/repository work, verified by content inspection)

**Gate**: build (quick gate sufficient)

---

### T14: Update `.specs/STATE.md` with New Decision AD-040

**What**: In `.specs/STATE.md`'s `## Decisions` section, append a new decision `AD-040` superseding `AD-038` (the docker-compose convention). Record: "Decision AD-038 (docker-compose.yml establishment) is superseded by the removal of docker-compose.yml per OTEL-14 (P3 AC3). The file is deleted; no docker-compose-based convention applies going forward. Local development/verification uses the sibling `local-env` project's k3s/Tilt setup (documented in CLAUDE.md, not an instruction for general developers)."

**Where**: `.specs/STATE.md` (Decisions section, append at end)

**Depends on**: T13 (docker-compose.yml must be deleted before decision is recorded)

**Reuses**: existing AD-NNN format.

**Requirement**: (implicit; decision hygiene)

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [x] `AD-040` appended to Decisions section
- [x] `AD-038` is unchanged but now explicitly superseded
- [x] Text is clear and concise (2–3 lines max)

**Tests**: none (decision record, verified by inspection)

**Gate**: none (metadata; no build gate)

---

### T15: Update `CLAUDE.md` with Standing OTel Instrumentation Directive

**What**: Append a new section to this repo's `CLAUDE.md` under "Project-Specific Requirements" (or similar) with the standing directive: "**Distributed Tracing — Standing Requirement**: `api`, `publisher`, and `reimbursement` are always instrumented with OpenTelemetry and export spans via OTLP/HTTP to the Elastic APM Server (default endpoint: `http://apm-server.shared-services.svc.cluster.local:8200`, configurable via `OTEL_EXPORTER_OTLP_ENDPOINT`). The `api` service's HTTP layer is auto-instrumented via `FastAPIInstrumentor.instrument_app()` at startup (no per-route changes). Any new Kafka consumer or background task added to `publisher` or `reimbursement` must wrap its message/task processing in an explicit `traced_message_span()` call (see `shared.tracing` module), maintaining the same trace-context propagation pattern. All three services must initialize the global `TracerProvider` at startup and cleanly shut it down at process exit. Spans must include identifying attributes: `reimbursement.uuid` at every hop where the value is known, and (for Kafka consumers) topic/partition/offset. **Local Development Note**: The real local dev/verification environment is the sibling `local-env` project's k3s/Tilt setup. Do not attempt to run the three services outside that context for testing traces — the Elastic APM Server dependency is only available there."

**Where**: `/Users/flaviostudart/Projects/Personal/tests/recargapay/CLAUDE.md` (or `CLAUDE.md` in repo root if not the global file)

**Depends on**: T3 (tracing module must exist and be complete)

**Reuses**: Existing CLAUDE.md sections and formatting conventions.

**Requirement**: OTEL-15

**Tools**:
- MCP: filesystem
- Skill: NONE

**Done when**:

- [x] New section added to `CLAUDE.md` (project instructions, not global)
- [x] Text includes: auto-instrumentation for `api`, explicit span wrapping for Kafka consumers/tasks, tracer lifecycle, required attributes, and the local-dev note about `local-env` project
- [x] No grammatical errors or typos
- [x] Formatting matches existing sections (bullet points, bold emphasis where appropriate)

**Tests**: none (documentation; verified by inspection)

**Gate**: none (metadata)

---

## Phase Execution Map

Visual representation of task ordering. Phases run in sequence; tasks within a phase run in order:

```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6

Phase 1:  T1 ──→ T2 ──→ T3

Phase 2:  T4 ──→ T5 ──→ T6

Phase 3:  T7 ──→ T8

Phase 4:  T9 ──→ T10

Phase 5:  T11 ──→ T12

Phase 6:  T13 ──→ T14 ──→ T15
```

Execution is strictly sequential — there is no intra-phase parallelism.

---

## Task Granularity Check

Before approving tasks, verify they are granular enough:

| Task | Scope | Status |
| --- | --- | --- |
| T1: Add OTel dependencies | 2 pyproject.toml files, clear dependency declarations | ✅ Granular |
| T2: Extend config with TracingConfig | 1 dataclass + extend Config + extend load_config | ✅ Granular |
| T3: Create shared.tracing module | 1 new file, 4 functions, one cohesive purpose | ✅ Granular |
| T4: Update shared.producer.publish | 1 function body rewrite, focused scope | ✅ Granular |
| T5: Rewrite test fakes | Rewrite 3–4 fake classes, rewrite 4 test methods | ✅ Granular |
| T6: Verify producer & config together | Verification task, no new implementation | ✅ Granular |
| T7: Instrument api/main.py | TracerProvider + FastAPIInstrumentor, ~6 lines | ✅ Granular |
| T8: Stamp uuid on GET/PUT spans | 2 one-line additions (2 routes) | ✅ Granular |
| T9: Instrument publisher/_serve() | TracerProvider init/shutdown, ~4 lines | ✅ Granular |
| T10: Wrap publisher consumer | One span wrapper in run loop + uuid stamp | ✅ Granular |
| T11: Wrap reimbursement consumer | Same as T10, different service | ✅ Granular |
| T12: Full integration test | New test file, 1 test scenario, all services | ✅ Granular |
| T13: Remove docker-compose | Delete file + edit README sections | ✅ Granular |
| T14: Update STATE.md decision | Append AD-040, 3–4 lines | ✅ Granular |
| T15: Update CLAUDE.md directive | Append standing requirement, ~10 lines | ✅ Granular |

---

## Diagram-Definition Cross-Check

Verify execution diagram is consistent with task definitions:

| Task | Depends On (task body) | Diagram Shows | Status |
| --- | --- | --- | --- |
| T1 | None | Phase 1 start | ✅ Match |
| T2 | T1 | T1 → T2 | ✅ Match |
| T3 | T2 | T2 → T3 | ✅ Match |
| T4 | T1, T2, T3 | Phase 2 start (T3 done) | ✅ Match |
| T5 | T4 | T4 → T5 | ✅ Match |
| T6 | T1, T2, T3, T4, T5 | T5 → T6 | ✅ Match |
| T7 | T3, T2, T1 | Phase 3 start (T6 done) | ✅ Match |
| T8 | T7 | T7 → T8 | ✅ Match |
| T9 | T3, T2 | Phase 4 start (T8 done) | ✅ Match |
| T10 | T3, T9 | T9 → T10 | ✅ Match |
| T11 | T3, T9 (via T8 parallel) | Phase 5 start (T10 done) | ✅ Match |
| T12 | T7, T8, T10, T11 | T11 → T12 | ✅ Match |
| T13 | None (hygiene) | Phase 6 start (T12 done) | ✅ Match |
| T14 | T13 | T13 → T14 | ✅ Match |
| T15 | T3 | T14 → T15 | ✅ Match |

All dependencies point backward or within-phase. No cross-phase forward dependencies. Diagram accurately reflects task-body dependencies.

---

## Test Co-location Validation

Verify EVERY task's `Tests` field is consistent with the **Test Coverage Matrix** generated above:

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| --- | --- | --- | --- | --- |
| T1 | pyproject.toml (config) | none | none | ✅ OK |
| T2 | `shared.config.TracingConfig` | unit | unit | ✅ OK |
| T3 | `shared.tracing` (5 functions) | unit | unit | ✅ OK |
| T4 | `shared.producer.publish` (function) | unit | unit | ✅ OK |
| T5 | test fakes and test methods | unit | unit | ✅ OK |
| T6 | (verification only) | none | none | ✅ OK |
| T7 | `api/main.py` (tracer init/shutdown) | unit | unit | ✅ OK |
| T8 | `api/reimbursement/get` and `update` routes (span attribute) | unit | unit | ✅ OK |
| T9 | `publisher/_serve()` (tracer init/shutdown) | unit | unit | ✅ OK |
| T10 | `publisher/consumer.py` run loop + `processing.py` (span wrapper + attribute) | unit + integration | unit + integration | ✅ OK |
| T11 | `reimbursement/consumer.py` run loop + `validation.py` (span wrapper + attribute) | unit + integration | unit + integration | ✅ OK |
| T12 | integration test (full round-trip) | integration | integration | ✅ OK |
| T13 | `docker-compose.yml` (delete), `README.md` (edit) | none | none | ✅ OK |
| T14 | `.specs/STATE.md` (append decision) | none | none | ✅ OK |
| T15 | `CLAUDE.md` (append directive) | none | none | ✅ OK |

All tasks with code layers have tests co-located and consistent with matrix expectations. No test deferral; no VIOLATION flags.

---

## Tips

- **Phases are ordered** — Each phase completes before the next; tasks run in order within a phase.
- **Reuses = Token saver** — Every task reuses existing patterns (TracerProvider, config loader, test fakes) rather than inventing new ones.
- **Tools per task** — Most tasks are filesystem edits; no MCPs or Skills needed (each is self-contained).
- **Dependencies are gates** — Clear what blocks what (e.g., T3 must come before T7; T4/T5 before T6).
- **Done when = Testable** — Every task's `Done when` is binary verifiable (test passes, file exists, attribute present).
- **Requirement ID = Traceable** — Every task traces back to a spec requirement (OTEL-NNN), and the spec's AC map (at spec.md end) tracks which requirement is which.
- **One commit per task** — Plan: `feat(tracing): <description>` following Conventional Commits. Example: `feat(tracing): add opentelemetry dependencies to shared and api`, `feat(tracing): create shared.tracing module with init_tracer and helpers`, etc.

---

## Task Verification Standards

Every task MUST follow the `Done when` + `Tests` + `Gate` fields defined above. Each `Done when` entry is specific, testable (binary pass/fail), and references the gate check command. Test count and expected assertions are named to prevent silent deletions.
