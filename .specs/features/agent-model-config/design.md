# Agent Model Config Design

**Spec**: `.specs/features/agent-model-config/spec.md`
**Status**: Implemented — see `validation.md`

---

## Architecture Overview

No new components — this extends `reimbursement`'s existing config layer and
graph-construction code in place, the same way `shared.config`'s
`KafkaConfig`/`DatabaseConfig` already extend `Config`.

```mermaid
graph TD
    ENV[".env / process env"] --> LAC["load_agent_config()\n(reimbursement/config.py)"]
    LAC --> AC["AgentConfig\n.ai: AIConfig\n.models: AgentModelsConfig"]
    AC -->|ai.api_key, ai.timeout_seconds\nmodels.extract_fields| BG["build_graph()\n(agent/agent.py)"]
    AC -->|ai.api_key, ai.timeout_seconds\nmodels.analysis| BG
    BG -->|"init_chat_model(\"groq:<model>\", ...)"| EFM["extract_fields chat model\n(langchain-groq)"]
    BG -->|"init_chat_model(\"groq:<model>\", ...)"| AM["analysis chat model\n(langchain-groq)"]
    EFM --> EF["ExtractFields node"]
    AM --> AN["Analysis node"]
    EF --> GROQ[("Groq API")]
    AN --> GROQ
```

Both chat models are built once per process inside `build_graph()` (unchanged
`@lru_cache`d `get_graph()` singleton, AD-023's shape) — same lifecycle as
today's two Ollama-backed models, just sourced from two independent
`ModelConfig`s instead of one shared `ollama_model`.

---

## Code Reuse Analysis

### Existing Components to Leverage

| Component | Location | How to Use |
| --- | --- | --- |
| `KafkaConfig`/`DatabaseConfig` frozen-dataclass + `@lru_cache` `load_config()` pattern | `packages/shared/src/shared/config.py` | Template for the new `AIConfig`/`ModelConfig`/`AgentModelsConfig` shape and `load_agent_config()`'s loading style — no new pattern introduced |
| `AgentConfig`/`load_agent_config()` | `packages/reimbursement/src/reimbursement/config.py` | Extended in place: `ollama_*` fields replaced by `ai`/`models`, `consumer_group_id`/`consume_timeout_seconds` untouched |
| `init_chat_model()` | `langchain.chat_models` (already imported in `agent.py`) | Same factory call already used for `"ollama:<model>"`; reused with `"groq:<model>"` and explicit `api_key`/`temperature`/`timeout` kwargs |
| `get_analysis_prompt()` / `get_extract_fields_prompt()` | `reimbursement/agent/prompts/{analysis,extract_fields}.py` | Already extracted (pre-existing, unrelated in-flight change) — `Analysis`/`ExtractFields.__call__` keep calling these unchanged |
| `_clear_config_cache` autouse fixture | `packages/reimbursement/tests/conftest.py` | Extended with a second autouse fixture supplying default Groq env vars, not replaced |
| `FakeStructuredModel` | `packages/reimbursement/tests/agent_fakes.py` | Reused unchanged as the test double for both nodes' models — only its docstring's "Ollama" wording is stale |

### Integration Points

| System | Integration Method |
| --- | --- |
| Groq API | New outbound HTTPS calls from `build_graph()`'s two chat models via `langchain-groq`, replacing today's Ollama HTTP calls at the same two call sites |
| LangFuse | Unchanged — `_langfuse_handlers()`/`config={"callbacks": ...}` wiring in `agent.py` traces every LLM invocation regardless of provider (AGD-23/24); no code in this feature touches that path |

---

## Components

### `reimbursement.config` (extended)

- **Purpose**: Central place for the agent's own Groq credential/timeout and each LLM node's `model_name`/`temperature`.
- **Location**: `packages/reimbursement/src/reimbursement/config.py`
- **Interfaces**:
  - `ModelConfig(model_name: str, temperature: float = 0.0)` — frozen dataclass, one per LLM node
  - `AIConfig(api_key: str, timeout_seconds: float = 30.0)` — frozen dataclass, shared by every node's model
  - `AgentModelsConfig(extract_fields: ModelConfig, analysis: ModelConfig)` — frozen dataclass grouping the two nodes' configs
  - `AgentConfig(consumer_group_id: str, ai: AIConfig, models: AgentModelsConfig, consume_timeout_seconds: float = 1.0)` — `ollama_model`/`ollama_base_url`/`ollama_timeout_seconds` fields removed
  - `load_agent_config() -> AgentConfig` — `@lru_cache(maxsize=1)`, unchanged signature; now reads `GROQ_API_KEY`, `AI_TIMEOUT_SECONDS`, `EXTRACT_FIELDS_MODEL_NAME`, `EXTRACT_FIELDS_TEMPERATURE`, `ANALYSIS_MODEL_NAME`, `ANALYSIS_TEMPERATURE`
- **Dependencies**: `os`, stdlib only (unchanged)
- **Reuses**: `shared.config`'s dataclass/`lru_cache` style; `KafkaConfig` stays untouched, only imported by `to_consumer_config()` as today

### `reimbursement.agent.agent.build_graph()` (modified)

- **Purpose**: Construct the two Groq-backed chat models (one per node) and wire the graph, unchanged otherwise.
- **Location**: `packages/reimbursement/src/reimbursement/agent/agent.py`
- **Interfaces**: `build_graph() -> CompiledStateGraph` (signature unchanged)
- **Dependencies**: `langchain.chat_models.init_chat_model`, `reimbursement.config.load_agent_config`
- **Reuses**: `_wire()`, `get_graph()`, `_langfuse_handlers()`, `decide()` — none of these change

### `reimbursement.agent.nodes.analysis.Analysis` (modified)

- **Purpose**: Unchanged — LLM-as-judge guardrail (AGD-17..20).
- **Location**: `packages/reimbursement/src/reimbursement/agent/nodes/analysis.py`
- **Interfaces**: `__init__(model: Runnable, model_name: str)` — drops the on-disk `self._prompt = prompt` line, which references an undefined `prompt` local (dead from the already-in-flight prompt-extraction change) and would `NameError` on construction; also drops the now-unused `ChatPromptTemplate`/`json` imports
- **Dependencies**: unchanged
- **Reuses**: `get_analysis_prompt()` — unchanged call site

### `reimbursement.agent.nodes.extract_fields.ExtractFields` (modified)

- **Purpose**: Unchanged — value/currency/receipts_date extraction (AGD-01..04).
- **Location**: `packages/reimbursement/src/reimbursement/agent/nodes/extract_fields.py`
- **Interfaces**: `__init__(model: Runnable, model_name: str)` — gains `model_name` (AMC-15's attribution logging); same dead-line/unused-import cleanup as `Analysis`
- **Dependencies**: unchanged
- **Reuses**: `get_extract_fields_prompt()` — unchanged call site

### `GuardrailVerdict` (modified)

- **Purpose**: `analysis`'s structured-output contract.
- **Location**: `packages/reimbursement/src/reimbursement/agent/nodes/analysis.py`
- **Interfaces**: `consistent: bool` (unchanged), `reason: str` (renamed from `reasoning`)
- **Dependencies**: `pydantic.BaseModel`
- **Reuses**: n/a — same class, one field renamed

---

## Data Models

```python
@dataclass(frozen=True)
class ModelConfig:
    model_name: str
    temperature: float = 0.0

@dataclass(frozen=True)
class AIConfig:
    api_key: str
    timeout_seconds: float = 30.0

@dataclass(frozen=True)
class AgentModelsConfig:
    extract_fields: ModelConfig
    analysis: ModelConfig

@dataclass(frozen=True)
class AgentConfig:
    consumer_group_id: str
    ai: AIConfig
    models: AgentModelsConfig
    consume_timeout_seconds: float = 1.0
```

**Relationships**: `AgentConfig` composes one `AIConfig` (shared) and one
`AgentModelsConfig` (which itself composes two independent `ModelConfig`s,
one per LLM node). `KafkaConfig` (from `shared.config`) is unrelated and
untouched — still passed separately into `to_consumer_config()`.

---

## Error Handling Strategy

| Error Scenario | Handling | User Impact |
| --- | --- | --- |
| `GROQ_API_KEY` unset at config-load time | `_require_env("GROQ_API_KEY")` raises `ValueError(f"{name} environment variable is required")` inside `load_agent_config()`, before `build_graph()` constructs any model | Process fails to start; the error names the exact missing variable |
| `EXTRACT_FIELDS_MODEL_NAME` / `ANALYSIS_MODEL_NAME` unset | Same `_require_env` helper, one call per var | Same — fails fast, names the variable |
| `EXTRACT_FIELDS_TEMPERATURE` / `ANALYSIS_TEMPERATURE` / `AI_TIMEOUT_SECONDS` unset | `os.getenv(name, "<default>")` then `float(...)` — no error, falls back to the documented default | None — silent default, as today |
| Groq unreachable/times out during a real call | Unchanged — same exception propagation as today's Ollama failure path; no new catch introduced (Out of Scope, R-011 covers the decision-stage error-handling mechanism separately) | Same as today |

---

## Risks & Concerns

| Concern | Location (file:line) | Impact | Mitigation |
| --- | --- | --- | --- |
| PII/data-residency shift: prompt payloads (`claimed_category`, `raw_ocr_text` — the latter can carry incidental PII embedded in receipt text even though AGD-26 already excludes `submitted_by`) now leave the local Docker network for a third-party cloud API instead of a host-machine Ollama instance | `agent.py` build_graph() call sites | Real compliance-posture change, not just a swap of hostnames — worth an explicit decision, not an implicit side effect of "remove Ollama, add Groq" | Flagging to user directly (see chat reply) rather than silently proceeding; no code mitigation added since this is a business/compliance call, not an engineering one |
| `self._prompt = prompt` in both `Analysis.__init__`/`ExtractFields.__init__` references an undefined `prompt` local on disk right now | `agent/nodes/analysis.py:29`, `agent/nodes/extract_fields.py:39` | `NameError` on the very next construction of either class, i.e. currently broken | Removed as part of this feature's AMC-01/AMC-02/AMC-15 tasks, since both constructors are already being rewritten for the new `model_name`/config wiring |
| Unused `json`/`ChatPromptTemplate` imports left in both node files after prompt-building moved to `get_*_prompt()` helpers | same two files, import block | Dead code, no functional impact | Removed in the same tasks (Minimal Impact) |
| `init_chat_model("groq:<model>", ...)` provider-prefix support is inferred by analogy to the existing `"ollama:<model>"` pattern and LangChain's documented `{provider}:{model_id}` convention — not independently confirmed for `"groq"` specifically via available docs | `agent.py` build_graph() | If the shorthand doesn't resolve, `init_chat_model` raises at construction time (caught immediately by tests, not a silent failure) | First implementation task constructs both models with no monkeypatching (mirrors the existing `it_wires_the_real_...` test) before anything else is built on top; falls back to direct `ChatGroq(...)` instantiation if the shorthand fails |
| Six new required env vars break every existing test that transitively calls `load_agent_config()` (e.g. `consumer_group_id` tests) unless supplied globally | `packages/reimbursement/tests/conftest.py` | Broad test-suite breakage if missed | New autouse fixture sets sane defaults for all six vars; individual fail-fast tests override via `monkeypatch.delenv(...)` |

---

## Tech Decisions (only non-obvious ones)

| Decision | Choice | Rationale |
| --- | --- | --- |
| Config nesting shape | `AgentConfig.models: AgentModelsConfig` (holding `extract_fields`/`analysis`) rather than four flat fields directly on `AgentConfig` | Mirrors the "per-node nested config" shape confirmed with the user; keeps `AgentConfig` from flattening further if a third LLM node is ever added |
| `timeout_seconds` placement | Lives on `AIConfig` (shared), not per-node `ModelConfig` | Not a model-quality knob like `model_name`/`temperature` — it's the same operational HTTP-call bound app-wide; avoids duplicating one value across two configs |
| `api_key` passed explicitly to `init_chat_model(..., api_key=...)` | Explicit pass-through from `AgentConfig.ai.api_key`, not relying on `ChatGroq`'s own implicit `GROQ_API_KEY` env read | Keeps one single source of truth (AMC-11/12) that's directly assertable in tests (construction-call args), rather than a second implicit env read the SDK does on its own |
| Fail-fast helper | Small `_require_env(name) -> str` raising `ValueError(f"{name} environment variable is required")`, not bare `os.environ[name]` | Bare `KeyError` doesn't produce the actionable, variable-naming message AMC-06/07/08 require |
| `model_name` has no code-level default; `.env.sample` still carries `llama-3.3-70b-versatile` as a placeholder | Two different layers: code enforces "no silent default," the *sample file* supplies a real, working value so local dev isn't broken out of the box | Resolves the apparent tension between "fail fast, no default" and "fresh clone must work" — confirmed with the user both are wanted together |

> **Project-level decision pending:** the `AIConfig`/`ModelConfig`/`AgentModelsConfig` nesting shape is a reusable convention for any future LLM-node addition. I'll append it to `.specs/STATE.md` as **AD-032** once this design is confirmed, rather than before — so it isn't recorded until it's actually the agreed shape.

