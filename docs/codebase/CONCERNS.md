# Codebase Concerns

**Analysis Date:** 2026-08-09 (refreshed)

## Tech Debt

**Resolved since the last scan — `reimbursement`'s decision policy is now implemented and wired end to end:** the entry previously here described the LangGraph scaffold (`packages/reimbursement/src/reimbursement/agent/`) as five stub nodes with empty prompts, never invoked from `validation.py`. That is no longer accurate: `agent.py` compiles a real `StateGraph` (`extract_fields` → `validate` → `apply_policies`, conditionally → `analysis` → `apply_agent_decision`), each node holds real logic (deterministic thresholds in `apply_policies`, an LLM-as-judge guardrail in `analysis`), every LLM call is LangFuse-traced, and `validation.py`'s resolve path calls `agent.decide()` for every resolved row. One test file per node plus a full-graph wiring test (`test_agent.py`) exist. Left here as a record, not deleted outright — see the Known Bugs entries below for the 4 test files that still don't collect.

**Resolved since the last scan — the dead `prompt` constructor parameter is gone:** the entry previously here described `Analysis.__init__`/`ExtractFields.__init__` as still accepting an unused `prompt: ChatPromptTemplate` parameter. That is no longer accurate — both constructors have dropped it entirely (`Analysis.__init__(self, model, model_name)`, `ExtractFields.__init__(self, model)`); message-building lives in `get_analysis_prompt()`/`get_extract_fields_prompt()` in the sibling `prompts/` modules, called directly from each node's `__call__`. Left here as a record, not deleted outright.

## Known Bugs

**4 test files (not `agent.py` itself) still import a name the prompt refactor removed:**
- Symptoms / Trigger: `agent.py` itself imports cleanly today (`uv run python -c "import reimbursement.agent.agent"` succeeds, no error) and no longer references `PLACEHOLDER_PROMPT` anywhere. What's still broken, confirmed via `uv run pytest packages/reimbursement/tests/`: exactly 4 test files raise `ImportError: cannot import name 'PLACEHOLDER_PROMPT' from 'reimbursement.agent.prompts.analysis'` (or the `extract_fields` equivalent) at collection time, and also construct `ExtractFields`/`Analysis` with a `prompt=` kwarg those classes no longer accept.
- Files: `packages/reimbursement/tests/{test_agent,test_analysis,test_extract_fields,test_integration}.py` — left over from the same prompt-authoring refactor (module-level constants renamed to `_ANALYSIS_PROMPT`/`_EXTRACT_FIELDS_PROMPT`, public surface replaced by `get_analysis_prompt()`/`get_extract_fields_prompt()`), now committed to `main` without these 4 files being updated to match. Every other file in `packages/reimbursement/tests/` — including `conftest.py` (which imports `agent.agent.get_graph`), `test_validate.py`, `test_apply_policies.py`, `test_apply_agent_decision.py`, `test_config.py`, `test_consumer.py`, `test_models.py`, `test_validation.py`, `test_langfuse.py` — collects and runs fine.
- Workaround: none currently.
- Root cause: the 4 files' `PLACEHOLDER_PROMPT` imports and `prompt=` construction kwargs were not updated when the prompt modules were refactored. Ownership: this is the confirmed scope of the concurrently in-flight sibling feature `agent-model-config` (`.specs/features/agent-model-config/tasks.md`'s T1/T4/T5/T6 explicitly own fixing these same 4 files; see also `.specs/STATE.md`'s AD-032/AD-034), not a fix to make from this doc set.

**Resolved since the last scan — `prompts/analysis.py`/`prompts/extract_fields.py`'s templates no longer crash on their own embedded JSON:** the entry previously here described both files rendering via `str.format()`, which raised `KeyError` on their own embedded literal JSON braces. That is no longer accurate — both now render via `.replace("{request_data}", str(request_data))` (confirmed by reading both files), which doesn't parse braces at all, so the collision this entry described cannot occur as written. Left here as a record, not deleted outright.

**Resolved since the last scan — `publisher` and `reimbursement`'s consume/resolve layers are now both fully implemented:** `packages/publisher/src/publisher/{consumer,processing}.py` consume `Request`, insert a `reimbursement` row, publish to `Reimbursement`, and handle retry/duplicate/escalation; `packages/reimbursement/src/reimbursement/{consumer,validation}.py` consume `Reimbursement` and resolve/requeue/escalate — both per `SCOPE.md`. Covered by `packages/publisher/tests/{test_consumer,test_processing,test_integration}.py`, `packages/reimbursement/tests/{test_consumer,test_validation,test_integration}.py`, plus `packages/shared/tests/reimbursement/`. Left here as a record that this entry's scope narrowed twice, not deleted outright.

**Resolved since the last scan — `GET`/`PUT /api/v1/reimbursement` are now fully implemented:** `packages/api/src/api/reimbursement/{list,update}/*.py` and `packages/shared/src/shared/reimbursement/use_cases/{list_reimbursements,review_reimbursement}.py` implement listing (status-filtered, paginated) and human-driven approve/reject decision recording. Covered by `packages/api/tests/reimbursement/{list,update}/*.py` and `packages/shared/tests/reimbursement/use_cases/{test_list_reimbursements,test_review_reimbursement}.py`. This also resolves the "`asyncpg` unused in `api`" entry below, and means `human_review` — previously documented as unwritten by either service — is now actively written by `review_reimbursement`'s `record_human_review_decision`.

**Resolved since the last scan — `asyncpg` is now a genuine `api` dependency:** the entry previously here ("`asyncpg` is a declared dependency with no consumer in `api`") no longer applies — `api`'s `GET`/`PUT` endpoints now import it for real, via `shared.db.managed_pool` and `shared.reimbursement.repository`, constructed in `main.py`'s `lifespan` and exposed via `dependencies.get_pool`. Left here as a record, not deleted outright.

**Resolved since the last scan — `README.md`'s stated migration path now matches the real physical path:** the entry previously here flagged `README.md`'s stated `.../api/migrations/` path as one segment deeper than the real, flat `setuptools`+`package-dir` layout. AD-031's amendment (`setuptools` → `uv_build`, nested src-layout) moved `api`'s real modules to `packages/api/src/api/`, which now matches what `README.md` always described — both `README.md` and this doc set were also swept for the `src/` → `packages/` container rename. The migration *command* (`uv run --extra migrations python -m api.migrate`) is unaffected and still correct. Left here as a record, not deleted outright.

## Security Considerations

**All secrets flow through one local `.env` file:**
- Risk: DB password, Kafka SASL credentials, and every LangFuse infra/API secret are read from a single `.env`, seeded from `.env.sample`'s documented weak placeholders.
- Files: `.env.sample`, `docker-compose.yml` (`${VAR}` interpolation throughout)
- Current mitigation: `.env.sample`'s own header states these are local-dev-only placeholders; `.gitignore` does not track a real `.env`.
- Recommendations: acceptable for local development as-is; no evidence of a secrets-manager integration path exists for a non-local deployment — worth defining before this ever runs outside a developer's machine.

**No authentication on the public API:**
- Risk: no route has auth middleware or dependency — this now covers the full HTTP surface, not just intake: `POST /api/v1/reimbursement`, `/health`, `GET /api/v1/reimbursement` (lists every reimbursement, including PII in `original_payload`, to any caller), and `PUT /api/v1/reimbursement/{uuid}` (anyone can approve or reject any reimbursement — the highest-stakes of the four, since it's the endpoint that actually finalizes a financial decision).
- Files: `packages/api/src/api/main.py`, `packages/api/src/api/reimbursement/create/route.py`, `packages/api/src/api/reimbursement/list/route.py`, `packages/api/src/api/reimbursement/update/route.py`
- Current mitigation: none observed.
- Recommendations: expected for a scoped technical test (also listed as Phase 2 backlog in `docs/SCOPE.md`); would need addressing before any real deployment, given the financial domain and stated audit requirements — `PUT` in particular should not ship unauthenticated past this stage.

## Fragile Areas

**Kafka producer construction is the app's single point of startup failure:**
- Files: `packages/api/src/api/main.py` (`lifespan`), `packages/shared/src/shared/producer.py` (`managed_producer`)
- Why fragile: the FastAPI app will not finish starting if the Kafka broker is unreachable at boot (the `AIOProducer` construction happens inside `lifespan`, which every request depends on).
- Common failures: broker not yet healthy when `api` starts — mitigated in compose by `depends_on: kafka: condition: service_healthy`, but a bare `uv run uvicorn` outside compose has no such guard.
- Safe-modification notes: any change to `lifespan` risks breaking startup for every route, not just `reimbursement/create` — test via `packages/api/tests/test_main.py`'s `DescribeLifespan` before altering it.
- Test coverage: covered (`test_main.py`), but only for construction/teardown — not for a broker-unreachable-at-startup scenario.

## Test Coverage Gaps

**Decision logic test suite is currently uncollectable:**
- What's not tested: nothing right now — every test for `reimbursement/agent/` (one file per node, `test_agent.py`'s full-graph wiring, `test_langfuse.py`) fails at collection because of the `PLACEHOLDER_PROMPT` import bug (see Known Bugs above). The tests themselves are comprehensive once the import is fixed; this is a currently-broken state, not a coverage gap in existing, working code.
- Risk: the entire decision layer — the service's core stated purpose — has no verifiable behavior until the import is fixed.
- Priority: highest — blocks running any test in `packages/reimbursement/tests/`.
- Difficulty to test: n/a — this is a bug fix, not a test-writing task.

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
