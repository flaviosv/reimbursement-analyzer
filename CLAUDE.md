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

## Distributed Tracing — Standing Requirement

`api`, `publisher`, and `reimbursement` are always instrumented with OpenTelemetry and export spans via OTLP/HTTP to the Elastic APM Server (default endpoint `http://apm-server.shared-services.svc.cluster.local:8200`, configurable via `OTEL_EXPORTER_OTLP_ENDPOINT`) — see `shared.tracing` for the shared `init_tracer`/`shutdown_tracer`/`traced_message_span` helpers every service's composition root calls into.

- **`api`'s HTTP layer** is auto-instrumented via `FastAPIInstrumentor.instrument_app(app)` at startup — no per-route span-creation code.
- **Any new Kafka consumer or background task** added to `publisher` or `reimbursement` must wrap its message/task processing in an explicit `shared.tracing.traced_message_span(...)` call, maintaining the same trace-context propagation pattern (extract from inbound Kafka headers, inject into outbound ones via `shared.producer.publish`) — this is required before that code path ships, not deferred as follow-up, mirroring the LLM-tracing rule above.
- **Every service** initializes the global `TracerProvider` at startup and shuts it down cleanly at its existing shutdown hook.
- **Span attributes**: stamp `correlation_id` at every hop where it's available (which is every hop, from the first — see `AD-041` in `.specs/STATE.md`), reading it from `shared.logging`'s ContextVar getter or the envelope, whichever is already in scope; also stamp `reimbursement.uuid` wherever the span already has it in scope (it remains independently useful, e.g. for direct DB lookups), and (for Kafka consumer spans) the message's topic/partition/offset.

**Local development note:** this repo's real local dev/verification environment is the sibling `local-env` project's k3s/Tilt setup (the only place that reaches the real Elastic APM Server) — not an operational instruction for other developers to follow verbatim, since that path is specific to this machine.

## Observability — Metrics & Dashboards

`docs/METRICS.md` is the source of truth for every Prometheus metric this
repo's 3 services expose: types, labels, histogram bucket configuration
(see "The Decision-Latency Histogram Split" and "API HTTP Latency
Buckets"), and which percentiles the Grafana dashboard graphs for each
histogram (with the reasoning, and a note on whether it's backed by an
actual SLO or just a generic default). Keep it in sync whenever a metric is
added, renamed, relabeled, or rebucketed.

A Grafana dashboard for this project already exists, in its own folder, in
the shared platform's k3s cluster — kept in sync with `docs/METRICS.md`,
not the other way around. A change to a metric's meaning, labels, or which
percentiles matter should land in `docs/METRICS.md` first, then be
reflected in the dashboard. To find the dashboard, its scrape config, or
how it's provisioned, query the cluster directly rather than assuming
anything about how it got there:
- `kubectl get ingress -A | grep -i grafana` — dashboard URL
- `kubectl get svc,deploy -n shared-services | grep -i grafana` — how Grafana itself runs
- `kubectl get svc <api/publisher service> -o yaml | grep prometheus.io` — this repo's own Prometheus scrape annotations (should already be present on `api`'s and `publisher`'s Services)

## Logging

Every log line across `api`, `publisher`, and `reimbursement` is a single JSON object with ECS field names, and every HTTP request carries a `correlation_id` that threads through in-process logs, Kafka messages, and LangFuse trace metadata — the mechanism that lets a decision be reconstructed end-to-end from logs alone (the traceability NFR above).

- **`configure_logging()` at every entrypoint.** Each service calls `shared.logging.configure_logging()` exactly once at startup, in place of a raw `logging.basicConfig(...)` call — `api/main.py`, `api/migrate.py`, `publisher/consumer.py`, `reimbursement/consumer.py`. It attaches one ECS-JSON-formatting handler to the root logger (idempotent — safe to call again) and resolves the log level. A new service added to this repo follows the same pattern rather than inventing a parallel one.
- **`LOG_LEVEL` env var.** Controls verbosity for all three services identically — one of `debug`/`info`/`warning`/`error`/`critical`, case-insensitive. Defaults to `debug` when unset. An unrecognized value falls back to `debug` and emits one `warning` line naming the invalid value — a config typo never crashes a service.
- **`correlation_id` propagation chain.** `X-Request-ID` request header (echoed if present and non-empty, else a fresh `uuid.uuid7()`) → `shared.logging`'s `ContextVar` (set by `api.middleware.CorrelationIdMiddleware`, read automatically into every log line via `CorrelationIdFilter` — no per-call-site `extra={}` needed) → `X-Request-ID` response header → the `correlation_id` field on `RequestEnvelope`/`ReimbursementEnvelope` (`Optional[str]`, carried forward unchanged on every requeue) → the consuming process's own `ContextVar` (set from the envelope at the top of `handle_message()`, reset in a `finally` once that message is done) → `agent.decide()`'s LangFuse `metadata` dict, alongside `langfuse_session_id`. A `None` correlation_id at any hop is normal (e.g. a pre-feature message) — never raises, just omits the field/key rather than logging it as `null`.
- **`log_event()`.** `shared.logging.log_event(logger, level, event, **fields)` emits `event` and every field as real structured JSON keys via `extra={}` — never hand-roll `json.dumps()` into a log message string, which defeats ECS field querying. `UUID`-typed field values are passed through as-is; `log_event()` coerces them to plain strings internally.
