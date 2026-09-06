# Grilling Session: RA-2 — Adding Tracing support

## Seed Request

Add OpenTelemetry (OTel) distributed tracing to this project, exporting to an Elastic APM Server already running in the cluster (`http://apm-server.shared-services.svc.cluster.local:8200`, OTLP/HTTP, no vendor-specific Elastic APM agent — upstream OTel Python libraries only).

Services: `api` (FastAPI, HTTP-facing), `publisher` and `reimbursement` (Kafka consumers, no HTTP boundary).

Baseline scope handed in by the user:

1. Dependencies: `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-instrumentation-fastapi` (api only) — current stable versions compatible with the project's Python version.
2. Each service initializes a `TracerProvider`: resource attribute `service.name` (`reimbursement-analyzer-api` / `-publisher` / `-reimbursement`), `BatchSpanProcessor` exporting via OTLP/HTTP to an endpoint read from `OTEL_EXPORTER_OTLP_ENDPOINT` (default the URL above), registered as the global tracer provider, shut down cleanly on process exit.
3. `api`: `FastAPIInstrumentor.instrument_app(app)` once at startup — no per-route changes.
4. `publisher`/`reimbursement`: wrap each consumed Kafka message's processing in an explicit span (`tracer.start_as_current_span("process_message")` or equivalent) around existing handling logic, without restructuring the consumer loop. Message-identifying attributes (topic, partition/offset).
5. Verify by actually running each service and confirming a trace exports — not just assuming the wiring works.
6. Add a standing instrumentation directive to this repo's `CLAUDE.md`.

## Facts Gathered (via Explore sub-agent, read-only)

- **Python 3.14.7** exactly, `uv` workspace (`packages/{api,publisher,reimbursement,shared}`, each with its own `pyproject.toml`, dependency ranges via `>=`, never `==`). `shared` is a workspace dependency of the other three.
- **Kafka client**: `confluent_kafka.aio` (`AIOConsumer`/`AIOProducer`), one message at a time via `consumer.consume(num_messages=1, ...)`, manual commit. `message.topic()/partition()/offset()` all available on the `cimpl.Message`.
- **Producer/consumer chain**: `api` (produce `Request`) → `publisher` (consume `Request`, produce `Reimbursement`, or requeue `Request` on transient failure) → `reimbursement` (consume `Reimbursement`, or requeue `Reimbursement` on transient failure). `publisher` is the pivot that both consumes and produces to a different topic.
- **Shutdown hooks**: api's FastAPI `lifespan` (after `yield`); publisher/reimbursement's `_serve()` (after `run(...)` returns, before the `async with` pool/producer/consumer block exits). `reimbursement` uses `shared.signals.install_shutdown_handlers`; `publisher` has its own separate (pre-existing, out-of-scope-to-fix) signal handler.
- **Existing correlation-id convention**: `reimbursement.uuid` (`shared.models.Reimbursement.uuid`) is already threaded through logs end-to-end (feature TRC-07/08/09) and stamped as `langfuse_session_id` on reimbursement's LangFuse callback metadata.
- **Config pattern**: no pydantic Settings — frozen `@dataclass` configs loaded via `@lru_cache` functions (`shared.config.load_config()`, `publisher.config.load_publisher_config()`, `reimbursement.config.load_agent_config()`), reading `os.getenv` after `load_dotenv()`. New OTel env vars would follow the same pattern.
- **Local infra reality**: this repo's `docker-compose.yml` is superseded — actual local dev runs on a real local k3s cluster via Terraform + Tilt in the sibling project `~/Projects/Personal/local-env` (`terraform/personal/reimbursement-analyzer.tf` + its `Tiltfile`), deploying api/publisher/reimbursement as real k8s Deployments in a `personal` namespace. `terraform/shared-services/apm-server.tf` confirms `apm-server.shared-services.svc.cluster.local:8200` is a genuinely reachable in-cluster address there — not a placeholder.

## Decisions (rounds 1–2, user-confirmed)

1. **Kafka trace propagation**: inject trace context into Kafka message headers on every publish through `shared.producer.publish` (covers api→Request, publisher→Reimbursement, and both services' retry/requeue re-publishes); extract on consume. The full api→publisher→reimbursement chain is one linked distributed trace, not three independent span sets.
2. **LangFuse correlation**: stamp `reimbursement.uuid` as a span attribute — and per the user's follow-up, do so at *every* hop (api's auto-instrumented request span, publisher's consumer span, reimbursement's consumer span), not only on reimbursement's span. **Forward-looking note** (not this feature's job to implement): a separate, currently in-flight session is introducing a broader `correlation_id` (sourced from the request or generated in the api layer); once that lands, this attribute likely gets renamed/rewired to use it instead of `reimbursement.uuid`. Use `reimbursement.uuid` now.
3. **Sampling**: default `ParentBased(AlwaysOn)` — no new sampling env var for this feature.
4. **Dependency/module placement**: centralize in `shared` — a new `shared.tracing` module (following the `shared.config`/`shared.signals` precedent) owns `TracerProvider` init, the Kafka header inject/extract helpers, and the shutdown hook logic. `opentelemetry-sdk` + `opentelemetry-exporter-otlp-proto-http` become dependencies of `shared`; only `api`'s `pyproject.toml` additionally adds `opentelemetry-instrumentation-fastapi`.
5. **Local verification / docker-compose**: don't build an ephemeral listener or add new local infra. Drop `docker-compose.yml` (and the docker-compose section of `README.md`) from this repo — it's stale. Verify this feature by running the real services on the existing local k3s cluster (via the sibling `local-env` project's Tilt setup) and confirming a trace actually lands, since the real in-cluster APM Server endpoint is reachable from there. `CLAUDE.md` gets a short informational note that this repo's real local dev target lives in that sibling project (not an operational instruction for other developers to follow verbatim, since the path is specific to this machine).
6. **CLAUDE.md standing directive**: add the instrumentation-required line from the original task (always instrumented with OTel/OTLP to the Elastic APM Server; api's HTTP layer via auto-instrumentation; any new Kafka consumer/background task must wrap processing in an explicit span).

Frontier confirmed empty by the user ("GO ahead") — proceeding to Specify.
