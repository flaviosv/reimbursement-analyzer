# POST /api/v1/reimbursement — Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and
follow its Execute flow and Critical Rules.** Do not search for skill files by
filesystem path. The skill is the source of truth for the full flow (per-task
cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user — do not proceed
without it.**

---

**Design**: `.specs/features/api-post-reimbursement/design.md`
**Spec**: `.specs/features/api-post-reimbursement/spec.md`
**Status**: Done — all 11 tasks complete, Verifier PASS (see `validation.md`)

---

## Test Coverage Matrix

> **Re-verified against the branch as of PR #3 merge (2026-08-07).** The
> sample below was taken before `db-schema-migrations` finished; its final
> shape changed two conventions this matrix must follow:
>
> - `pyproject.toml`'s `[tool.pytest.ini_options]` now sets
>   `python_classes = ["Describe*"]` and `python_functions = ["it_*"]` — a
>   **confirmed project-wide convention** (`README.md`: "Tests are grouped
>   `describe`/`it` style... a failure reads as a sentence"). Every test in
>   this feature uses `class DescribeX: def it_does_y(self, ...) -> None:`,
>   not bare functions.
> - `testpaths = ["src"]`, not `["src/api/tests"]` — the whole workspace, so a
>   single member's tests dir never goes silently uncollected.
>
> Guidelines found: `~/.claude/CLAUDE.md` (global directives — no explicit
> coverage thresholds), `README.md` (test invocation, Docker prerequisite,
> describe/it style), `pyproject.toml` (`testpaths`, `python_classes`,
> `python_functions`). No project `AGENTS.md`/`CONTRIBUTING.md` beyond these,
> so **strong defaults apply** for depth.
> Style sampled from `src/api/tests/test_reimbursement_schema.py`,
> `test_human_review_schema.py`, `conftest.py`, `helpers.py` —
> `Describe*`/`it_*` classes, module-level helper functions, full type hints,
> no bare test functions.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------ | -------------------- | ---------------- | ----------- |
| Slice logic — validation, envelope, publish | unit | All branches; 1:1 to spec ACs; every listed edge case has a test | `src/api/tests/reimbursement/<op>/test_{validation,producer}.py` | quick |
| Route / HTTP contract | unit (httpx ASGI transport) | Every status the route can return — `201`, `400`, `413`, `500` — plus each listed edge case | `src/api/tests/reimbursement/<op>/test_route.py` | quick |
| App-wide error handlers | unit | Every registered handler renders `{"msg"}`; FastAPI's `422`+`detail[]` proven replaced | `src/api/tests/test_errors.py` | quick |
| Infrastructure config — producer config, compose sizing | unit | Each asserted invariant: `acks=all`, idempotence, size ceiling, timeout ordering, compose↔`config.py` parity | `src/api/tests/test_kafka.py`, `src/api/tests/test_compose_parity.py` | quick |
| Broker round-trip | integration | RCV-20: ceiling-sized message published and consumed back byte-identical | `src/api/tests/reimbursement/<op>/test_integration.py` | full |
| OpenAPI schema | unit | Route present; all four status codes documented; body schema derived from the adapter | `src/api/tests/reimbursement/<op>/test_openapi.py` | quick |
| Existing endpoint regression | unit | `/health` unchanged after the `main.py` refactor | `src/api/tests/test_health.py` | quick |
| Wire-contract models, `responses.py`, `config.py` constants | none | build gate only — behaviour is covered where the constants are consumed | — | build gate only |

## Gate Check Commands

> Generated from codebase — confirm before Execute.

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | After tasks with unit tests only | `uv run pytest -q -m "not integration"` |
| Full | After tasks with integration tests | `uv run pytest -q` |
| Build | After config/schema-only tasks | `uv sync --all-packages && docker compose config -q` |

**Docker is a prerequisite for every gate above Build**, not just Full — AD-008
made the migration suite start its own `postgres:18` container. Quick excludes
only the Kafka container. For the inner edit loop (no Postgres either):
`uv run pytest src/api/tests/reimbursement src/api/tests/test_health.py -q -m "not integration"`.

---

## Execution Plan

Phases are ordered and run sequentially — each phase completes before the next
begins, and tasks within a phase execute in order.

### Phase 1: Foundation — test wiring, contracts, cross-cutting concerns

Nothing in the slice can be written or tested until the nested test layout
works, the models exist, and the error contract is registered.

```
T1 → T2 → T3 → T4
```

### Phase 2: The slice

`reimbursement/create/` end to end, behind a fake producer.

```
T5 → T6 → T7 → T8
```

### Phase 3: Kafka sizing, real broker, docs

```
T9 → T10 → T11
```

---

## Task Breakdown

### T1: Wire pytest for nested test packages and add the `/health` regression

**What**: Add `pythonpath`, `--import-mode=importlib`, and the `integration`
marker to the root pytest config; add `httpx` to the dev group; add the `/health`
regression test that guards the `main.py` refactor in T8.
**Where**: `pyproject.toml` (modify), `src/api/tests/test_health.py` (new)
**Depends on**: None
**Reuses**: `[tool.pytest.ini_options]` block at `pyproject.toml:16-17`
**Requirement**: Success criterion — `/health` unchanged

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `[tool.pytest.ini_options]` gains `pythonpath = ["src/api/tests"]`,
      `addopts = "--import-mode=importlib"`, and
      `markers = ["integration: requires a container beyond the suite default"]`
- [ ] `testpaths` is **unchanged** — the block is shared with `db-schema-migrations`
- [ ] `httpx` added to `[dependency-groups] dev` (currently only transitive)
- [ ] `test_health.py` asserts `GET /health` → `200` and `{"status": "ok"}`
- [ ] The existing migration suite still collects and passes — no test lost
- [ ] Gate check passes: `uv run pytest -q -m "not integration"`
- [ ] Test count: existing suite count + 1, all passing (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `test(api): wire pytest for nested slices and cover /health`

---

### T2: Add the cross-service wire contracts to `shared`

**What**: `ReimbursementRequest` and `RequestEnvelope` in the shared kernel, plus
the `pydantic[email]` extra that `EmailStr` requires at import time.
**Where**: `src/shared/src/shared/models.py` (modify),
`src/shared/pyproject.toml` (modify)
**Depends on**: None
**Reuses**: `HealthStatus` at `src/shared/src/shared/models.py:4` — same file, same style
**Requirement**: RCV-07, RCV-08, RCV-09 (shape only; behaviour asserted in T5)

**Tools**:

- MCP: `context7` (confirm Pydantic v2 `AwareDatetime` / `EmailStr` / `StringConstraints` surface)
- Skill: NONE

**Done when**:

- [ ] `ReimbursementRequest` matches the design exactly:
      `extra="allow"`, `request_id` stripped + `min_length=1`, `submitted_by:
      EmailStr`, `submitted_at: AwareDatetime`
- [ ] `RequestEnvelope` has `retry: int`, `published_at: AwareDatetime`,
      `payload: list[dict[str, Any]]`
- [ ] `MessageResponse` is **not** here — it is api-only and lands in T4
- [ ] `pydantic[email]` in `src/shared/pyproject.toml`; `uv sync` resolves
- [ ] `from shared.models import ReimbursementRequest` imports without error
- [ ] Gate check passes: `uv sync --all-packages && docker compose config -q`

**Tests**: none (schema layer — matrix says build gate only)
**Gate**: build

**Commit**: `feat(shared): add reimbursement request and envelope contracts`

---

### T3: Add `api/config.py`

**What**: The constants something outside a slice compares against, plus the
bootstrap-servers reader.
**Where**: `src/api/src/api/config.py` (new)
**Depends on**: None
**Reuses**: `os.environ.get(name, local_default)` convention from
`src/agent/src/agent/consumer.py:12`
**Requirement**: RCV-18, RCV-19, RCV-20

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `MAX_BODY_BYTES = 26_214_400` and `KAFKA_MAX_MESSAGE_BYTES = 27_262_976`
- [ ] `MESSAGE_TIMEOUT_MS = 8000`
- [ ] `bootstrap_servers()` reads `KAFKA_BOOTSTRAP_SERVERS`, defaulting to
      `localhost:9092` to match the existing consumers
- [ ] `REQUEST_TOPIC` and `PUBLISH_TIMEOUT_SECONDS` are **absent** — they belong
      to the slice (design slice rule 4)
- [ ] Gate check passes: `uv sync --all-packages && docker compose config -q`

**Tests**: none (config layer — matrix says build gate only)
**Gate**: build

**Commit**: `feat(api): add config constants for body and message ceilings`

---

### T4: Add the app-wide `{"msg"}` error contract

**What**: `MessageResponse`, the three domain exceptions, and the exception
handlers that guarantee every error response is `{"msg": ...}` — including
replacing FastAPI's `422` + `detail[]`.
**Where**: `src/api/src/api/responses.py` (new), `src/api/src/api/errors.py`
(new), `src/api/tests/test_errors.py` (new)
**Depends on**: T1
**Reuses**: FastAPI's `app.exception_handler` registration
**Requirement**: RCV-11, RCV-13

**Tools**:

- MCP: `context7` (FastAPI exception-handler + `RequestValidationError` surface)
- Skill: NONE

**Done when**:

- [ ] `responses.py` defines `MessageResponse(msg: str)` — api-only, **not** in `shared`
- [ ] `errors.py` defines `PayloadTooLarge`, `BatchInvalid`, `PublishFailed`
- [ ] `register_handlers(app)` covers all three plus `RequestValidationError`,
      `HTTPException`, and a catch-all `Exception`
- [ ] Tests build a throwaway FastAPI app whose routes raise each exception, and
      assert status code + `{"msg"}` body shape — one test per handler
- [ ] A test proves `RequestValidationError` yields `400` + `{"msg"}`, never
      `422` + `detail[]` (RCV-13)
- [ ] A test proves the handler never echoes a field value: raise `BatchInvalid`
      built from a Pydantic `ValidationError` carrying a known secret value, and
      assert that value is absent from the response body (RCV-11)
- [ ] Gate check passes: `uv run pytest -q -m "not integration"`
- [ ] Test count: ≥7 new tests pass (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(api): add app-wide msg error contract`

---

### T5: Add the slice's validation stage

**What**: `read_capped` (streaming byte ceiling) and `validate_batch`
(`TypeAdapter`-driven batch check) — everything that rejects a request before
any side effect.
**Where**: `src/api/src/api/reimbursement/create/validation.py` (new, plus
`__init__.py` for `reimbursement/` and `create/`),
`src/api/tests/reimbursement/create/test_validation.py` (new)
**Depends on**: T1, T2, T3, T4
**Reuses**: Starlette `request.stream()`; `shared.models.ReimbursementRequest`;
`api.config.MAX_BODY_BYTES`; `api.errors.{PayloadTooLarge,BatchInvalid}`
**Requirement**: RCV-07, RCV-08, RCV-09, RCV-10, RCV-11, RCV-12, RCV-18, RCV-19

**Tools**:

- MCP: `context7` (Pydantic `TypeAdapter.validate_json` + `ValidationError.errors()` `loc` shape)
- Skill: NONE

**Done when**:

- [ ] `read_capped` counts bytes **while** streaming and raises `PayloadTooLarge`
      as soon as the running total exceeds the limit — `Content-Length` never consulted
- [ ] Boundary proven both ways: exactly `MAX_BODY_BYTES` passes,
      `MAX_BODY_BYTES + 1` raises (RCV-19)
- [ ] A test streams a body in chunks with a **lying** `Content-Length` header and
      proves the decision came from the counted bytes (RCV-18)
- [ ] `BATCH_ADAPTER` is a module-level `TypeAdapter(list[ReimbursementRequest])`
- [ ] Table-driven rejection tests: missing each of the three fields, bad email,
      naive `submitted_at`, non-JSON body, JSON object instead of array, empty
      array — one case each (RCV-07/08/09/12)
- [ ] Error message names the zero-based item index and field name and contains
      **no** field value (RCV-10, RCV-11)
- [ ] A test proves `extra="allow"` keeps unknown fields (feeds RCV-06)
- [ ] Nested test package imports resolve — this task is the first proof that
      T1's `pythonpath` + `importlib` config works
- [ ] Gate check passes: `uv run pytest -q -m "not integration"`
- [ ] Test count: ≥14 new tests pass (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(api): add reimbursement create validation stage`

---

### T6: Add the `AIOProducer` lifecycle module

**What**: Producer configuration, the lifespan hook that owns the single
producer, and the FastAPI dependency that hands it to routes.
**Where**: `src/api/src/api/kafka.py` (new), `src/api/tests/test_kafka.py` (new)
**Depends on**: T3
**Reuses**: `api.config.{bootstrap_servers,KAFKA_MAX_MESSAGE_BYTES,MESSAGE_TIMEOUT_MS}`
**Requirement**: RCV-14, RCV-20

**Tools**:

- MCP: `context7` (confluent-kafka `AIOProducer` constructor surface)
- Skill: NONE

**Done when**:

- [ ] `producer_config()` sets `acks=all`, `enable.idempotence=true`,
      `message.max.bytes=KAFKA_MAX_MESSAGE_BYTES`,
      `message.timeout.ms=MESSAGE_TIMEOUT_MS`, and `bootstrap.servers`
- [ ] `AIOProducer` is constructed with `batch_size=1` — verified against
      `_AIOProducer.py:218`; the 1000/1.0s defaults would add ~1 s per request
      and buffer up to 1000 × 25 MB
- [ ] `lifespan_producer` sets `app.state.producer` and stops it on shutdown
- [ ] `get_producer` dependency returns it from the request
- [ ] A test asserts `retries` is **not** disabled (idempotence rejects `retries=0`,
      and `retry` in the envelope is a different counter entirely)
- [ ] Gate check passes: `uv run pytest -q -m "not integration"`
- [ ] Test count: ≥5 new tests pass (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(api): add kafka producer lifecycle`

---

### T7: Add the slice's publish stage

**What**: `build_envelope` (byte splice, no re-serialisation) and `publish`
(produce + await the per-message delivery Future).
**Where**: `src/api/src/api/reimbursement/create/producer.py` (new),
`src/api/tests/reimbursement/create/test_producer.py` (new)
**Depends on**: T2, T4
**Reuses**: `api.errors.PublishFailed`; `shared.models.RequestEnvelope` as the
parse-side assertion target
**Requirement**: RCV-03, RCV-04, RCV-05, RCV-14, RCV-15, RCV-16, RCV-17

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `build_envelope` splices bytes — `prefix + raw + b"}"` — and never calls
      `model_dump_json` on the payload
- [ ] A test round-trips `docs/original/sample.json` and asserts the `payload`
      slice is **byte-for-byte** identical to the input, including key order and
      unicode escaping (RCV-04)
- [ ] Tests assert `retry == 0` and `published_at` parses as an aware RFC 3339
      UTC datetime (RCV-03, RCV-05)
- [ ] The result parses cleanly with `RequestEnvelope.model_validate_json`
- [ ] `REQUEST_TOPIC` and `PUBLISH_TIMEOUT_SECONDS` are defined here, not in `config.py`
- [ ] A test asserts `api.config.MESSAGE_TIMEOUT_MS / 1000 < PUBLISH_TIMEOUT_SECONDS`
      — the ordering that stops a phantom `500` on a message that later
      delivers. Moved here from T6: `PUBLISH_TIMEOUT_SECONDS` doesn't exist
      until this task, so T6 could not have tested it (task-boundary fix,
      2026-08-07)
- [ ] `publish` takes the producer as an argument — never constructs one
- [ ] Tests with a fake producer cover: success resolves, broker error raises
      `PublishFailed`, timeout raises `PublishFailed` (RCV-14, RCV-15)
- [ ] A concurrency test drives two overlapping publishes whose delivery Futures
      resolve **out of order** — one success, one failure — and proves each caller
      gets its own verdict (RCV-16)
- [ ] A failure test asserts the log record carries the `request_id`s and the
      broker error but **not** the payload body (RCV-17)
- [ ] Gate check passes: `uv run pytest -q -m "not integration"`
- [ ] Test count: ≥10 new tests pass (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(api): add reimbursement create publish stage`

---

### T8: Add the route and wire it into the app

**What**: `POST /api/v1/reimbursement` orchestrating cap → validate → publish →
respond, plus the `main.py` refactor that registers the lifespan, the error
handlers, and the router.
**Where**: `src/api/src/api/reimbursement/create/route.py` (new),
`src/api/src/api/main.py` (modify),
`src/api/tests/reimbursement/create/test_route.py` (new)
**Depends on**: T5, T6, T7
**Reuses**: `main.py:4` app instance — `/health` stays untouched
**Requirement**: RCV-01, RCV-02, RCV-06, RCV-10, RCV-12, RCV-13, RCV-15, RCV-18

**Tools**:

- MCP: `context7` (FastAPI lifespan + `APIRouter` + dependency surface)
- Skill: NONE

**Done when**:

- [ ] Route is `POST /api/v1/reimbursement`, returns `201` only after the
      delivery Future resolves successfully (RCV-01)
- [ ] `main.py` registers `lifespan_producer`, `register_handlers`, and the
      router — and nothing else about the slice
- [ ] httpx ASGI-transport tests with a fake producer cover every status the
      route emits: `201`, `400`, `413`, `500`
- [ ] A test asserts exactly **one** message is produced per accepted request (RCV-02)
- [ ] A test asserts unknown fields survive to the published payload unaltered (RCV-06)
- [ ] Every reject path asserts the fake recorded **zero** publishes (RCV-10, RCV-12)
- [ ] `/health` regression test from T1 still passes after the refactor
- [ ] Gate check passes: `uv run pytest -q -m "not integration"`
- [ ] Test count: ≥10 new tests pass (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(api): add POST reimbursement endpoint`

---

### T9: Size the compose broker and assert parity with `config.py`

**What**: Raise the three Kafka size limits on the compose broker, and add the
test that stops the shipped number from drifting away from the constant.
**Where**: `docker-compose.yml` (modify),
`src/api/tests/test_compose_parity.py` (new)
**Depends on**: T3
**Reuses**: `kafka` service env block at `docker-compose.yml:53-83`
**Requirement**: RCV-20 (half 1)

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] `KAFKA_MESSAGE_MAX_BYTES`, `KAFKA_REPLICA_FETCH_MAX_BYTES`, and the
      topic-level default are set to `27262976` on the `kafka` service
- [ ] `KAFKA_SOCKET_REQUEST_MAX_BYTES` is left alone — its 100 MB default already clears this
- [ ] The parity test parses `docker-compose.yml` and asserts the shipped value
      equals `api.config.KAFKA_MAX_MESSAGE_BYTES`
- [ ] A test asserts the invariant `KAFKA_MAX_MESSAGE_BYTES > MAX_BODY_BYTES`
      with enough headroom for the envelope prefix
- [ ] The test **fails** when the compose literal is edited — verified by
      temporarily changing it, not assumed
- [ ] Gate check passes: `uv run pytest -q -m "not integration"` and
      `docker compose config -q`
- [ ] Test count: ≥3 new tests pass (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(compose): size kafka for 25 MiB request batches`

---

### T10: Prove a ceiling-sized message survives a real broker

**What**: The slice's Kafka container fixture and the 25 MiB round-trip
integration test.
**Where**: `src/api/tests/reimbursement/create/conftest.py` (new),
`src/api/tests/reimbursement/create/test_integration.py` (new)
**Depends on**: T8, T9
**Reuses**: AD-008's testcontainers pattern from `src/api/tests/conftest.py:34`
**Requirement**: RCV-20 (half 2)

**Tools**:

- MCP: NONE
- Skill: NONE

**Done when**:

- [ ] Session-scoped `KafkaContainer` fixture lives in the **slice's** `conftest.py`,
      so the migration tests never start a broker
- [ ] The container is sized **from** `api.config.KAFKA_MAX_MESSAGE_BYTES` — the
      number is never retyped
- [ ] Image pinned to the compose broker (`apache/kafka:4.3.1`) if
      `KafkaContainer`'s KRaft start script boots it; otherwise fall back to the
      module default and **record the deviation in the commit body** — the parity
      test still carries the shipped-config claim
- [ ] Test publishes a payload at exactly `MAX_BODY_BYTES` through the real route
      and consumes it back, asserting the payload slice is byte-identical
- [ ] Marked `@pytest.mark.integration` so the Quick gate stays broker-free
- [ ] Gate check passes: `uv run pytest -q`
- [ ] Test count: ≥2 new tests pass (no silent deletions)

**Tests**: integration
**Gate**: full

**Commit**: `test(api): round-trip a ceiling-sized batch through kafka`

---

### T11: Document the route in OpenAPI

**What**: Manual body schema plus the four documented status codes, derived from
the same adapter that validates, so docs and validation cannot drift.
**Where**: `src/api/src/api/reimbursement/create/route.py` (modify),
`src/api/tests/reimbursement/create/test_openapi.py` (new)
**Depends on**: T8
**Reuses**: `BATCH_ADAPTER.json_schema()` from T5
**Requirement**: RCV-21

**Tools**:

- MCP: `context7` (FastAPI `openapi_extra` + `responses=` surface)
- Skill: NONE

**Done when**:

- [ ] `openapi_extra` supplies the request-body schema from
      `BATCH_ADAPTER.json_schema()` — not hand-written
- [ ] `responses=` documents `201`, `400`, `413`, `500`, all with `MessageResponse`
- [ ] A test fetches `/openapi.json` and asserts the path, all four status codes,
      and that the body schema is an array whose items require the three fields
- [ ] Gate check passes: `uv run pytest -q -m "not integration"`
- [ ] Test count: ≥3 new tests pass (no silent deletions)

**Tests**: unit
**Gate**: quick

**Commit**: `docs(api): document POST reimbursement in openapi`

---

## Phase Execution Map

Execution is strictly sequential T1 → T11. The arrows below are **true data
dependencies**, not execution order — a task with no inbound arrow simply has no
prerequisite.

```
Phase 1 → Phase 2 → Phase 3

Phase 1:   T1 ──→ T4
           T2
           T3

Phase 2:   T1, T2, T3, T4 ──→ T5 ──┐
                  T3 ──→ T6 ───────┼──→ T8
              T2, T4 ──→ T7 ───────┘

Phase 3:           T3 ──→ T9 ──┐
                               ├──→ T10
                   T8 ─────────┘
                   T8 ──→ T11
```

---

## Task Granularity Check

| Task | Scope | Status |
| ---- | ----- | ------ |
| T1: pytest wiring + `/health` test | 1 config block + 1 test file | ✅ Granular |
| T2: shared contracts | 2 models, 1 file + its dep | ✅ Granular |
| T3: `config.py` | 1 file, constants only | ✅ Granular |
| T4: error contract | 2 files, one concept (the `{"msg"}` contract) | ⚠️ Cohesive — `responses.py` is 3 lines and exists only to be rendered by `errors.py`; splitting yields a 3-line commit |
| T5: validation stage | 1 module, 1 concern (reject before side effect) | ✅ Granular |
| T6: producer lifecycle | 1 module | ✅ Granular |
| T7: publish stage | 1 module | ✅ Granular |
| T8: route + wiring | 1 endpoint + its registration | ⚠️ Cohesive — merged backward per the skill's compilation-dependency rule: the route cannot be tested until it is mounted |
| T9: compose sizing + parity | 1 config change + its test | ✅ Granular |
| T10: integration test | 1 fixture + 1 test | ✅ Granular |
| T11: OpenAPI | 1 route decoration + its test | ✅ Granular |

---

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| ---- | ---------------------- | ------------- | ------ |
| T1 | None | no inbound arrow | ✅ Match |
| T2 | None | no inbound arrow | ✅ Match |
| T3 | None | no inbound arrow | ✅ Match |
| T4 | T1 | T1 → T4 | ✅ Match |
| T5 | T1, T2, T3, T4 | T1, T2, T3, T4 → T5 | ✅ Match |
| T6 | T3 | T3 → T6 | ✅ Match |
| T7 | T2, T4 | T2, T4 → T7 | ✅ Match |
| T8 | T5, T6, T7 | T5, T6, T7 → T8 | ✅ Match |
| T9 | T3 | T3 → T9 | ✅ Match |
| T10 | T8, T9 | T8, T9 → T10 | ✅ Match |
| T11 | T8 | T8 → T11 | ✅ Match |

No task depends on a later phase.

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| ---- | --------------------------- | --------------- | --------- | ------ |
| T1 | Existing endpoint regression | unit | unit | ✅ OK |
| T2 | Wire-contract models | none (build gate) | none | ✅ OK |
| T3 | Config constants | none (build gate) | none | ✅ OK |
| T4 | App-wide error handlers + `responses.py` | unit | unit | ✅ OK |
| T5 | Slice logic — validation | unit | unit | ✅ OK |
| T6 | Infrastructure config — producer | unit | unit | ✅ OK |
| T7 | Slice logic — envelope + publish | unit | unit | ✅ OK |
| T8 | Route / HTTP contract | unit | unit | ✅ OK |
| T9 | Infrastructure config — compose sizing | unit | unit | ✅ OK |
| T10 | Broker round-trip | integration | integration | ✅ OK |
| T11 | OpenAPI schema | unit | unit | ✅ OK |

No `Tests: none` is justified by "covered in another task" — T2 and T3 are the
schema/config layer the matrix marks build-gate-only, and their behavioural
consequences are asserted in T5, T6, and T9 where the values are consumed.

---

## Execution Mode

**Decided 2026-08-07: inline, no sub-agents.** The 11 tasks pack into two
batches (T1–T8, T9–T11) and the sub-agent offer was made and declined — every
task runs in the main conversation so each implementation, gate result, and
commit is visible and redirectable mid-flight.

**Tooling: as assigned per task.** Context7 on the five tasks that touch a
library's API surface (Pydantic v2 in T2/T5, FastAPI in T4/T8/T11,
confluent-kafka in T6); no additional skills layered in. The per-task test gates
and the automatic Verifier carry the quality bar.

The Verifier still runs automatically after T11 — it is never optional and never
prompted.
