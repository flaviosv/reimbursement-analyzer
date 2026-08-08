# External Integrations

## Integrations

**Kafka (`Request` topic):**

- Type: message broker
- Purpose: durable transport for reimbursement requests between `api` and `publisher`
- Data flow: outbound from `api` (publish), inbound to `publisher` (consume — implemented) — a failed item is also republished here with `retry` incremented
- Protocol: Kafka native protocol via `confluent_kafka` — async producer (`confluent_kafka.aio.AIOProducer`) and async consumer (`confluent_kafka.aio.AIOConsumer`)
- Location: `src/shared/src/shared/producer.py` (generic `publish`/`managed_producer`), `src/api/src/reimbursement/create/producer.py` (envelope building), `src/publisher/src/consumer.py` (consume lifecycle), `src/publisher/src/processing.py` (requeue)
- Authentication: PLAINTEXT by default (local); SASL/TLS supported via `KAFKA_SECURITY_PROTOCOL` + related env vars, read once through `shared.config.load_config().kafka`

**Kafka (`Reimbursement` topic):**

- Type: message broker
- Purpose: durable handoff from `publisher` to the (not-yet-implemented) `agent` decision layer — one message per `reimbursement` row, carrying only its `uuid`
- Data flow: outbound from `publisher` only; inbound consumption is unimplemented — no code subscribes to this topic yet
- Protocol: same as above, message contract `shared.models.ReimbursementEnvelope` (`uuid`, `retry`, `published_at`, `errors`)
- Location: `src/publisher/src/processing.py` (`_insert_and_publish`)
- Authentication: same as the `Request` topic

**PostgreSQL (app database):**

- Type: relational database
- Purpose: system of record for `reimbursement` and `human_review`
- Data flow: read+write from `publisher` (`insert_pending`, `insert_human_review`) via `shared.reimbursement.repository`; `agent` does not yet read or write it
- Protocol: `asyncpg` (async, `publisher`'s runtime read/write path) and `psycopg` (sync, migration runner only)
- Location: `src/api/src/migrations/*.sql`, `src/api/src/migrate.py` (schema); `src/shared/src/shared/reimbursement/repository.py` (runtime access)
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
