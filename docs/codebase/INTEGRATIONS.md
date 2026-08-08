# External Integrations

## Integrations

**Kafka (`Request` topic):**

- Type: message broker
- Purpose: durable transport for reimbursement requests between `api` and the (not-yet-implemented) downstream `publisher`/`agent` services
- Data flow: outbound from `api` (publish only); inbound consumption is unimplemented
- Protocol: Kafka native protocol via `confluent_kafka` — async producer (`confluent_kafka.aio.AIOProducer`)
- Location: `src/shared/src/shared/producer.py` (generic `publish`/`managed_producer`), `src/api/src/reimbursement/create/producer.py` (envelope building)
- Authentication: PLAINTEXT by default (local); SASL/TLS supported via `KAFKA_SECURITY_PROTOCOL` + related env vars, read once through `shared.config.load_config().kafka`

**PostgreSQL (app database):**

- Type: relational database
- Purpose: system of record for `reimbursement` and `human_review` — schema exists, no code currently reads or writes it
- Data flow: none yet (schema only)
- Protocol: `psycopg` (sync, migration runner only) — `asyncpg` is a declared dependency of `api`/`agent`/`publisher` but unused
- Location: `src/api/src/migrations/*.sql`, `src/api/src/migrate.py`
- Authentication: `DATABASE_URL` connection string (password via `POSTGRES_PASSWORD`)

**LangFuse (self-hosted, v4):**

- Type: LLM observability platform
- Purpose: intended tracing/eval backend for the future `agent` service's LLM calls
- Data flow: not yet wired into any code — `docker-compose.yml` provisions the full stack and bootstraps a project/API-key pair (`LANGFUSE_INIT_*`), but `agent` never references it
- Protocol: n/a yet
- Location: `docker-compose.yml` (`langfuse-web`, `langfuse-worker`, and their own Postgres/ClickHouse/Redis/MinIO)
- Authentication: project public/secret key pair, auto-provisioned on first boot

## Background Jobs

| Job | Frequency | Purpose |
| --- | --------- | ------- |
| `migrate` | Once per stack startup (one-shot compose service) | Applies pending SQL migrations before `api`/`agent`/`publisher` start; every dependent service waits on `service_completed_successfully` |

No recurring/scheduled jobs exist. `migrate` is not a queue-backed job — it is a single compose service, gated by dependency ordering, not a cron/queue system.
