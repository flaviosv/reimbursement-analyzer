# Testing infrastructure

**There is no test infrastructure yet.** No test framework is declared in any
manifest, no test files exist, and no test command is defined.
`.dockerignore` anticipates `pytest`/`mypy`/`ruff` cache directories, but
none of those tools is configured.

`docs/SCOPE.md` records the intent: unit and integration tests will be
AI-generated and manually validated, given the time constraints of the test.

## Test coverage matrix

| Code layer | Required test type | Location pattern | Run command |
| ---------- | ------------------ | ---------------- | ----------- |
| Decision engine (`src/agent`) | none yet | — | — |
| HTTP API (`src/api`) | none yet | — | — |
| Persistence bridge (`src/publisher`) | none yet | — | — |
| Shared kernel (`src/shared`) | none yet | — | — |

## Gate check commands

None available. Until a test runner lands, the only executable check is
booting the stack (`docker compose up -d`) and hitting
`http://localhost:8000/health`.

The absence of tests in a mission-critical financial service is tracked as
the top item in `CONCERNS.md`.
