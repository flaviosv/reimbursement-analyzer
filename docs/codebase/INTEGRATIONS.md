# External Integrations

## Integrations

**Kafka (`Request` topic):**

- Type: message broker
- Purpose: durable transport for reimbursement requests between `api` and `publisher`
- Data flow: outbound from `api` (publish), inbound to `publisher` (consume — implemented) — a failed item is also republished here with `retry` incremented
- Protocol: Kafka native protocol via `confluent_kafka` — async producer (`confluent_kafka.aio.AIOProducer`) and async consumer (`confluent_kafka.aio.AIOConsumer`)
- Location: `packages/shared/src/shared/producer.py` (generic `publish`/`managed_producer`), `packages/api/src/reimbursement/create/producer.py` (envelope building), `packages/publisher/src/consumer.py` (consume lifecycle), `packages/publisher/src/processing.py` (requeue)
- Authentication: PLAINTEXT by default (local); SASL/TLS supported via `KAFKA_SECURITY_PROTOCOL` + related env vars, read once through `shared.config.load_config().kafka`

**Kafka (`Reimbursement` topic):**

- Type: message broker
- Purpose: durable handoff from `publisher` to `reimbursement`'s resolve layer — one message per `reimbursement` row, carrying only its `uuid`
- Data flow: outbound from `publisher`, inbound to `reimbursement` (consumed — resolves the row by `uuid` into `shared.models.Reimbursement`; no decision policy consumes it yet — the LangGraph scaffold under `reimbursement/agent/` isn't called — only the resolve/staleness/escalation layer). A transient resolve failure is also republished here by `reimbursement` with `retry` incremented, same requeue-to-own-topic shape `publisher` uses on `Request`.
- Protocol: same as above, message contract `shared.models.ReimbursementEnvelope` (`uuid`, `retry`, `published_at`, `errors`)
- Location: `packages/publisher/src/processing.py` (`_insert_and_publish` → `shared.reimbursement.use_cases.publish_pending`), `packages/reimbursement/src/consumer.py` (consume lifecycle), `packages/reimbursement/src/validation.py` (resolve, requeue)
- Authentication: same as the `Request` topic

**PostgreSQL (app database):**

- Type: relational database
- Purpose: system of record for `reimbursement` and `human_review`
- Data flow: read+write from `publisher` (`publish_pending` → `insert_pending`; `send_human_review` → `insert_human_review`), from `reimbursement` (`get_by_uuid` reads every message; `escalate_existing` writes only past the retry ceiling), and now from `api` directly — `GET /api/v1/reimbursement` reads via `fetch_reimbursement_page` (paginated, optionally status-filtered, `LEFT JOIN LATERAL` against `human_review` for each row's most recent review); `PUT /api/v1/reimbursement/{uuid}` writes via `approve`/`reject` (atomic `UPDATE ... WHERE status = ANY(...) RETURNING`) plus `record_human_review_decision` (`INSERT` into `human_review`), both inside one transaction orchestrated by `review_reimbursement.{approve_reimbursement,reject_reimbursement}` — the first code path that actually writes a `human_review` row — all via `shared.reimbursement.repository`
- Protocol: `asyncpg` (async — `publisher`, `reimbursement`, and now `api`'s runtime read/write path) and `psycopg` (sync, migration runner only)
- Location: `packages/api/src/migrations/*.sql`, `packages/api/src/migrate.py` (schema); `packages/shared/src/shared/db.py` (pool construction — relocated here from `repository.py`, AD-029); `packages/shared/src/shared/reimbursement/repository.py` (SQL statements); `packages/reimbursement/src/validation.py` (reimbursement service's call sites); `packages/api/src/reimbursement/list/route.py`, `packages/api/src/reimbursement/update/route.py`, `packages/shared/src/shared/reimbursement/use_cases/{list_reimbursements,review_reimbursement}.py` (api's call sites); `packages/api/src/main.py` (pool constructed in `lifespan`), `packages/api/src/dependencies.py` (`get_pool`)
- Authentication: `DATABASE_URL` connection string (password via `POSTGRES_PASSWORD`)

**LangFuse (self-hosted, v4):**

- Type: LLM observability platform
- Purpose: intended tracing/eval backend for `reimbursement`'s LangGraph decision graph (the `agent/` subpackage) — one trace per reimbursement, per the in-progress design (`.specs/features/agent-decide-reimbursement/design.md`)
- Data flow: not yet wired into any code — `docker-compose.yml` provisions the full stack and bootstraps a project/API-key pair (`LANGFUSE_INIT_*`), but `reimbursement` never references it
- Protocol: n/a yet
- Location: `docker-compose.yml` (`langfuse-web`, `langfuse-worker`, and their own Postgres/ClickHouse/Redis/MinIO)
- Authentication: project public/secret key pair, auto-provisioned on first boot

## Background Jobs

| Job | Frequency | Purpose |
| --- | --------- | ------- |
| `migrate` | Once per stack startup (one-shot compose service) | Applies pending SQL migrations before `api`/`reimbursement`/`publisher` start; every dependent service waits on `service_completed_successfully` |

No recurring/scheduled jobs exist. `migrate` is not a queue-backed job — it is a single compose service, gated by dependency ordering, not a cron/queue system.
