# Project Directives — reimbursementanalyzer

Project-specific instructions for Claude Code in this repo. For project context, see `docs/codebase/PROJECT.md` (overview/vision), `docs/SCOPE.md` (the project scope documentation — full requirements), and `.specs/STATE.md` (decisions/risks).

## Non-Functional Requirement — Full Traceability (Hard Requirement)

This service is mission-critical and operates in a financial domain: it drives monetary decisions partly from probabilistic LLM output. **Full traceability of every operation is a functional requirement, not an aspirational goal.** It applies to all new development — treat it with the same weight as correctness.

Source of truth for the requirement text: `docs/SCOPE.md` (`Non-functional`, `Audit trail`, `Traceability` sections). Update that doc first if the requirement itself changes — this file governs how Claude enforces it during implementation.

For any change that creates, modifies, or influences a reimbursement decision (deterministic rule evaluation, LLM/agent call, human review action):

- **Capture inputs and outputs.** Log the request payload, the rule(s) evaluated (with pass/fail), the LLM prompt/response (or a reference to a stored trace), and the final decision — enough to reconstruct *why* a decision was made without re-running it.
- **Attribute every action.** Record which actor produced it — the specific rule ID, the model/prompt version, or the human reviewer's identity — with a timestamp.
- **Wire up LLM tracing.** LangFuse is wired into `reimbursement`'s LangGraph decision graph (`docs/codebase/INTEGRATIONS.md`), and every trace carries `langfuse_session_id=<reimbursement uuid>`, so a trace is queryable by the row it decided. Any new agent/LLM code path must emit a trace to it, session-correlated the same way — this is required before that code path ships, not deferred as follow-up.
- **No silent fallbacks.** If a traceability sink (e.g., LangFuse) is unavailable, fall back to durable file/structured logging rather than dropping the trace. A decision must never happen without a durable record of how it was reached.
- **PII** Be careful with PII, do not output/log PII information

## Logging

Every log line across `api`, `publisher`, and `reimbursement` is a single JSON object with ECS field names, and every HTTP request carries a `correlation_id` that threads through in-process logs, Kafka messages, and LangFuse trace metadata — the mechanism that lets a decision be reconstructed end-to-end from logs alone (the traceability NFR above).

- **`configure_logging()` at every entrypoint.** Each service calls `shared.logging.configure_logging()` exactly once at startup, in place of a raw `logging.basicConfig(...)` call — `api/main.py`, `api/migrate.py`, `publisher/consumer.py`, `reimbursement/consumer.py`. It attaches one ECS-JSON-formatting handler to the root logger (idempotent — safe to call again) and resolves the log level. A new service added to this repo follows the same pattern rather than inventing a parallel one.
- **`LOG_LEVEL` env var.** Controls verbosity for all three services identically — one of `debug`/`info`/`warning`/`error`/`critical`, case-insensitive. Defaults to `debug` when unset. An unrecognized value falls back to `debug` and emits one `warning` line naming the invalid value — a config typo never crashes a service.
- **`correlation_id` propagation chain.** `X-Request-ID` request header (echoed if present and non-empty, else a fresh `uuid.uuid7()`) → `shared.logging`'s `ContextVar` (set by `api.middleware.CorrelationIdMiddleware`, read automatically into every log line via `CorrelationIdFilter` — no per-call-site `extra={}` needed) → `X-Request-ID` response header → the `correlation_id` field on `RequestEnvelope`/`ReimbursementEnvelope` (`Optional[str]`, carried forward unchanged on every requeue) → the consuming process's own `ContextVar` (set from the envelope at the top of `handle_message()`, reset in a `finally` once that message is done) → `agent.decide()`'s LangFuse `metadata` dict, alongside `langfuse_session_id`. A `None` correlation_id at any hop is normal (e.g. a pre-feature message) — never raises, just omits the field/key rather than logging it as `null`.
- **`log_event()`.** `shared.logging.log_event(logger, level, event, **fields)` emits `event` and every field as real structured JSON keys via `extra={}` — never hand-roll `json.dumps()` into a log message string, which defeats ECS field querying. `UUID`-typed field values are passed through as-is; `log_event()` coerces them to plain strings internally.