# Agent Model Config Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user — do not proceed without it.**

---

**Design**: `.specs/features/agent-model-config/design.md`
**Status**: Done — all 8 tasks complete, Verifier PASS (see `validation.md`)

---

## Pre-Execute Blocker (must clear before T1 starts)

`docs/codebase/CONCERNS.md`'s "Known Bugs" documents `prompts/analysis.py`/
`prompts/extract_fields.py`'s `str.format()` templates crashing on their own
embedded literal JSON (unescaped `{`/`}`). Confirmed still reproducing right
now:

```
get_analysis_prompt(...)       → KeyError('"status"')
get_extract_fields_prompt(...) → KeyError('\n    "request_id"')
```

Every test that actually *invokes* `Analysis`/`ExtractFields.__call__` (not
just constructs the graph) calls one of these functions internally,
regardless of Groq/Ollama — so T4–T8 below cannot pass their gates while
this is broken. Neither file is touched by this feature's design (prompt
*content*, not model config). This is the same in-progress edit session
that produced (and just fixed) `validation.py`'s syntax error — flagging so
it's cleared the same way before Execute starts, not discovered mid-task.

---

## Test Coverage Matrix

> Generated from `docs/codebase/TESTING.md` (existing, documented project
> guidelines — cited directly, no strong-default fallback needed) plus this
> feature's spec ACs.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| --- | --- | --- | --- | --- |
| `reimbursement` config loading (`AIConfig`/`ModelConfig`/`AgentModelsConfig`, fail-fast) | Unit | All branches: each of the 3 required vars missing → raises naming that var; each of the 3 optional vars missing → documented default; `api_key` proven single-sourced — 1:1 to AMC-06..10, AMC-13, AMC-14 | `packages/reimbursement/tests/test_config.py` | `uv run pytest -m "not integration"` |
| Test fixture infra (autouse Groq env defaults) | none | Proven transitively — every other test that calls `load_agent_config()` passing is the proof | `packages/reimbursement/tests/conftest.py` | build gate only |
| `.env.sample`/`.env` parity (six new keys present, non-blank) | Unit | Both files carry all six keys — 1:1 to AMC-11, AMC-12 | `packages/reimbursement/tests/test_config.py` (co-located parity check) | `uv run pytest -m "not integration"` |
| Decision graph node: `analysis` (`GuardrailVerdict` rename, dead-code cleanup) | Unit (fakes) | 1:1 to AMC-16, AMC-17; existing status-derivation branches (`consistent=True/False`) unchanged | `packages/reimbursement/tests/test_analysis.py` | same |
| Decision graph node: `extract_fields` (model-attribution logging) | Unit (fakes) | 1:1 to AMC-15 | `packages/reimbursement/tests/test_extract_fields.py` | same |
| Full-graph wiring / `build_graph()` (independent per-node Groq models) | Unit (fakes at LLM boundary + one real, no-monkeypatch construction test) | 1:1 to AMC-01..05; explicit distinctness proof (two different `model_name`s reach two different model instances) | `packages/reimbursement/tests/test_agent.py` | same |
| `reimbursement` end-to-end (`Reimbursement` → resolved, via the decision graph) | Integration | Existing scenarios continue passing with renamed strings/schema — no new scenario needed (behavior unchanged) | `packages/reimbursement/tests/test_integration.py` | `uv run pytest` |
| `reimbursement` resolve/requeue/escalate | Unit (fakes) | Existing coverage continues passing with renamed strings — no new scenario needed | `packages/reimbursement/tests/test_validation.py` | `uv run pytest -m "not integration"` |

**Coverage Expectation values** — set from `docs/codebase/TESTING.md`'s own
documented conventions (`Describe*`/`it_*`, one test file per node,
fakes-at-the-boundary), not the skill's strong defaults.

## Gate Check Commands

> Sourced from `docs/codebase/TESTING.md`'s own Gate Check Commands table.

| Gate Level | When to Use | Command |
| --- | --- | --- |
| Quick | After tasks with unit tests only | `uv run pytest packages/reimbursement -m "not integration"` |
| Full | Before considering the feature done / after touching `test_integration.py` | `uv run pytest packages/reimbursement` |
| Build | After the dependency-swap task | `uv sync --all-packages` |

---

## Execution Plan

Phases are ordered and run sequentially — each phase completes before the next begins, and tasks within a phase execute in order.

### Phase 1: Configuration Layer

```
T1 → T2 → T3
```

### Phase 2: Node & Graph Rewiring

```
T4 → T5 → T6
```

### Phase 3: Env Samples & Cross-File Cleanup

```
T7 → T8
```

---

## Task Breakdown

### T1: Replace Ollama fields with `AIConfig`/`ModelConfig`/`AgentModelsConfig` in `reimbursement/config.py`

**What**: Add the three new frozen dataclasses and a `_require_env` helper; rewrite `AgentConfig`/`load_agent_config()` to drop `ollama_model`/`ollama_base_url`/`ollama_timeout_seconds` and read the six new env vars (3 required via `_require_env`, 3 optional via `os.getenv` + documented default).
**Where**: `packages/reimbursement/src/reimbursement/config.py`
**Depends on**: None
**Reuses**: `shared.config`'s frozen-dataclass + `@lru_cache` `load_config()` shape (pattern only, no import)
**Requirement**: AMC-06, AMC-07, AMC-08, AMC-09, AMC-10, AMC-13, AMC-14

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `ModelConfig(model_name, temperature=0.0)`, `AIConfig(api_key, timeout_seconds=30.0)`, `AgentModelsConfig(extract_fields, analysis)` defined
- [x] `AgentConfig.ai: AIConfig` and `.models: AgentModelsConfig` replace the three `ollama_*` fields
- [x] `GROQ_API_KEY`/`EXTRACT_FIELDS_MODEL_NAME`/`ANALYSIS_MODEL_NAME` unset each independently raise a `ValueError` naming that variable
- [x] `AI_TIMEOUT_SECONDS`/`EXTRACT_FIELDS_TEMPERATURE`/`ANALYSIS_TEMPERATURE` unset each independently default to `30.0`/`0.0`/`0.0`
- [x] Both nodes' `ModelConfig`s read from the same `AgentConfig.ai.api_key` (single-source test)
- [x] `test_config.py`'s Ollama-specific tests (`it_defaults_the_ollama_settings_when_unset`, `it_reads_the_ollama_settings_from_the_environment`) replaced with equivalents for the new fields
- [x] Gate check passes: `uv run pytest packages/reimbursement -m "not integration"`
- [x] Test count recorded (no silent deletions vs. pre-task count)

**Tests**: unit
**Gate**: quick

---

### T2: Default Groq env vars in `conftest.py` so unrelated tests don't break

**What**: Add an autouse fixture setting `GROQ_API_KEY`/`EXTRACT_FIELDS_MODEL_NAME`/`ANALYSIS_MODEL_NAME` (and the three optional vars, for determinism) to sane placeholder values via `monkeypatch.setenv`, so every existing test that transitively calls `load_agent_config()` keeps working without setting all six vars itself.
**Where**: `packages/reimbursement/tests/conftest.py`
**Depends on**: T1
**Reuses**: The existing `_clear_config_cache` autouse fixture's pattern (extended, not replaced)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] New autouse fixture sets all six vars to non-empty placeholder values
- [x] Fail-fast tests in `test_config.py` (T1) still pass by explicitly `monkeypatch.delenv`-ing the var under test
- [x] Gate check passes: `uv run pytest packages/reimbursement -m "not integration"`
- [x] Test count recorded (no silent deletions vs. pre-task count)

**Tests**: none (fixture infra — proven transitively by every dependent test passing)
**Gate**: quick

---

### T3: Swap `langchain-ollama` → `langchain-groq` in `pyproject.toml`

**What**: Remove the `langchain-ollama` dependency, add `langchain-groq`, re-sync the workspace.
**Where**: `packages/reimbursement/pyproject.toml`
**Depends on**: None
**Reuses**: N/A
**Requirement**: AMC-03

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `langchain-ollama` no longer listed in `dependencies`
- [x] `langchain-groq` listed (version per whatever `uv add` resolves — no manual pin unless `uv` requires one)
- [x] Build gate passes: `uv sync --all-packages`

**Tests**: none (dependency manifest)
**Gate**: build

---

### T4: `Analysis`/`GuardrailVerdict` — rename `reasoning`→`reason`, drop dead constructor code

**What**: Rename `GuardrailVerdict.reasoning` to `reason`; update `Analysis.__call__`'s `verdict.reasoning`/`decision_reason` reference to `verdict.reason`; remove the on-disk `self._prompt = prompt` line (references an undefined local) and the now-unused `ChatPromptTemplate`/`json` imports from `Analysis.__init__`.
**Where**: `packages/reimbursement/src/reimbursement/agent/nodes/analysis.py`
**Depends on**: None (independent of config/pyproject changes)
**Reuses**: `get_analysis_prompt()` (unchanged call site)
**Requirement**: AMC-16, AMC-17

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `GuardrailVerdict` has exactly two fields: `consistent: bool`, `reason: str`
- [x] `status` derivation (`"auto-approved"`/`"human-review"` from `consistent`) unchanged
- [x] `self._prompt`/`prompt` param and unused imports removed from `Analysis.__init__`
- [x] `test_analysis.py` updated: drops its `PLACEHOLDER_PROMPT` import, asserts `reason` not `reasoning`, "ollama unreachable" test strings renamed to be provider-neutral
- [x] Gate check passes: `uv run pytest packages/reimbursement -m "not integration"`
- [x] Test count recorded (no silent deletions vs. pre-task count)

**Tests**: unit
**Gate**: quick

---

### T5: `ExtractFields` — add `model_name` attribution logging, drop dead constructor code

**What**: Add `model_name: str` to `ExtractFields.__init__`; log it in the node's completion log line (matching `Analysis`'s `model=%s` style); remove the on-disk `self._prompt = prompt` line and unused imports.
**Where**: `packages/reimbursement/src/reimbursement/agent/nodes/extract_fields.py`
**Depends on**: None
**Reuses**: `get_extract_fields_prompt()` (unchanged call site)
**Requirement**: AMC-15

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `ExtractFields.__init__(model, model_name)` stores `model_name`
- [x] Completion log line includes `model=%s` with the configured `model_name`
- [x] `self._prompt`/`prompt` param and unused imports removed
- [x] `test_extract_fields.py` updated: drops its `PLACEHOLDER_PROMPT` import, constructs `ExtractFields` with a `model_name`, asserts the log line includes it, "ollama unreachable" test strings renamed
- [x] Gate check passes: `uv run pytest packages/reimbursement -m "not integration"`
- [x] Test count recorded (no silent deletions vs. pre-task count)

**Tests**: unit
**Gate**: quick

---

### T6: `build_graph()` — two independent Groq-backed models, one per node

**What**: Replace the single shared `f"ollama:{config.ollama_model}"` construction with two independent `init_chat_model(f"groq:{...}", api_key=..., temperature=..., timeout=...)` calls, sourced from `config.models.extract_fields`/`config.models.analysis` and `config.ai`; pass each node's own `model_name` into its constructor; drop the broken `PLACEHOLDER_PROMPT` imports entirely (no longer needed — prompt building lives in `get_*_prompt()`, called from inside each node).
**Where**: `packages/reimbursement/src/reimbursement/agent/agent.py`
**Depends on**: T1, T4, T5 (needs the new config shape and both nodes' new constructor signatures)
**Reuses**: `_wire()`, `get_graph()`, `_langfuse_handlers()`, `decide()` — unchanged
**Requirement**: AMC-01, AMC-02, AMC-04, AMC-05

**Tools**:
- MCP: `context7` (only if `init_chat_model("groq:...")` needs re-verification against a live error during implementation — the shorthand's support was inferred by analogy in Design, not independently confirmed for `"groq"`)
- Skill: NONE

**Done when**:
- [x] `extract_fields`'s model built from `config.models.extract_fields.model_name`/`.temperature`, `analysis`'s from `config.models.analysis.model_name`/`.temperature`, both sharing `config.ai.api_key`/`.timeout_seconds`
- [x] Two differently-configured `model_name`s produce two distinct model instances (explicit distinctness test)
- [x] `PLACEHOLDER_PROMPT` imports removed from `agent.py`
- [x] `test_agent.py` updated: drops its `PLACEHOLDER_PROMPT` import, its `it_wires_the_real_ollama_bound_models_...` test renamed and updated to construct via Groq (relying on T2's conftest defaults, no live network/credentials), new distinctness assertion added
- [x] Zero `ollama`/`Ollama`/`OLLAMA` matches remain in `agent.py`, `config.py`, `pyproject.toml`, `analysis.py`, `extract_fields.py` (running total — full zero-match sweep completes at T8)
- [x] Gate check passes: `uv run pytest packages/reimbursement -m "not integration"`
- [x] Test count recorded (no silent deletions vs. pre-task count)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(reimbursement): build_graph constructs independent Groq models per node`

---

### T7: `.env.sample`/`.env` — settled placeholders for the six new vars

**What**: Add `GROQ_API_KEY`, `AI_TIMEOUT_SECONDS`, `EXTRACT_FIELDS_MODEL_NAME`, `EXTRACT_FIELDS_TEMPERATURE`, `ANALYSIS_MODEL_NAME`, `ANALYSIS_TEMPERATURE` to both files with non-blank placeholder values (`llama-3.3-70b-versatile` for both model-name vars, an obviously-fake string for `GROQ_API_KEY`, `30`/`0.0` for the numeric ones); add a co-located parity test.
**Where**: `.env.sample`, `.env`
**Depends on**: T1 (needs the final var names)
**Reuses**: `.env.sample`'s existing section-comment style (`# --- ... ---`)
**Requirement**: AMC-11, AMC-12

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] Both files carry all six keys with non-blank values
- [x] A parity test (in `test_config.py`) asserts `.env.sample` contains all six keys
- [x] `cp .env.sample .env` on a clean checkout would start the agent without editing model-name/temperature/timeout values (manually verified once, per Success Criteria)
- [x] Gate check passes: `uv run pytest packages/reimbursement -m "not integration"`
- [x] Test count recorded (no silent deletions vs. pre-task count)

**Tests**: unit
**Gate**: quick

---

### T8: Sweep remaining Ollama references; full-suite verification

**What**: Rename the last Ollama-specific wording in `agent_fakes.py` (docstring), `test_integration.py`, and `test_validation.py` (comments + "ollama unreachable" test strings) to be provider-neutral; run the full gate and confirm zero `ollama`/`Ollama`/`OLLAMA` matches anywhere in `packages/reimbursement`.
**Where**: `packages/reimbursement/tests/agent_fakes.py`, `packages/reimbursement/tests/test_integration.py`, `packages/reimbursement/tests/test_validation.py`
**Depends on**: T6
**Reuses**: N/A — wording-only changes, no behavior change
**Requirement**: AMC-05 (final verification), plus closing coverage on AMC-01..17 as a whole

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `agent_fakes.py`'s `FakeStructuredModel` docstring no longer says "Ollama"
- [x] `test_integration.py`/`test_validation.py`'s comments and "ollama unreachable" strings renamed (behavior/assertions unchanged, wording only)
- [x] `grep -ril "ollama" packages/reimbursement` returns zero matches
- [x] Full gate passes: `uv run pytest packages/reimbursement`
- [x] Test count recorded (no silent deletions vs. pre-task count)

**Tests**: unit + integration (existing scenarios, wording-only changes)
**Gate**: full

**Commit**: `chore(reimbursement): remove remaining Ollama references`

---

## Phase Execution Map

```
Phase 1 → Phase 2 → Phase 3

Phase 1:  T1 ──→ T2 ──→ T3
Phase 2:  T4 ──→ T5 ──→ T6
Phase 3:  T7 ──→ T8
```

Execution is strictly sequential — 8 tasks total fits a single batch (≤ ~8), so this runs inline with no sub-agent dispatch.

---

## Task Granularity Check

| Task | Scope | Status |
| --- | --- | --- |
| T1: Config dataclasses + loader | 1 file | ✅ Granular |
| T2: conftest default-env fixture | 1 file | ✅ Granular |
| T3: pyproject dependency swap | 1 file | ✅ Granular |
| T4: `Analysis`/`GuardrailVerdict` rename + cleanup | 1 file (2 cohesive changes: rename + dead-code removal) | ✅ Granular |
| T5: `ExtractFields` attribution + cleanup | 1 file (2 cohesive changes) | ✅ Granular |
| T6: `build_graph()` rewiring | 1 file | ✅ Granular |
| T7: `.env.sample`/`.env` placeholders | 2 files (one cohesive concern: env var parity) | ✅ Granular |
| T8: Ollama-reference sweep | 3 files (one cohesive concern: wording sweep, no behavior change) | ✅ Granular |

---

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| --- | --- | --- | --- |
| T1 | None | None | ✅ Match |
| T2 | T1 | T1 → T2 | ✅ Match |
| T3 | None | T2 → T3 (sequential within Phase 1, no data dependency — ordered for cohesion only) | ✅ Match (phase-order arrow, not a data dependency — noted in task body as "Depends on: None") |
| T4 | None | T3 → T4 (phase-order arrow only) | ✅ Match (task body: "Depends on: None") |
| T5 | None | T4 → T5 (phase-order arrow only) | ✅ Match (task body: "Depends on: None") |
| T6 | T1, T4, T5 | T5 → T6 (diagram shows sequential order; body lists full cross-phase dependency set) | ✅ Match |
| T7 | T1 | T6 → T7 (phase-order arrow; body's real dependency is T1, already satisfied earlier) | ✅ Match |
| T8 | T6 | T7 → T8 (phase-order arrow; body's real dependency is T6, already satisfied earlier) | ✅ Match |

**Note on phase-order-only arrows**: T3, T4, T5's diagram arrows reflect execution order within their phase, not a data dependency — each task body correctly says "Depends on: None" since they touch disjoint files. This is intentional sequencing (grouping cohesive config/node work into phases), not a cross-check violation — no task depends on anything later in the diagram.

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| --- | --- | --- | --- | --- |
| T1: config.py | `reimbursement` config loading | Unit | unit | ✅ OK |
| T2: conftest.py | Test fixture infra | none | none | ✅ OK |
| T3: pyproject.toml | Dependency manifest (not in matrix — no code layer) | — | none | ✅ OK |
| T4: analysis.py | Decision graph node: `analysis` | Unit (fakes) | unit | ✅ OK |
| T5: extract_fields.py | Decision graph node: `extract_fields` | Unit (fakes) | unit | ✅ OK |
| T6: agent.py | Full-graph wiring / `build_graph()` | Unit (fakes + 1 real construction) | unit | ✅ OK |
| T7: .env.sample/.env | `.env.sample`/`.env` parity | Unit | unit | ✅ OK |
| T8: agent_fakes.py, test_integration.py, test_validation.py | `reimbursement` end-to-end (integration) + resolve/requeue/escalate (unit) | Integration + Unit | unit + integration | ✅ OK |

All ✅ — no restructuring needed.

---

## Tools for Execution

Proposed default, given this is backend Python work with no UI/API-design surface: **no MCP beyond ad hoc `context7` for T6 only** (to re-verify the `"groq:<model>"` `init_chat_model` shorthand if it errors during implementation — flagged as a Design-time uncertainty, not confirmed), **no skill** for any task (this is direct code editing, not a specialized workflow like `security-review` or `docs-writer`). Confirm or override before I start Execute.
