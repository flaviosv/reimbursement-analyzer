# Code Conventions

## Naming Conventions

- **Files:** `snake_case.py` — e.g. `route.py`, `payload.py`, `producer.py`.
- **Directories (vertical slices):** `<domain>/<operation>/` — e.g. `reimbursement/create/`. Future operations (list, review, ...) are expected as sibling directories under `reimbursement/`.
- **Functions/Methods:** `snake_case`, verb-first for actions — `read_capped`, `validate_batch`, `build_envelope`, `get_producer`.
- **Pydantic models / dataclasses:** `PascalCase` — `ReimbursementRequest`, `RequestEnvelope`, `KafkaConfig`.
- **Constants:** `UPPER_SNAKE_CASE` at module level — `MAX_BODY_BYTES`, `MAX_BATCH_ITEMS`, `KAFKA_MAX_MESSAGE_BYTES`, `REQUEST_TOPIC`.
- **Private/internal helpers:** leading underscore — `_enforce_max_body_bytes`, `_format_error`, `_require_str`.
- **Test classes/methods:** `DescribeXxx` classes holding `it_xxx` methods (`python_classes`/`python_functions` in root `pyproject.toml`) — a failure reads as a sentence, e.g. `DescribePublish::it_raises_publish_failed_on_a_timeout`.

## Code Organization

- **Vertical slices, not horizontal layers:** each operation under `reimbursement/<op>/` owns its route, validation, and publish logic end to end, rather than spreading them across shared `routes/`, `services/`, `repositories/` directories.
- **App-root vs. slice-local:** a module lives at the package root (`api/src/*.py`) only when it is genuinely slice-independent infrastructure (FastAPI app/lifespan, DI accessors, the app-wide error contract). Anything with exactly one consumer belongs inside that consumer's slice — `payload.py` moved from the package root into `reimbursement/create/` for this reason.
- **Shared kernel discipline:** `shared` holds only code with more than one real consumer across services, or a documented cross-service wire contract (`shared.models`, `shared.config`, `shared.errors`, `shared.producer`, `shared.failure_log`, `shared.reimbursement`). Framework-specific code (FastAPI response shaping) stays in `api`, since `api` is the only HTTP service.
- **`use_cases/` for multi-statement actions:** inside a `shared` domain slice, a single SQL statement lives directly in `repository.py`; an action spanning more than one statement (or combining a statement with non-trivial logic, like rendering an error history to prose) gets its own module under `use_cases/` — first instance: `shared/reimbursement/use_cases/send_human_review.py`.
- **Test doubles as a module, not fixtures:** when a test needs a double constructed with different arguments per test (injectable errors, delays, ordering), it's a plain class in a `fakes.py` module, reachable by bare name via the workspace `pythonpath` — not a conftest fixture, which is reserved for shared session-scoped resources. First instance: `src/publisher/tests/fakes.py`.
- **`.next(...)` classmethod for sequence construction:** a pydantic model representing one entry in an accumulating sequence exposes a `.next(previous_entries, ...)` classmethod that derives the next entry's position/timestamp — e.g. `AttemptError.next(errors, stage, exc)` — rather than callers computing `attempt=len(errors) + 1` themselves at each call site.
- **Import ordering** (observed, not `ruff`-enforced — see `CONCERNS.md`): standard library, blank line, third-party + workspace packages (`fastapi`, `pydantic`, `shared.*`), blank line, local same-package modules.

## Type Safety / Documentation

Full type hints everywhere, including test fixtures and helper functions. `pydantic` models (`BaseModel`, `TypeAdapter`) validate all external input; `@dataclass(frozen=True)` for internal config/value objects. No `Any` beyond narrow, justified cases (e.g. `RequestEnvelope.payload: list[dict[str, Any]]`, since the payload's shape is validated upstream, not here).

## Error Handling

Business/domain code raises typed exceptions from `shared.errors` (`PayloadTooLarge`, `BatchInvalid`, `PublishFailed`) — it never constructs an HTTP response itself. Only `api/errors.py`'s registered handlers translate exceptions into responses, so the `{"msg": "..."}` contract is defined in exactly one place. One documented exception to "catch narrowly": `PublishFailed` deliberately catches `Exception` broadly rather than a specific broker-exception type, with the failing exception's class name folded into the message so the failure mode stays identifiable without narrowing the catch surface (a recorded decision, not an oversight).

`publisher.processing` uses a different, complementary shape for its own decision tree: every branch returns an `ItemOutcome` enum member instead of raising, and the top-level `handle_message` never raises either. Per-item independence under concurrent `asyncio.gather` fan-out becomes structural rather than a discipline to remember — one item's exception literally cannot exist to propagate into another's.

Three-tier logging severity, observed consistently in `publisher.processing`:

1. **Structured informational events** — `logger.info(json.dumps({"event": "<domain>.<event_name>", ...}))` for countable, expected outcomes (e.g. `reimbursement.duplicate_dropped`), so a log-based monitor can count occurrences without parsing prose.
2. **Sanitized stdout errors** — `logger.error(...)` paired with `shared.errors.sanitize(exc)`, which renders only the exception type and (if present) the violated constraint name — never a driver's raw `DETAIL` text, which can embed PII.
3. **Last-resort failure log** — `shared.failure_log.write()`, one full structured JSON record (including the item itself) at `logging.CRITICAL` to a dedicated named logger, for a failure nothing else in the chain could handle. Never raises.

## Comments

Sparse and high-value — most functions have no inline comments at all. Where present, comments explain a non-obvious *why* (a security/performance tradeoff, a subtle invariant, a rejected alternative), never a *what*. Many non-trivial functions carry a short docstring stating the one thing a reader wouldn't infer from the code alone (e.g. `build_envelope`'s docstring explaining why splicing bytes — not re-serialising — is what makes the byte-identical guarantee hold).

## Documentation Pattern

- **API schema:** the OpenAPI request-body schema for `POST /api/v1/reimbursement` is derived programmatically from the same `pydantic.TypeAdapter` (`BATCH_ADAPTER.json_schema()`) that performs runtime validation, so the documented and enforced schemas cannot drift apart — no hand-maintained OpenAPI spec.
- **Architectural decisions:** recorded as an append-only `AD-NNN` log in `.specs/STATE.md` (owned by the `tlc-spec-driven` skill, out of scope for this context set) — not in code comments or this doc set.
- **README:** `README.md` documents install/run/test/migrate at a high level, but currently references a pre-flatten file layout (`src/api/src/api/migrations/`) and an outdated migrate invocation — see `CONCERNS.md`.
