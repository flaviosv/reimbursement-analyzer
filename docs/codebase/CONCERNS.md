# Codebase Concerns

**Analysis Date:** 2026-08-08

## Tech Debt

**`agent` is an unimplemented stub, despite being wired as if functional:**
- Issue: the service's entire implementation is a `consumer.py` that subscribes to a placeholder topic (`sample-topic`), parses `shared.models.SampleMessage`, and `print()`s it. No rules engine, no LLM evaluation, no consumption of the real `Reimbursement` topic `publisher` now publishes to.
- Files: `src/agent/src/agent/consumer.py`
- Why: scaffolded ahead of the real infrastructure/decision work (per the file's own comment: "Placeholder topic name for structural validation ahead of the real infra setup"). `publisher` was in the same state as of the last scan; it has since been implemented (see below) — `agent` has not.
- Impact: `docker-compose.yml` builds and runs it as if it participates in the pipeline — a reader could assume the reimbursement decision flow is wired end to end, when the pipeline durably reaches a `Reimbursement` Kafka message and stops there.
- Fix approach: implement `agent` (consume `Reimbursement`, run rules + LLM evaluation, record the decision) as its own feature.

**Resolved since the last scan — `publisher` is now fully implemented:** `src/publisher/src/{consumer,processing}.py` consume `Request`, insert a `reimbursement` row, publish to `Reimbursement`, and handle retry/duplicate/escalation per `SCOPE.md`. Covered by `src/publisher/tests/{test_consumer,test_processing,test_integration}.py` plus `src/shared/tests/reimbursement/`. Left here as a record that this entry's scope narrowed, not deleted outright.

**`README.md` documents a pre-flatten file layout:**
- Issue: references `src/api/src/api/migrations/` and `uv run --extra migrations python -m api.migrate`, both from before `api`'s directory structure was flattened (loose modules directly under `src/api/src/`, no wrapping `api/` package dir).
- Files: `README.md` (lines ~33, 54, 69)
- Why: the flatten happened during PR review remediation after the README was originally written; the README was not updated alongside it.
- Impact: a new contributor following the README literally would run a command (`python -m api.migrate`) that does not resolve — the correct current invocation is `PYTHONPATH=src/api/src python -m migrate` (see `src/api/Dockerfile`'s `migrate` stage, and `docs/codebase/STACK.md`).
- Fix approach: update the three lines to reflect the current flat layout and command.

**`asyncpg` is a declared dependency with no consumer in `agent` (resolved for `publisher`):**
- Issue: `asyncpg` is listed in `api`, `agent`, and `publisher`'s `pyproject.toml`. `publisher` now imports it for real, via `shared.reimbursement.repository`. `api` and `agent` still declare it with no import anywhere.
- Files: `src/api/pyproject.toml`, `src/agent/pyproject.toml` (unused); `src/shared/src/shared/reimbursement/repository.py` (used, on `publisher`'s behalf)
- Why: added ahead of the persistence layer that will use it (the migration runner uses sync `psycopg` instead, since it's a one-shot job, not a request-serving path); `publisher-consume-request` is the feature that put it to use.
- Impact: none currently for `api`/`agent` (unused dependency, not a runtime risk) — worth closing once `agent` starts reading/writing Postgres, and worth asking whether `api` needs it at all.
- Fix approach: no action needed until `agent`'s persistence work begins; revisit then. `api`'s declaration is unexplained and could likely be dropped.

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
- What's not tested: nothing — the approval policy described in `docs/SCOPE.md` (auto-approve ≤200, mandatory human-review >2000, reject receipts >90 days old) has no implementing code anywhere in this repo yet. This is distinct from `publisher`'s own `retry > 3` → `human-review` escalation (which is implemented and tested) — that path preserves a row for a human to decide, it does not itself decide anything.
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
