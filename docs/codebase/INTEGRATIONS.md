# External Integrations

## Integrations

**Kafka (`Request` topic):**

- Type: message broker
- Purpose: durable transport for reimbursement requests between `api` and `publisher`
- Data flow: outbound from `api` (publish), inbound to `publisher` (consume — implemented) — a failed item is also republished here with `retry` incremented
- Protocol: Kafka native protocol via `confluent_kafka` — async producer (`confluent_kafka.aio.AIOProducer`) and async consumer (`confluent_kafka.aio.AIOConsumer`)
- Location: `packages/shared/src/shared/producer.py` (generic `publish`/`managed_producer`), `packages/api/src/api/reimbursement/create/producer.py` (envelope building), `packages/publisher/src/publisher/consumer.py` (consume lifecycle), `packages/publisher/src/publisher/processing.py` (requeue)
- Authentication: PLAINTEXT by default (local); SASL/TLS supported via `KAFKA_SECURITY_PROTOCOL` + related env vars, read once through `shared.config.load_config().kafka`

**Kafka (`Reimbursement` topic):**

- Type: message broker
- Purpose: durable handoff from `publisher` to `reimbursement`'s resolve layer — one message per `reimbursement` row, carrying only its `uuid`
- Data flow: outbound from `publisher`, inbound to `reimbursement` (consumed — resolves the row by `uuid` into `shared.models.Reimbursement`, then hands it to the `reimbursement/agent/` decision graph via `agent.decide()`). A transient resolve failure is also republished here by `reimbursement` with `retry` incremented, same requeue-to-own-topic shape `publisher` uses on `Request`.
- Protocol: same as above, message contract `shared.models.ReimbursementEnvelope` (`uuid`, `retry`, `published_at`, `errors`)
- Location: `packages/publisher/src/publisher/processing.py` (`_insert_and_publish` → `shared.reimbursement.use_cases.publish_pending`), `packages/reimbursement/src/reimbursement/consumer.py` (consume lifecycle), `packages/reimbursement/src/reimbursement/validation.py` (resolve, requeue)
- Authentication: same as the `Request` topic

**PostgreSQL (app database):**

- Type: relational database
- Purpose: system of record for `reimbursement` and `human_review`
- Data flow: read+write from `publisher` (`publish_pending` → `insert_pending`; `send_human_review` → `insert_human_review`), from `reimbursement` (`get_by_uuid` reads every message; its decision graph's `apply_policies`/`apply_agent_decision` nodes write the final status/decision fields; `escalate_existing` writes only past the resolve-retry ceiling), and now from `api` directly — `GET /api/v1/reimbursement` reads via `fetch_reimbursement_page` (paginated, optionally status-filtered, `LEFT JOIN LATERAL` against `human_review` for each row's most recent review); `GET /api/v1/reimbursement/{uuid}` reads a single row via `get_reimbursement`; `PUT /api/v1/reimbursement/{uuid}` writes via `approve`/`reject` (atomic `UPDATE ... WHERE status = ANY(...) RETURNING`) plus `record_human_review_decision` (`INSERT` into `human_review`), both inside one transaction orchestrated by `review_reimbursement.{approve_reimbursement,reject_reimbursement}` — the first code path that actually writes a `human_review` row — all via `shared.reimbursement.repository`
- Protocol: `asyncpg` (async — `publisher`, `reimbursement`, and now `api`'s runtime read/write path) and `psycopg` (sync, migration runner only)
- Location: `packages/api/src/api/migrations/*.sql`, `packages/api/src/api/migrate.py` (schema); `packages/shared/src/shared/db.py` (pool construction — relocated here from `repository.py`, AD-029); `packages/shared/src/shared/reimbursement/repository.py` (SQL statements); `packages/reimbursement/src/reimbursement/validation.py` (reimbursement service's call sites); `packages/api/src/api/reimbursement/list/route.py`, `packages/api/src/api/reimbursement/update/route.py`, `packages/shared/src/shared/reimbursement/use_cases/{list_reimbursements,review_reimbursement}.py` (api's call sites); `packages/api/src/api/main.py` (pool constructed in `lifespan`), `packages/api/src/api/dependencies.py` (`get_pool`)
- Authentication: `DATABASE_URL` connection string (password via `POSTGRES_PASSWORD`)

**LangFuse (self-hosted, v4):**

- Type: LLM observability platform
- Purpose: tracing backend for `reimbursement`'s LangGraph decision graph (the `agent/` subpackage) — one trace per reimbursement invocation, via `agent.agent._langfuse_handlers()`
- Data flow: `agent.decide()` passes a memoized `langchain.CallbackHandler` into `graph.ainvoke`'s `callbacks` config on every invocation; if the package is missing or the handler can't be constructed, a durable `failure_log` record (`reimbursement.langfuse_fallback`) is written instead — no invocation runs trace-less and unlogged
- Protocol: LangFuse's LangChain callback integration (OTLP under the hood)
- Location: `docker-compose.yml` (`langfuse-web`, `langfuse-worker`, and their own Postgres/ClickHouse/Redis/MinIO); client wiring in `packages/reimbursement/src/reimbursement/agent/agent.py`
- Authentication: project public/secret key pair, auto-provisioned on first boot, passed to the `reimbursement` service as `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_HOST`

**Groq (LLM inference):**

- Type: hosted LLM inference provider (not local/self-hosted)
- Purpose: backs `reimbursement`'s agent decision graph — `extract_fields` and `analysis` nodes each bind their own structured-output model via `init_chat_model("groq:<model>", ...)`, two independent chat model instances (one per node, each with its own `model_name`/`temperature`) instead of one model shared across both — replaces Ollama (AD-032)
- Data flow: outbound HTTPS calls from `build_graph()`'s two chat models to Groq's cloud API; no inbound calls into `reimbursement`. Real data-residency shift from the prior Ollama setup: prompt payloads (including `raw_ocr_text`, which can carry incidental PII) now leave the local Docker network for a third-party cloud API — see `docs/codebase/CONCERNS.md`'s Security Considerations
- Protocol: HTTPS (Groq's REST API)
- Location: `packages/reimbursement/src/reimbursement/config.py` (`AgentConfig.ai: AIConfig`, `AgentConfig.models: AgentModelsConfig`), `packages/reimbursement/src/reimbursement/agent/agent.py` (`build_graph()`)
- Authentication: `GROQ_API_KEY` — required, fails fast at config-load time if unset (unlike Ollama's no-auth local default)

## Background Jobs

| Job | Frequency | Purpose |
| --- | --------- | ------- |
| `migrate` | Once per stack startup (one-shot compose service) | Applies pending SQL migrations before `api`/`reimbursement`/`publisher` start; every dependent service waits on `service_completed_successfully` |

No recurring/scheduled jobs exist. `migrate` is not a queue-backed job — it is a single compose service, gated by dependency ordering, not a cron/queue system.
