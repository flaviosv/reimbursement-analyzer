# Codebase Concerns

**Analysis Date:** 2026-08-08 (refreshed)

## Tech Debt

**`reimbursement`'s decision policy (the service's core stated purpose) is scaffolded but non-functional, despite the pipeline plumbing around it now being complete:**
- Issue: `reimbursement` consumes `Reimbursement`, resolves the row by `uuid`, applies a staleness guard, tolerates a ghost, requeues transient failures, and escalates past its own retry ceiling (`packages/reimbursement/src/{consumer,validation}.py`) — but the actual auto-approve/auto-reject/human-review policy from `docs/SCOPE.md` has no working implementation. A LangGraph scaffold exists under `packages/reimbursement/src/agent/`: `agent.py` builds a `StateGraph` and registers five nodes but adds no edges and never compiles/invokes it; `nodes/{extract_fields,validate,apply_policies,analysis,apply_agent_decision}.py` are each a one-line stub returning their own name; `prompts/{extract_fields,analysis}.py` are empty. `validation.py`'s `RESOLVED` branch never calls any of it.
- Files: `packages/reimbursement/src/agent/{agent.py,nodes/*,prompts/*}` — code exists but does nothing yet.
- Why: this feature deliberately scoped the consume/resolve layer separately from decision logic; the design for the decision layer itself is in progress (`.specs/features/agent-decide-reimbursement/`), and the package was renamed `agent` → `reimbursement` (flattened, `ce80603`) ahead of the decision graph landing inside it.
- Impact: `docker-compose.yml` builds and runs `reimbursement` as if it participates in the full pipeline — a reader could assume the reimbursement decision flow is wired end to end, when the pipeline durably reaches a resolved, decision-ready message and stops there. Its retry-ceiling escalation to `human-review` is a failure-handling safety valve (mirroring `publisher`'s own), not the policy itself — don't conflate the two.
- Fix approach: implement the decision layer (rules + LLM evaluation, recording the decision) per the in-progress design, consuming what `reimbursement.validation` already resolves.

**Resolved since the last scan — `publisher` and `reimbursement`'s consume/resolve layers are now both fully implemented:** `packages/publisher/src/{consumer,processing}.py` consume `Request`, insert a `reimbursement` row, publish to `Reimbursement`, and handle retry/duplicate/escalation; `packages/reimbursement/src/{consumer,validation}.py` consume `Reimbursement` and resolve/requeue/escalate — both per `SCOPE.md`. Covered by `packages/publisher/tests/{test_consumer,test_processing,test_integration}.py`, `packages/reimbursement/tests/{test_consumer,test_validation,test_integration}.py`, plus `packages/shared/tests/reimbursement/`. Left here as a record that this entry's scope narrowed twice, not deleted outright.

**Resolved since the last scan — `GET`/`PUT /api/v1/reimbursement` are now fully implemented:** `packages/api/src/reimbursement/{list,update}/*.py` and `packages/shared/src/shared/reimbursement/use_cases/{list_reimbursements,review_reimbursement}.py` implement listing (status-filtered, paginated) and human-driven approve/reject decision recording. Covered by `packages/api/tests/reimbursement/{list,update}/*.py` and `packages/shared/tests/reimbursement/use_cases/{test_list_reimbursements,test_review_reimbursement}.py`. This also resolves the "`asyncpg` unused in `api`" entry below, and means `human_review` — previously documented as unwritten by either service — is now actively written by `review_reimbursement`'s `record_human_review_decision`.

**Resolved since the last scan — `asyncpg` is now a genuine `api` dependency:** the entry previously here ("`asyncpg` is a declared dependency with no consumer in `api`") no longer applies — `api`'s `GET`/`PUT` endpoints now import it for real, via `shared.db.managed_pool` and `shared.reimbursement.repository`, constructed in `main.py`'s `lifespan` and exposed via `dependencies.get_pool`. Left here as a record, not deleted outright.

**`README.md`'s stated migration path is stale by one path segment (partially resolved since the last scan):**
- Issue: line ~57 states migrations live at `packages/api/src/api/migrations/` — there is no nested `api/` folder on disk; the physical path is `packages/api/src/migrations/`. The migration *command* it documents, `uv run --extra migrations python -m api.migrate`, is now actually correct again — AD-031 switched `api` to a real setuptools package-dir install, so `api.migrate` is once more a valid dotted import, matching `packages/api/Dockerfile`'s `migrate` stage CMD exactly. (Previously flagged as stale in both respects — the command half of that was true only for the brief flat/loose-module period between the initial flatten and AD-031's package-dir fix; left here as a record.)
- Files: `README.md` (line ~57)
- Why: the physical directory description was written before setuptools package-dir mapping existed and never revisited.
- Impact: minor — a reader trying to locate the SQL files by the literal stated path would look one level too deep and not find them; the documented command itself works as written.
- Fix approach: update the one line to `packages/api/src/migrations/`.

## Security Considerations

**All secrets flow through one local `.env` file:**
- Risk: DB password, Kafka SASL credentials, and every LangFuse infra/API secret are read from a single `.env`, seeded from `.env.sample`'s documented weak placeholders.
- Files: `.env.sample`, `docker-compose.yml` (`${VAR}` interpolation throughout)
- Current mitigation: `.env.sample`'s own header states these are local-dev-only placeholders; `.gitignore` does not track a real `.env`.
- Recommendations: acceptable for local development as-is; no evidence of a secrets-manager integration path exists for a non-local deployment — worth defining before this ever runs outside a developer's machine.

**No authentication on the public API:**
- Risk: no route has auth middleware or dependency — this now covers the full HTTP surface, not just intake: `POST /api/v1/reimbursement`, `/health`, `GET /api/v1/reimbursement` (lists every reimbursement, including PII in `original_payload`, to any caller), and `PUT /api/v1/reimbursement/{uuid}` (anyone can approve or reject any reimbursement — the highest-stakes of the four, since it's the endpoint that actually finalizes a financial decision).
- Files: `packages/api/src/main.py`, `packages/api/src/reimbursement/create/route.py`, `packages/api/src/reimbursement/list/route.py`, `packages/api/src/reimbursement/update/route.py`
- Current mitigation: none observed.
- Recommendations: expected for a scoped technical test (also listed as Phase 2 backlog in `docs/SCOPE.md`); would need addressing before any real deployment, given the financial domain and stated audit requirements — `PUT` in particular should not ship unauthenticated past this stage.

## Fragile Areas

**Kafka producer construction is the app's single point of startup failure:**
- Files: `packages/api/src/main.py` (`lifespan`), `packages/shared/src/shared/producer.py` (`managed_producer`)
- Why fragile: the FastAPI app will not finish starting if the Kafka broker is unreachable at boot (the `AIOProducer` construction happens inside `lifespan`, which every request depends on).
- Common failures: broker not yet healthy when `api` starts — mitigated in compose by `depends_on: kafka: condition: service_healthy`, but a bare `uv run uvicorn` outside compose has no such guard.
- Safe-modification notes: any change to `lifespan` risks breaking startup for every route, not just `reimbursement/create` — test via `packages/api/tests/test_main.py`'s `DescribeLifespan` before altering it.
- Test coverage: covered (`test_main.py`), but only for construction/teardown — not for a broker-unreachable-at-startup scenario.

## Test Coverage Gaps

**Decision logic (auto-approve / auto-reject / human-review):**
- What's not tested: the LangGraph scaffold (`packages/reimbursement/src/agent/`) has zero test coverage — every node is a stub, so there's no behavior to assert yet. The approval policy itself, described in `docs/SCOPE.md` (auto-approve ≤200, mandatory human-review >2000, reject receipts >90 days old), has no working implementing code anywhere in this repo. This is distinct from both `publisher`'s and `reimbursement`'s own `retry > 3` → `human-review` escalations (both implemented and fully tested) — those paths preserve a row for a human to decide when the pipeline itself fails, they do not evaluate the request's substance. Also untested: the new `shared.models.Reimbursement` model (`test_models.py` covers `AttemptError`/`ReimbursementEnvelope`/`RequestEnvelope` only).
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

**No lint gate — and now two unconfigured linters, not one:**
- Problem: `ruff` is present locally (evidenced by a `.ruff_cache/` directory) but has no `[tool.ruff]` configuration anywhere in the workspace, and is not run in any automated gate. `pylint>=4.0.6` was newly added to the root `dependency-groups.dev` with no `.pylintrc`/`[tool.pylint]` config and no gate either — the workspace now declares two linters, neither wired to anything.
- Current workaround: ad hoc, manual `ruff check` invocations during development.
- Blocks: consistent import-ordering and a handful of real, currently-unaddressed findings (e.g. a `TRY004` in `shared/models.py`'s validator, a `BLE001` broad-except in `api/tests/conftest.py`, `B008` on FastAPI's own idiomatic `Depends(...)` default-argument pattern — the last is a known ruff/FastAPI friction point, not a real bug).
- Rough effort: small to add a baseline `[tool.ruff]` config; decide whether `pylint` is meant to replace or complement `ruff` before configuring it too — running both unconfigured invites divergent, overlapping findings.
