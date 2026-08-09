# Agent Model Config Specification

## Problem Statement

The reimbursement agent's two LLM-backed nodes (`extract_fields`, `analysis`) are
hardwired to Ollama, a self-hosted model server with no configuration-layer parity
to the rest of the codebase's `shared.config`-style frozen-dataclass pattern
(`KafkaConfig`, `DatabaseConfig`). Both nodes today also share a single model
name with no independent tuning and no `temperature` control at all. This blocks
moving to Groq (a hosted inference provider) and leaves each node's model choice
unconfigurable per-node.

## Goals

- [ ] Replace Ollama with Groq as the reimbursement agent's LLM provider — no
      Ollama dependency, code, or config remains.
- [ ] Give `extract_fields` and `analysis` their own independent model
      configuration (`model_name`, `temperature`), each its own chat-model
      instance — not a single model shared across both.
- [ ] Centralize the Groq credential (`api_key`) in one general AI config, read
      once and reused by both nodes' model instances.
- [ ] Preserve existing traceability/observability guarantees (LangFuse tracing,
      `decision_reason` logging, AGD-23/24) unchanged.

## Out of Scope

| Feature                                                | Reason                                                                                          |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `docker-compose.yml` changes                             | Ollama runs on the host machine (`docs/codebase/STACK.md:48`), not containerized — nothing to remove from compose. |
| `docs/SCOPE.md` changes                                  | Contains no Ollama/Groq/model-config references today (verified by grep) — nothing to amend.     |
| `docs/codebase/*.md` resync                              | Owned by the `architecture-evaluate` skill's incremental sync, run after implementation lands — not hand-edited by this spec. |
| Decision-graph routing/policy changes (AGD-17..20)       | Only the `analysis` node's structured-output *field names* change (AMC-09); routing logic is untouched. |
| Retry/fallback behavior for a failing Groq call          | Unchanged from today's Ollama failure path; a dedicated error-handling mechanism for the decision stage is already tracked separately as R-011. |
| Multi-provider abstraction (choosing Groq vs. another provider at runtime) | Not requested — this spec hardwires Groq as the provider, same as today's code hardwires Ollama. |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here — nothing is left silently unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --- | --- | --- | --- |
| `analysis` node's structured-output schema shape | Keep the 2-field shape: boolean verdict field unchanged (`consistent`, still code-derives `status`), `reasoning` renamed to `reason` | User confirmed via question: "Just rename the fields" — no behavior change to the auto-approve/human-review decision path, only the reason field's name | y |
| `model_name` default behavior | Required, no hardcoded default — fails fast at startup if unset | User confirmed: a financial-decision agent should never silently run on an unvetted default model; today's Ollama `llama3.2` fallback is deliberately not carried forward | y |
| `temperature` default behavior | Defaults to `0.0` for both nodes if unset | User confirmed: both structured extraction and a pass/fail guardrail verdict want low-variance, near-deterministic output | y |
| General AI config shape/name | `AIConfig` dataclass with `api_key: str` (required, fails fast if unset) and `timeout_seconds: float = 30.0` (moved from today's `ollama_timeout_seconds`, same default value) | Matches user's "general for AI with api_key" framing; `timeout_seconds` isn't model-name/temperature-specific so it doesn't belong on the per-node `ModelConfig`, and folding it into the one shared section avoids duplicating it per node | n |
| Config placement | Lives in `packages/reimbursement/src/reimbursement/config.py` (`AgentConfig`), not `shared.config` | User confirmed directly: "this config must be placed in the reimbursement layer" — also consistent with today's comment that Ollama/model config is reimbursement-only | y |
| Per-node model instances | `extract_fields` and `analysis` each get their own chat-model instance built from their own `ModelConfig`, not a shared instance | User confirmed directly: "each node will have its own configuration" | y |
| Env var naming | `GROQ_API_KEY`, `AI_TIMEOUT_SECONDS`, `EXTRACT_FIELDS_MODEL_NAME`, `EXTRACT_FIELDS_TEMPERATURE`, `ANALYSIS_MODEL_NAME`, `ANALYSIS_TEMPERATURE` | Follows the existing `<SCOPE>_<FIELD>` convention already used for `AGENT_CONSUMER_GROUP_ID`/`DATABASE_POOL_MAX_SIZE`; `GROQ_API_KEY` matches the credential name `langchain-groq`/Groq's own tooling looks for by convention, avoiding a second name for the same secret | y |
| `.env.sample`/`.env` placeholder values | User confirmed directly: these six vars must be set in `.env`, and `.env.sample` must carry a **settled (non-blank) placeholder** for each — not left empty like the optional `KAFKA_SECURITY_PROTOCOL`-family vars. Model-name placeholders use `llama-3.3-70b-versatile` (verified live on Groq's own model docs via Context7, `console.groq.com/docs/model/llama-3.3-70b-versatile`) for both nodes; `GROQ_API_KEY` gets an obviously-fake placeholder string (matching the style of `.env.sample`'s existing `LANGFUSE_INIT_USER_PASSWORD=changeme123456`-style fakes), not a blank | Fresh-clone `.env.sample` → `.env` must work out of the box for local dev, same as every other var in that file today | y |
| `extract_fields` model-attribution logging | Add `model_name` to `extract_fields`'s completion log line, matching `analysis`'s existing `model=%s` attribution | This code path is already being rewritten to source its model from the new config; the project's traceability NFR (`CLAUDE.md`) requires attributing every action to the specific model version, and `analysis` already does this — leaving `extract_fields` unattributed while touching the exact same construction code would be an inconsistency introduced by this change, not a pre-existing gap left alone | n |
| Test-time construction without live credentials | The existing "no monkeypatching" `build_graph()` wiring test (`test_agent.py::it_wires_the_real_...`) must keep working using a placeholder `GROQ_API_KEY` + placeholder model names supplied by test env setup, not a real Groq account | Constructing a `ChatGroq`/`init_chat_model("groq:...")` instance is not expected to make a network call by itself (only `.ainvoke()` does) — this needs empirical verification during implementation since it isn't independently documented; if construction does validate the key eagerly, the placeholder value still satisfies that check | n |

**Open questions:** none — all resolved or logged above.

---

## User Stories

### P1: Reimbursement agent runs its LLM nodes on Groq, each independently configured ⭐ MVP

**User Story**: As the reimbursement agent operator, I want `extract_fields` and
`analysis` to call Groq instead of Ollama, each with its own `model_name` and
`temperature`, so that I can run the agent without a self-hosted GPU host and
tune each node's model independently.

**Why P1**: This is the core ask — without it, Ollama is still the provider and
nothing else in this spec has a foundation to sit on.

**Acceptance Criteria**:

1. WHEN `build_graph()` constructs the `extract_fields` node's model THEN the
   system SHALL initialize a Groq-backed chat model using that node's own
   `model_name` and `temperature` read from configuration.
2. WHEN `build_graph()` constructs the `analysis` node's model THEN the system
   SHALL initialize a Groq-backed chat model using that node's own `model_name`
   and `temperature`, independent of `extract_fields`'s configured values.
3. WHEN `extract_fields` and `analysis` are configured with two different
   `model_name` values THEN the system SHALL construct two distinct
   model instances (proves independence — not one shared instance whose
   value happens to be read twice).
4. WHEN `packages/reimbursement/pyproject.toml` is inspected THEN the system
   SHALL show `langchain-ollama` removed and `langchain-groq` present as a
   dependency.
5. WHEN the codebase is searched for `ollama`/`Ollama`/`OLLAMA` (source and
   tests) THEN the system SHALL return zero matches.

**Independent Test**: Run `build_graph()` with `EXTRACT_FIELDS_MODEL_NAME` and
`ANALYSIS_MODEL_NAME` set to two different values and assert the two node
instances hold distinct model names; grep the tree for `ollama` and confirm
zero hits.

---

### P1: Configuration fails fast on missing required values

**User Story**: As the reimbursement agent operator, I want the agent to refuse
to start with a clear error when required Groq configuration is missing, so
that a misconfigured deployment never silently falls back to an unvetted
default model or an empty credential.

**Why P1**: Matches the project's "no silent fallbacks" NFR — a decision must
never happen without a durable, well-understood configuration behind it.

**Acceptance Criteria**:

1. WHEN `GROQ_API_KEY` is unset at config-load time THEN the system SHALL raise
   an error identifying `GROQ_API_KEY` as the missing variable, before any
   Groq call is attempted.
2. WHEN `EXTRACT_FIELDS_MODEL_NAME` is unset THEN the system SHALL raise an
   error identifying that specific variable as missing.
3. WHEN `ANALYSIS_MODEL_NAME` is unset THEN the system SHALL raise an error
   identifying that specific variable as missing.
4. WHEN `EXTRACT_FIELDS_TEMPERATURE` or `ANALYSIS_TEMPERATURE` is unset THEN
   the system SHALL default that node's `temperature` to `0.0` rather than
   raising.
5. WHEN `AI_TIMEOUT_SECONDS` is unset THEN the system SHALL default to `30.0`
   (today's `OLLAMA_TIMEOUT_SECONDS` default, carried forward unchanged).
6. WHEN `.env.sample` is inspected THEN the system SHALL show all six vars
   (`GROQ_API_KEY`, `AI_TIMEOUT_SECONDS`, `EXTRACT_FIELDS_MODEL_NAME`,
   `EXTRACT_FIELDS_TEMPERATURE`, `ANALYSIS_MODEL_NAME`,
   `ANALYSIS_TEMPERATURE`) present with non-blank placeholder values — a
   fresh `cp .env.sample .env` SHALL be enough to start the agent without
   editing model-name/temperature values (a real `GROQ_API_KEY` is still
   required, same as every other real secret in that file).
7. WHEN `.env` (the local, gitignored dev file) is inspected THEN the system
   SHALL show the same six vars set, mirroring `.env.sample`.

**Independent Test**: Unset each required variable in turn and assert
`load_agent_config()` raises with that variable's name in the error message;
unset each optional variable and assert the documented default is used; diff
`.env.sample` against `.env` and confirm both carry all six keys.

---

### P2: Groq credential is centralized, not duplicated per node

**User Story**: As the reimbursement agent operator, I want one place that
holds the Groq `api_key`, so that both nodes' models always use the same
credential and rotating it requires changing one value, not two.

**Why P2**: Correctness-adjacent (a drifted second credential would be a
subtle bug) but not blocking — P1 already gets both nodes working end to end.

**Acceptance Criteria**:

1. WHEN both nodes' models are constructed THEN the system SHALL source the
   Groq `api_key` from a single shared `AIConfig.api_key` field.
2. WHEN `GROQ_API_KEY` changes and the process reloads its config THEN the
   system SHALL apply the new value to both nodes' models from that same
   single read.

**Independent Test**: Assert `AgentConfig.ai.api_key` is the one value both
the `extract_fields` and `analysis` model-construction calls reference (by
inspecting the constructed models' credential or by construction-call
arguments in a test double).

---

### P2: `extract_fields` gets model-attribution logging parity with `analysis`

**User Story**: As someone auditing a reimbursement decision, I want to know
which model performed the `extract_fields` step, the same way I can already
see which model produced the `analysis` verdict, so that every LLM-influenced
step in the decision is attributable.

**Why P2**: A direct consequence of the project's traceability NFR applied to
code this spec is already rewriting — not a new capability, closing an
inconsistency this change would otherwise introduce.

**Acceptance Criteria**:

1. WHEN the `extract_fields` node completes THEN the system SHALL log the
   `model_name` that performed the extraction, in the same FLOW-log style
   `analysis` already uses.

**Independent Test**: Run `extract_fields` and assert its completion log line
includes the configured `model_name`.

---

### P3: `analysis`'s structured-output field renamed for clarity

**User Story**: As a developer reading the `analysis` node's LLM contract, I
want the reasoning field named `reason` (matching `decision_reason`
terminology used everywhere else), so the schema reads consistently with the
rest of the codebase.

**Why P3**: Cosmetic — no behavior change, confirmed with the user as a pure
rename.

**Acceptance Criteria**:

1. WHEN `GuardrailVerdict` is inspected THEN the system SHALL expose exactly
   two fields: the existing boolean verdict field (name/semantics unchanged)
   and `reason: str` (renamed from `reasoning`).
2. WHEN the `analysis` node runs THEN the system SHALL derive `status`
   (`"auto-approved"`/`"human-review"`) from the boolean field exactly as
   today — no change to that derivation.

**Independent Test**: Assert `GuardrailVerdict.model_fields` contains
`reason`, not `reasoning`; assert the existing status-derivation tests
(`consistent=True` → `auto-approved`, etc.) still pass unmodified in
behavior.

---

## Edge Cases

- WHEN Groq is unreachable or times out during a call THEN the system SHALL
  surface the same failure path already in place today (unchanged
  propagation — no new retry/fallback introduced by this spec).
- WHEN the test suite constructs the real graph with no monkeypatching
  (`test_agent.py::it_wires_the_real_..._bound_models_into_the_expected_node_set`)
  THEN the system SHALL succeed without a live network call or a real Groq
  account, using placeholder `GROQ_API_KEY`/`EXTRACT_FIELDS_MODEL_NAME`/
  `ANALYSIS_MODEL_NAME` values supplied by test setup (e.g. an autouse
  fixture), consistent with how `_clear_config_cache` already resets
  `load_agent_config`'s cache between tests.
- WHEN any other existing test (e.g. `consumer_group_id` tests) calls
  `load_agent_config()` and doesn't care about Groq settings THEN the system
  SHALL still succeed, because a shared test fixture supplies the now-required
  Groq env vars globally rather than requiring every test to set them
  individually.

---

## Requirement Traceability

| Requirement ID | Story                                             | Phase  | Status  |
| --------------- | -------------------------------------------------- | ------ | ------- |
| AMC-01          | P1: Groq-backed LLM nodes                          | Design | Pending |
| AMC-02          | P1: Groq-backed LLM nodes                          | Design | Pending |
| AMC-03          | P1: Groq-backed LLM nodes                          | Design | Pending |
| AMC-04          | P1: Groq-backed LLM nodes                          | Design | Pending |
| AMC-05          | P1: Groq-backed LLM nodes                          | Design | Pending |
| AMC-06          | P1: Fail-fast configuration                        | Design | Pending |
| AMC-07          | P1: Fail-fast configuration                        | Design | Pending |
| AMC-08          | P1: Fail-fast configuration                        | Design | Pending |
| AMC-09          | P1: Fail-fast configuration                        | Design | Pending |
| AMC-10          | P1: Fail-fast configuration                        | Design | Pending |
| AMC-11          | P1: Fail-fast configuration (`.env.sample`)        | Design | Pending |
| AMC-12          | P1: Fail-fast configuration (`.env`)               | Design | Pending |
| AMC-13          | P2: Centralized Groq credential                    | Design | Pending |
| AMC-14          | P2: Centralized Groq credential                    | Design | Pending |
| AMC-15          | P2: `extract_fields` attribution logging           | Design | Pending |
| AMC-16          | P3: `GuardrailVerdict` field rename                | Design | Pending |
| AMC-17          | P3: `GuardrailVerdict` field rename                | Design | Pending |

**ID format:** `AMC-[NUMBER]` (Agent Model Config)

**Status values:** Pending → In Design → In Tasks → Implementing → Verified

**Coverage:** 17 total, 0 mapped to tasks, 17 unmapped ⚠️ (Tasks not yet run)

---

## Success Criteria

- [ ] Zero `ollama`/`Ollama`/`OLLAMA` matches remain anywhere in
      `packages/reimbursement` (source and tests).
- [ ] `extract_fields` and `analysis` can be configured with two different
      `model_name`/`temperature` pairs and demonstrably use them independently.
- [ ] Starting the agent with any of `GROQ_API_KEY`, `EXTRACT_FIELDS_MODEL_NAME`,
      `ANALYSIS_MODEL_NAME` unset fails immediately with a message naming the
      missing variable, before any Groq call.
- [ ] Full `reimbursement` package test suite passes with no live network
      dependency on Groq.
- [ ] `cp .env.sample .env` on a fresh clone starts the agent without editing
      `EXTRACT_FIELDS_MODEL_NAME`/`ANALYSIS_MODEL_NAME`/temperature/timeout
      values — only the `GROQ_API_KEY` placeholder needs a real key.
