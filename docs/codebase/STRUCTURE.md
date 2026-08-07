# Project structure

**Root:** `/Users/flaviostudart/Projects/Personal/tests/reimbursementanalyzer`

## Directory tree

```
reimbursementanalyzer/
├── docs/
│   ├── assets/               # Lifecycle/processing diagrams (PNG)
│   ├── codebase/             # This context-file set
│   ├── original/             # Requirements.pdf + sample.json (source data)
│   ├── SCOPE.md              # Authoritative requirements & decisions
│   └── SCOPE_GAP_ANALYSIS.md # Traceability check vs Requirements.pdf
├── src/
│   ├── agent/                # LLM evaluation service (Kafka consumer)
│   │   ├── src/agent/        #   consumer.py
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   ├── api/                  # Public HTTP API (FastAPI)
│   │   ├── src/api/          #   main.py
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   ├── publisher/            # Persists requests, republishes (consumer)
│   │   ├── src/publisher/    #   consumer.py
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   └── shared/               # Shared kernel (no Dockerfile — library only)
│       └── src/shared/       #   models.py, py.typed
├── docker-compose.yml        # Full local stack
├── pyproject.toml            # uv workspace root (members = src/*)
└── uv.lock                   # Single lockfile for the whole workspace
```

## Monorepo package map

uv workspace; every member uses the src layout
(`src/<package>/src/<package>/`). Services depend on `shared` via
`[tool.uv.sources] shared = { workspace = true }`.

| Package | Path | Responsibility |
| ------- | ---- | -------------- |
| agent | `src/agent` | Decision engine: consumes reimbursements, applies deterministic + LLM layers |
| api | `src/api` | HTTP ingress: accepts requests, publishes to Kafka |
| publisher | `src/publisher` | Consumes raw requests, persists, republishes downstream |
| shared | `src/shared` | Shared kernel: Pydantic models common to all services (typed, ships `py.typed`) |

## Where things live

- Data models: `src/shared/src/shared/models.py`
- HTTP routes: `src/api/src/api/main.py`
- Kafka consumers: `src/{agent,publisher}/src/*/consumer.py`
- Local infra: `docker-compose.yml` (root)
- Requirements and design decisions: `docs/SCOPE.md`

## Special directories

- `docs/original/` — immutable inputs: the requirements PDF and the sample
  payload dataset (`sample.json`) the agent must handle.
- Dockerfiles build with the **repo root** as context (see
  `build.context` in `docker-compose.yml`) so each image can copy `uv.lock`,
  sibling manifests, and `src/shared`.
