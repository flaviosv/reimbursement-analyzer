# Codebase Concerns

**Analysis Date:** 2026-08-09 (refreshed — agent-model-config)

## Tech Debt

**Resolved since the last scan — `reimbursement`'s decision policy is now implemented and wired end to end:** the entry previously here described the LangGraph scaffold (`packages/reimbursement/src/reimbursement/agent/`) as five stub nodes with empty prompts, never invoked from `validation.py`. That is no longer accurate: `agent.py` compiles a real `StateGraph` (`extract_fields` → `validate` → `apply_policies`, conditionally → `analysis` → `apply_agent_decision`), each node holds real logic (deterministic thresholds in `apply_policies`, an LLM-as-judge guardrail in `analysis`), every LLM call is LangFuse-traced, and `validation.py`'s resolve path calls `agent.decide()` for every resolved row. One test file per node plus a full-graph wiring test (`test_agent.py`) exist. Left here as a record, not deleted outright.

**Resolved since the last scan — dead constructor parameters removed from the two LLM-calling nodes:** the entry previously here described `Analysis.__init__`/`ExtractFields.__init__` as still accepting an unused `prompt: ChatPromptTemplate` parameter, stored as `self._prompt` but never read. Both constructors now take only `model`/`model_name` — the dead parameter, the `self._prompt` assignment, and the corresponding `agent.py`/test `prompt=` kwargs were all removed as part of the agent-model-config feature (AD-032). Left here as a record, not deleted outright.

## Known Bugs

**Resolved since the last scan — `reimbursement.agent.agent` no longer fails to import:** the entry previously here described a `PLACEHOLDER_PROMPT` `ImportError` breaking the whole decision graph and its test suite (`agent.py` importing a name the prompt-refactor renamed to `get_analysis_prompt()`/`get_extract_fields_prompt()`). `agent.py` no longer imports `PLACEHOLDER_PROMPT`; every test file that still did (`test_agent.py`, `test_analysis.py`, `test_extract_fields.py`, `test_integration.py`) was updated in the same pass. Left here as a record, not deleted outright.

**Resolved since the last scan — `prompts/analysis.py`/`prompts/extract_fields.py` no longer crash on their own embedded JSON:** the entry previously here described a `str.format()`/`KeyError` crash on literal JSON embedded in both prompt templates. Both `get_analysis_prompt()`/`get_extract_fields_prompt()` now interpolate via `.replace("{request_data}", str(request_data))`, not `str.format()` — confirmed by direct invocation with no `KeyError`, and covered by the full `packages/reimbursement` test suite (121 passing, including the traceability-correlation-ids and agent-model-config features merged together). Left here as a record, not deleted outright.

**Resolved since the last scan — `publisher` and `reimbursement`'s consume/resolve layers are now both fully implemented:** `packages/publisher/src/publisher/{consumer,processing}.py` consume `Request`, insert a `reimbursement` row, publish to `Reimbursement`, and handle retry/duplicate/escalation; `packages/reimbursement/src/reimbursement/{consumer,validation}.py` consume `Reimbursement` and resolve/requeue/escalate — both per `SCOPE.md`. Covered by `packages/publisher/tests/{test_consumer,test_processing,test_integration}.py`, `packages/reimbursement/tests/{test_consumer,test_validation,test_integration}.py`, plus `packages/shared/tests/reimbursement/`. Left here as a record that this entry's scope narrowed twice, not deleted outright.

**Resolved since the last scan — `GET`/`PUT /api/v1/reimbursement` are now fully implemented:** `packages/api/src/api/reimbursement/{list,update}/*.py` and `packages/shared/src/shared/reimbursement/use_cases/{list_reimbursements,review_reimbursement}.py` implement listing (status-filtered, paginated) and human-driven approve/reject decision recording. Covered by `packages/api/tests/reimbursement/{list,update}/*.py` and `packages/shared/tests/reimbursement/use_cases/{test_list_reimbursements,test_review_reimbursement}.py`. This also resolves the "`asyncpg` unused in `api`" entry below, and means `human_review` — previously documented as unwritten by either service — is now actively written by `review_reimbursement`'s `record_human_review_decision`.

**Resolved since the last scan — `asyncpg` is now a genuine `api` dependency:** the entry previously here ("`asyncpg` is a declared dependency with no consumer in `api`") no longer applies — `api`'s `GET`/`PUT` endpoints now import it for real, via `shared.db.managed_pool` and `shared.reimbursement.repository`, constructed in `main.py`'s `lifespan` and exposed via `dependencies.get_pool`. Left here as a record, not deleted outright.

**Resolved since the last scan — `extract_fields` no longer returns a non-ISO `receipts_date`:** Groq's `extract_fields` occasionally returned a non-ISO `receipts_date` (e.g. `"10/08/2026"` or a full datetime), which failed Groq's tool-call schema validation with a 400 and left the row pending indefinitely — `extract_fields`'s failure path has no retry. Reproduced during real-Groq e2e validation. The extraction prompt (`packages/reimbursement/src/reimbursement/agent/prompts/extract_fields.py`) now explicitly instructs `YYYY-MM-DD`, date-only output regardless of the source field's format, and to return empty rather than guess when the source format is ambiguous. Left here as a record, not deleted outright.

**Resolved since the last scan — the 90-day reject rule can no longer be defeated by a backdated `submitted_at`:** the rule previously computed receipt staleness as `submitted_at - receipts_date`, where `submitted_at` is a client-supplied field in the request payload (only DB-constrained against future dates) — a requester could backdate `submitted_at` to always clear the reject rule regardless of the receipt's real age. `packages/reimbursement/src/reimbursement/agent/nodes/apply_policies.py` now computes staleness against the server clock (`datetime.now(UTC).date()`) instead; `submitted_at` is no longer read by this node at all, and a regression test asserts a backdated `submitted_at` can no longer defeat the rule. Left here as a record, not deleted outright.

**Resolved since the last scan — `README.md`'s stated migration path now matches the real physical path:** the entry previously here flagged `README.md`'s stated `.../api/migrations/` path as one segment deeper than the real, flat `setuptools`+`package-dir` layout. AD-031's amendment (`setuptools` → `uv_build`, nested src-layout) moved `api`'s real modules to `packages/api/src/api/`, which now matches what `README.md` always described — both `README.md` and this doc set were also swept for the `src/` → `packages/` container rename. The migration *command* (`uv run --extra migrations python -m api.migrate`) is unaffected and still correct. Left here as a record, not deleted outright.

## Security Considerations

**All secrets flow through one local `.env` file:**
- Risk: DB password, Kafka SASL credentials, and every LangFuse infra/API secret are read from a single `.env`, seeded from `.env.sample`'s documented weak placeholders.
- Files: `.env.sample`, `docker-compose.yml` (`${VAR}` interpolation throughout)
- Current mitigation: `.env.sample`'s own header states these are local-dev-only placeholders; `.gitignore` does not track a real `.env`.
- Recommendations: acceptable for local development as-is; no evidence of a secrets-manager integration path exists for a non-local deployment — worth defining before this ever runs outside a developer's machine.

**Reimbursement prompt payloads now leave the local network for Groq's cloud API:**
- Risk: Groq is a third-party hosted inference provider, not a self-hosted, host-machine, local-network-only service like Ollama was (AD-032). Prompt payloads — `raw_ocr_text` in particular, which can carry incidental PII embedded in receipt text — now transit to Groq's cloud API for both LLM nodes' calls. This is a real data-residency/compliance-posture change, not just a hostname swap.
- Files: `packages/reimbursement/src/reimbursement/agent/agent.py` (the two `init_chat_model("groq:...")` call sites), `packages/reimbursement/src/reimbursement/agent/nodes/extract_fields.py` (the payload sent to `extract_fields`'s prompt)
- Current mitigation: `submitted_by` is filtered out of the payload before the extraction prompt is built (`extract_fields.py`'s `ExtractFields.__call__`); no redaction/scrubbing exists for `raw_ocr_text` today.
- Recommendations: this was flagged as a real compliance-posture question during design; the project owner (AD-032, `.specs/STATE.md`) chose to proceed without adding scrubbing/redaction as part of this change — worth a data-residency/compliance review before Groq is used with real, non-synthetic receipt data.

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
