# Code conventions

Observed from the current codebase (small sample — four Python modules,
three Dockerfiles, compose file). Expect this file to grow as the
implementation lands.

## Naming conventions

- **Branches:** `<type>/<issue-number>-<snake_case_description>` — examples:
  `feature/3-api_receive_reimbursement_endpoint`, `docs/scope_documentation`.
- **Commits:** Conventional Commits — examples: `feat: bootstrap uv
  monorepo…`, `fix: address code review findings on PR #2`,
  `docs(scope): WIP - Scope V1`.
- **Constants:** UPPER_SNAKE at module top — examples: `TOPIC`, `QUEUE`.
- **Classes:** PascalCase Pydantic models — examples: `HealthStatus`,
  `SampleMessage`.
- **Files/modules:** short snake_case nouns — examples: `main.py`,
  `consumer.py`, `models.py`.
- **Packages:** single lowercase words (`agent`, `api`, `publisher`,
  `shared`), src layout `src/<pkg>/src/<pkg>/`.
- **Kafka consumer group ids:** `<service>-<purpose>-consumer` — example:
  `agent-sample-consumer`.

## Code organization

- Imports: stdlib first, then third-party, then `shared.*` — plain `import`
  / `from … import` with a blank line between groups.
- Entry points: module-level `main()` guarded by
  `if __name__ == "__main__":`, run as `python -m <pkg>.<module>`.
- Configuration: environment variables read with `os.environ.get(name,
  local_default)` — no settings framework yet.

## Type safety

- Full type hints, including `-> None` on procedures.
- `shared` ships a `py.typed` marker.
- Data shapes are Pydantic v2 models; parsing via `model_validate_json()`.

## Error handling

Current code: consumer errors are printed and the loop continues; resources
are closed in `finally`. The target retry/park/file-log strategy lives in
`ARCHITECTURE.md`.

## Comments

Comments explain *constraints and rationale*, not mechanics — the
Dockerfiles and `docker-compose.yml` carry detailed "why" comments (build
caching, volume ownership, port-scoping exceptions), while Python code has
almost none beyond placeholder markers.

## Documentation pattern

- `docs/SCOPE.md` holds requirements, API contracts, and decision records.
- `docs/codebase/` holds this generated context set.
- Swagger/OpenAPI exposure is planned per `docs/SCOPE.md` (FastAPI provides
  it once routes exist).
