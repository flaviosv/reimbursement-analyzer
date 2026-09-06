# Grilling Session: RA-1 — json-structured-logging

## Seed

Structured JSON logging (ECS field names via ecs-logging) centralized in packages/shared, LOG_LEVEL env var (default debug), configure_logging() called once per service entrypoint. Plus: API generates a request_id, returns it as X-Request-ID response header, propagates it through Kafka messages so publisher/reimbursement can log with it, includes it in all structured logs, and sends it to LangFuse for tracing correlation. Update CLAUDE.md with a Logging section.

## Facts gathered (via sub-agent exploration)

- API framework: FastAPI, no middleware exists today, no route sets custom response headers today.
- Kafka client: confluent-kafka (AIOProducer/AIOConsumer), not kafka-python/aiokafka. Headers supported by the client but unused anywhere in this repo today.
- Envelopes: `RequestEnvelope` (retry, published_at, errors, payload) and `ReimbursementEnvelope` (uuid, retry, published_at, errors) — both plain pydantic models, no correlation field today. API's producer byte-splices raw JSON for `RequestEnvelope` rather than constructing the model.
- **Naming collision**: `ReimbursementRequest.request_id` already exists — a client-supplied business field (POST payload, unique-indexed with submitted_by in the DB), unrelated to an HTTP correlation id. Confirmed via grep: no existing `X-Request-ID`/`correlation_id`/`trace_id` concept anywhere.
- DB: no `langfuse_session_id` or any correlation-id column on the reimbursement table; only 3 migrations exist (0001–0003).
- LangFuse: `langfuse_session_id=str(reimbursement.uuid)` set once per `agent.decide()` invocation via LangGraph's `config["configurable"]["metadata"]` dict — trivially extensible with more metadata keys, no restructuring needed.
- packages/shared: `config.py` (frozen dataclass Config, `load_config()` @lru_cache) is the natural home for a new LoggingConfig; `shared/logging.py` **already exists** (from the merged traceability-correlation-ids feature, PR #12) — `log_event()` manually `json.dumps()`s fields into the message string, which would defeat this feature's purpose for its own call sites if left as-is.
- Dependency manager: `uv` workspace mode. Python: `>=3.14.7` pinned everywhere already.
- `ecs-logging` 2.3.0 (PyPI, released 2026-01-19) explicitly supports Python 3.14 — confirmed via web search.
- 4 `logging.basicConfig` call sites found (not 3): `api/main.py`, `publisher/consumer.py`, `reimbursement/consumer.py`, and `api/migrate.py` (the yoyo migration runner) — the last one missed by the original ground truth but covered by the same CLAUDE.md rule (it's inside `api`).

## Decisions (Round 1)

1. **request_id/correlation_id format**: UUID7 (`uuid.uuid7()`, stdlib — confirmed Python 3.14 adds RFC 9562 uuid6/7/8 support; no new dependency needed since the project already requires ≥3.14.7). User explicitly asked for "the most modern UUID version."
2. **Inbound X-Request-ID header**: respect if present (echo it back), else generate fresh.
3. **Threading within a process**: `ContextVar` + a `logging.Filter` that auto-injects the id into every `LogRecord` — no per-call-site `extra={}` needed for this field specifically.
4. **Invalid LOG_LEVEL**: falls back to `debug` (same as unset) + logs one warning line, rather than raising.

## Decisions (Round 2 — after facts landed)

5. **Naming**: the new infra-level id is called `correlation_id` internally (contextvar, envelope field, log field) — distinct from the existing business `ReimbursementRequest.request_id`. The HTTP header stays the industry-standard `X-Request-ID` regardless.
6. **Kafka propagation**: a new `Optional[str] = None` field on `RequestEnvelope`/`ReimbursementEnvelope` (not a Kafka header) — reuses the existing typed pydantic parsing at every produce/consume site, no new confluent-kafka header plumbing. Optional for safe rolling-deploy schema evolution.
7. **DB persistence**: none — log/trace-level correlation (header + envelope + logs + LangFuse metadata) covers the traceability requirement without a schema migration.
8. **`log_event()` fix**: refactor to pass fields via `extra={}` instead of manually `json.dumps`-ing them into the message string, so its fields become real ECS/Kibana fields under the new formatter. Call signature unchanged for existing callers.

## Also settled without a separate question (non-controversial, follows directly from the above)

- `agent.decide()`'s LangGraph `metadata` dict gains `"correlation_id": <value>`, read from the same contextvar — no signature change to `decide()` needed.
- `api/migrate.py`'s `basicConfig` call also switches to `configure_logging()` (covered by the existing CLAUDE.md rule, not a new ask).

## Frontier: empty — session concluded, proceeding to Specify.
