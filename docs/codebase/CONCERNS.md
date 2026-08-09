# Codebase Concerns

**Analysis Date:** 2026-08-08

## Tech Debt

**`agent`'s decision policy (the service's core stated purpose) is unimplemented, despite the pipeline plumbing around it now being complete:**
- Issue: `agent` now consumes `Reimbursement`, resolves the row by `uuid`, applies a staleness guard, tolerates a ghost, requeues transient failures, and escalates past its own retry ceiling (`src/agent/src/agent/{consumer,validation}.py`) — but no code path anywhere implements the actual auto-approve/auto-reject/human-review policy from `docs/SCOPE.md`. `langchain`/`langgraph` remain declared, unused dependencies.
- Files: no single file — the gap is the absence of a decision module, not a defect in an existing one.
- Why: this feature deliberately scoped the consume/resolve layer separately from decision logic, per the project's own stated plan to implement them in different sessions.
- Impact: `docker-compose.yml` builds and runs `agent` as if it participates in the full pipeline — a reader could assume the reimbursement decision flow is wired end to end, when the pipeline durably reaches a resolved, decision-ready message and stops there. Its retry-ceiling escalation to `human-review` is a failure-handling safety valve (mirroring `publisher`'s own), not the policy itself — don't conflate the two.
- Fix approach: implement the decision layer (rules + LLM evaluation, recording the decision) as its own feature, consuming what `agent.validation` already resolves.

**Resolved since the last scan — `publisher` and `agent`'s consume/resolve layers are now both fully implemented:** `src/publisher/src/{consumer,processing}.py` consume `Request`, insert a `reimbursement` row, publish to `Reimbursement`, and handle retry/duplicate/escalation; `src/agent/src/agent/{consumer,validation}.py` consume `Reimbursement` and resolve/requeue/escalate — both per `SCOPE.md`. Covered by `src/publisher/tests/{test_consumer,test_processing,test_integration}.py`, `src/agent/tests/{test_consumer,test_validation,test_integration}.py`, plus `src/shared/tests/reimbursement/`. Left here as a record that this entry's scope narrowed twice, not deleted outright.

**`README.md` documents a pre-flatten file layout:**
- Issue: references `src/api/src/api/migrations/` and `uv run --extra migrations python -m api.migrate`, both from before `api`'s directory structure was flattened (loose modules directly under `src/api/src/`, no wrapping `api/` package dir).
- Files: `README.md` (lines ~33, 54, 69)
- Why: the flatten happened during PR review remediation after the README was originally written; the README was not updated alongside it.
- Impact: a new contributor following the README literally would run a command (`python -m api.migrate`) that does not resolve — the correct current invocation is `PYTHONPATH=src/api/src python -m migrate` (see `src/api/Dockerfile`'s `migrate` stage, and `docs/codebase/STACK.md`).
- Fix approach: update the three lines to reflect the current flat layout and command.

**`asyncpg` is a declared dependency with no consumer in `api` (resolved for `publisher` and `agent`):**
- Issue: `asyncpg` is listed in `api`, `agent`, and `publisher`'s `pyproject.toml`. `publisher` and `agent` both now import it for real, via `shared.reimbursement.repository`. Only `api` still declares it with no import anywhere.
- Files: `src/api/pyproject.toml` (unused); `src/shared/src/shared/reimbursement/repository.py` (used, on both `publisher`'s and `agent`'s behalf)
- Why: added ahead of the persistence layer that will use it (the migration runner uses sync `psycopg` instead, since it's a one-shot job, not a request-serving path); `publisher-consume-request` and `agent-consume-reimbursement` are the features that put it to use.
- Impact: none currently for `api` (unused dependency, not a runtime risk).
- Fix approach: `api`'s declaration is unexplained and could likely be dropped — no other action needed, this entry is otherwise resolved.

## Security Considerations

**All secrets flow through one local `.env` file:**
- Risk: DB password, Kafka SASL credentials, and every LangFuse infra/API secret are read from a single `.env`, seeded from `.env.sample`'s documented weak placeholders.
- Files: `.env.sample`, `docker-compose.yml` (`${VAR}` interpolation throughout)
- Current mitigation: `.env.sample`'s own header states these are local-dev-only placeholders; `.gitignore` does not track a real `.env`.
- Recommendations: acceptable for local development as-is; no evidence of a secrets-manager integration path exists for a non-local deployment — worth defining before this ever runs outside a developer's machine.

**No authentication on the public API:**
- Risk: `POST /api/v1/reimbursement` and `/health` have no auth middleware or dependency.
- Files: `src/api/src/main.py`, `src/api/src/reimbursement/create/route.py`
- Current mitigation: none observed.
- Recommendations: expected for a scoped technical test; would need addressing before any real deployment, given the financial domain and stated audit requirements.

## Fragile Areas

**Kafka producer construction is the app's single point of startup failure:**
- Files: `src/api/src/main.py` (`lifespan`), `src/shared/src/shared/producer.py` (`managed_producer`)
- Why fragile: the FastAPI app will not finish starting if the Kafka broker is unreachable at boot (the `AIOProducer` construction happens inside `lifespan`, which every request depends on).
- Common failures: broker not yet healthy when `api` starts — mitigated in compose by `depends_on: kafka: condition: service_healthy`, but a bare `uv run uvicorn` outside compose has no such guard.
- Safe-modification notes: any change to `lifespan` risks breaking startup for every route, not just `reimbursement/create` — test via `src/api/tests/test_main.py`'s `DescribeLifespan` before altering it.
- Test coverage: covered (`test_main.py`), but only for construction/teardown — not for a broker-unreachable-at-startup scenario.

## Test Coverage Gaps

**Decision logic (auto-approve / auto-reject / human-review):**
- What's not tested: nothing — the approval policy described in `docs/SCOPE.md` (auto-approve ≤200, mandatory human-review >2000, reject receipts >90 days old) has no implementing code anywhere in this repo yet. This is distinct from both `publisher`'s and `agent`'s own `retry > 3` → `human-review` escalations (both implemented and fully tested) — those paths preserve a row for a human to decide when the pipeline itself fails, they do not evaluate the request's substance.
- Risk: this is the core stated purpose of the service; it does not exist yet, so there is nothing to test.
- Priority: highest — this is the next major piece of work, not a testing gap in existing code.
- Difficulty to test: n/a until implemented.

**Kafka-container-shared-fixture concurrency:**
- What's not tested: whether `reimbursement/create/`'s session-scoped `KafkaContainer` fixture holds up under `pytest-xdist` parallel workers (the README's parallel-safety claim is documented only for the Postgres fixtures).
- Risk: low today (small test count), but would surface as flaky integration tests if the suite grows and CI adopts `-n auto`.
- Priority: low — revisit if/when CI or `pytest-xdist` is introduced.
- Difficulty to test: moderate — would need a per-test or per-worker Kafka container instead of session-scoped.

## Missing Critical Features

**No CI/CD pipeline:**
- Problem: no `.github/workflows/` or other CI configuration exists — `uv run pytest`, linting, and building are entirely manual/local.
- Current workaround: none; relies on the developer running the suite before pushing.
- Blocks: automated gate-checking on PRs, `docs/codebase/PIPELINE.md` (skipped entirely by this scan for lack of evidence).
- Rough effort: small — a single workflow running `uv sync --all-packages` + `uv run pytest` would cover the current test suite.

**No lint gate:**
- Problem: `ruff` is present locally (evidenced by a `.ruff_cache/` directory) but has no `[tool.ruff]` configuration anywhere in the workspace, and is not run in any automated gate.
- Current workaround: ad hoc, manual `ruff check` invocations during development.
- Blocks: consistent import-ordering and a handful of real, currently-unaddressed findings (e.g. a `TRY004` in `shared/models.py`'s validator, a `BLE001` broad-except in `api/tests/conftest.py`, `B008` on FastAPI's own idiomatic `Depends(...)` default-argument pattern — the last is a known ruff/FastAPI friction point, not a real bug).
- Rough effort: small to add a baseline `[tool.ruff]` config; addressing existing findings is a separate, smaller pass.
