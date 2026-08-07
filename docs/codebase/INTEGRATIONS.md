# External integrations

The only external integration today is LangFuse. Kafka and PostgreSQL are
infrastructure dependencies (see `ARCHITECTURE.md`). There are no webhooks
and no background jobs.

## Integrations

**LangFuse (v4, self-hosted):**

- Type: LLM observability / tracing
- Purpose: trace every step the agent takes so decisions driven by
  probabilistic output stay auditable and reproducible
- Data flow: outbound (agent → LangFuse)
- Protocol: SDK/HTTP against `LANGFUSE_HOST` (in-cluster:
  `http://langfuse-web:3000`)
- Location: full stack self-hosted in `docker-compose.yml` (web, worker,
  Postgres 17, ClickHouse, Redis, MinIO); agent container receives host and
  keys via environment
- Authentication: project API key pair, auto-provisioned on first boot via
  `LANGFUSE_INIT_*` variables; the agent reuses the same pair
- Status: wiring only — no LangFuse SDK calls exist in code yet. Target
  behavior (`docs/SCOPE.md`): tracing with file-logging fallback when
  LangFuse is unavailable

**LLM / SLM provider (planned):**

- Purpose: probabilistic field extraction from non-standard payloads, plus
  an LLM-as-judge consistency check and a cheap-model side note for human
  reviewers
- Status: not selected or integrated. `langchain`/`langgraph` are installed
  in the agent, but no model provider dependency or client code exists.
  `docs/SCOPE.md` directs choosing a small model to reduce cost, one prompt
  per reimbursement, structured JSON output, and PII minimization in prompts
