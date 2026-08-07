# ReimbursementAnalyzer

Mission-critical, event-driven service that decides the outcome of expense reimbursement requests in a financial domain. For each submitted request it produces one of three decisions — auto-approve, auto-reject (with justification), or route to human review — using a combination of deterministic business rules and a probabilistic (LLM/SLM-based) evaluation layer. Because decisions affecting money are partly driven by probabilistic LLM output, full traceability and auditability of every decision (automated or human) is a hard requirement.

> This project is a technical test for ReimbursementAnalyzer.

See [`docs/SCOPE.md`](docs/SCOPE.md) for the full requirements, approval policy, entities, API contracts, and architectural decisions.

## Project layout

A [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) monorepo with a shared kernel and three independently deployable services:

- `src/api` — public HTTP API (FastAPI)
- `src/agent` — LLM-based evaluation layer, consumes from Kafka (LangChain / LangGraph)
- `src/publisher` — consumes requests, persists them, republishes for downstream processing
- `src/shared` — shared kernel: models and code common to the services above

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (manages Python itself — no separate Python install needed)
- [Docker](https://docs.docker.com/get-docker/) and Docker Compose

## Install

```bash
# Install the pinned Python version and sync every workspace member
uv sync --all-packages

# Copy the local-dev environment defaults
cp .env.sample .env
```

## Run

```bash
docker compose up -d
```

This starts the app's Postgres, Kafka (KRaft, single node), the LangFuse observability stack, and the three services (`api`, `agent`, `publisher`).

- API: http://localhost:8000 (health check at `/health`)
- LangFuse: http://localhost:3000

```bash
docker compose down -v
```
