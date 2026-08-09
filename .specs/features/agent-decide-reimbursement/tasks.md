# Agent — Decide `Reimbursement` Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user — do not proceed without it.**

---

**Design**: `.specs/features/agent-decide-reimbursement/design.md`
**Status**: Draft

---

## Scope note — R-011's full policy is still open; an interim floor is in scope

`.specs/RISKS.md`'s R-011 (the real decision-stage retry/circuit-breaker policy) is **not resolved**. `design.md` now specifies an interim floor so this task list isn't blocked: an `agent.decide()` failure is caught, durably logged (`failure_log`), and returns `MessageOutcome.LOGGED` — no retry, no auto-escalation. That floor **is** in scope (T15 below). Out of scope, explicitly: any retry/republish logic, and widening `AttemptError`'s `Stage` Literal with `"decide"` (nothing consumes it yet — adding it now would be dead code ahead of R-011's real mechanism).

---

## Test Coverage Matrix

> Generated from codebase sampling (`packages/reimbursement/tests/test_validation.py`, `packages/shared/tests/reimbursement/test_repository.py`, `packages/shared/tests/reimbursement/use_cases/test_send_human_review.py`, `packages/shared/tests/test_models.py`, `packages/reimbursement/tests/agent_fakes.py`) and this repo's root `CLAUDE.md` (traceability is a hard, non-functional requirement — every node test that decides also asserts its `FLOW:` log line and, where applicable, its `decision_reason` content). Guidelines found: root `CLAUDE.md` (traceability), `.specs/STATE.md` (AD-023 singleton pattern, AD-025 use-case placement).

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| --- | --- | --- | --- | --- |
| `shared.models.Reimbursement` (new model) | unit | `from_record` success + JSON-decode of `original_payload`, matching `DescribeAttemptError*`'s style | `packages/shared/tests/test_models.py` | `uv run pytest packages/shared -m "not integration"` |
| Repository (`update_decision`) | unit | Success + 0-rows-affected path, replacing `update_human_review`'s existing coverage | `packages/shared/tests/reimbursement/test_repository.py` | same |
| Use case (`apply_decision`, `escalate_existing`) | unit | Ghost (`None`) + success path for `apply_decision`; `escalate_existing`'s existing assertions pass unchanged post-refactor | `packages/shared/tests/reimbursement/use_cases/*.py` | same |
| `reimbursement` config (Ollama settings) | unit | env-var read + default, same shape as existing `AgentConfig` tests | `packages/reimbursement/tests/test_config.py` | `uv run pytest packages/reimbursement -m "not integration"` |
| Graph nodes (`ExtractFields`, `Validate`, `ApplyPolicies`, `Analysis`, `ApplyAgentDecision`) | unit | All branches; 1:1 to AGD-01..20; every listed boundary (exactly 90 days, exactly 200, exactly 2000); `FLOW:` log line asserted per node | `packages/reimbursement/tests/test_agent_nodes.py` (or one file per node) | same |
| Graph wiring / singleton (`agent.py`) | unit | `get_graph() is get_graph()`, `build_graph()` invoked once (L-003 proxy); one routing test per path: reject, ≤200, >2000, ambiguous→auto-approved, ambiguous→human-review, missing-fields | `packages/reimbursement/tests/test_agent.py` | same |
| `validation.py` extension | unit | `agent.decide()` call site wired in, `persisted`/`status` logged, `agent.decide()` failure caught and logged via `failure_log` (R-011 interim floor) — never propagates | `packages/reimbursement/tests/test_validation.py` | same |
| Consumer end-to-end | integration | One full happy-path proof against real Postgres, with a **fake** model injected into `ExtractFields`/`Analysis` (no real Ollama call — prompts are still placeholders) | `packages/reimbursement/tests/test_integration.py` | `uv run pytest` (full, Docker required) |
| Prompts (placeholders), `schema.py` (`TypedDict`/`Protocol`) | none | build gate only | `packages/reimbursement/src/reimbursement/agent/prompts/*.py`, `packages/reimbursement/src/reimbursement/schema.py` | build gate |

**Baseline: 437 passed, 10 deselected** on `-m "not integration"`, before this task list starts. Every task below states its expected new total so a silent deletion fails the gate.

## Gate Check Commands

> Generated from codebase — confirm before Execute. The root `pyproject.toml`'s `testpaths` spans `api`, `publisher`, `reimbursement`, `shared` in one run — no per-package scoping needed.

| Gate Level | When to Use | Command |
| --- | --- | --- |
| Quick | After tasks with unit tests only | `uv run pytest -q -m "not integration"` |
| Full | After tasks with Postgres-container integration tests | `uv run pytest -q` (Docker must be running) |

---

## Execution Plan

Phases are ordered and run sequentially — each phase completes before the next begins, and tasks within a phase execute in order.

### Phase 1: Shared kernel — new model, then consolidate the update path

```
T1 → T2 → T3 → T4
```

### Phase 2: `reimbursement`-local config and dependency

```
T5 → T6
```

### Phase 3: Graph state

```
T7
```

### Phase 4: The five nodes

```
T8 → T9 → T10 → T11 → T12
```

### Phase 5: Graph wiring and the singleton entry point

```
T13
```

### Phase 6: Consumer integration and end-to-end proof

```
T14 → T15
```

---

## Task Breakdown

### T1: Add `Reimbursement` model to `shared.models`

**What**: New `class Reimbursement(BaseModel)` in `shared/models.py`: `uuid: UUID`, `original_payload: dict[str, Any]`, plus `@classmethod from_record(cls, record: asyncpg.Record) -> Self` that decodes `original_payload` via `json.loads` (no asyncpg JSON codec is configured anywhere in this codebase, so it always arrives as raw text). This is the graph's `State["reimbursement"]` input — `schema.py`'s `from shared.models import Reimbursement` is currently a broken import; this task fixes that.
**Where**: `packages/shared/src/shared/models.py`
**Depends on**: None
**Reuses**: `AttemptError.from_exception`'s classmethod-constructor shape, already in this same file.
**Requirement**: Supports AGD-01 (the extraction node's input).

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `Reimbursement` exists with `uuid`, `original_payload`, and `from_record`.
- [x] `packages/shared/tests/test_models.py` gains `DescribeReimbursement`: one case builds from a fake `asyncpg.Record`-like mapping with a JSON-text `original_payload` and asserts the decoded dict; one case asserts `uuid` round-trips as a `UUID`.
- [x] Gate check passes: `uv run pytest packages/shared -q -m "not integration"`.
- [x] Test count: +2 from this task (437 → 439).

**Tests**: unit
**Gate**: quick

---

### T2: Consolidate `update_human_review` into a generic `update_decision`

**What**: In `packages/shared/src/shared/reimbursement/repository.py`, replace `_UPDATE_HUMAN_REVIEW`/`update_human_review(conn, uuid, reason)` with `_UPDATE_DECISION`/`update_decision(conn, uuid, status, decision_reason) -> bool` (same `UPDATE ... WHERE uuid = $1` shape, `status` now a bound parameter instead of a literal). Remove `update_human_review` entirely — do not keep it alongside.
**Where**: `packages/shared/src/shared/reimbursement/repository.py`
**Depends on**: None
**Reuses**: `update_human_review`'s own SQL shape and `"UPDATE 1"`-string-parsing return convention.
**Requirement**: Supports AGD-21..23 (persistence side of traceability).

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `update_human_review` no longer exists anywhere in `repository.py`.
- [x] `update_decision(conn, uuid, status, decision_reason) -> bool` exists, returns `True` iff exactly one row was affected.
- [x] `packages/shared/tests/reimbursement/test_repository.py`'s `DescribeUpdateHumanReview` is renamed `DescribeUpdateDecision` and its two existing cases (success, ghost) are adapted to call `update_decision` with an explicit status (e.g. `"human-review"`), asserting the row's `status` column too, not just `decision_reason`.
- [x] Gate check passes: `uv run pytest packages/shared -q -m "not integration"`.
- [x] Test count: no net loss from `packages/shared/tests/reimbursement/test_repository.py`'s current count (same 2 cases, renamed/adapted, not deleted) — running total stays 439.

**Tests**: unit
**Gate**: quick

---

### T3: Add the `apply_decision` use case

**What**: New file `packages/shared/src/shared/reimbursement/use_cases/apply_decision.py` with `async def apply_decision(conn: asyncpg.Connection, uuid: UUID, status: str, decision_reason: str) -> UUID | None`, calling `repository.update_decision` and returning `None` on 0-rows-affected (mirrors `escalate_existing`'s exact contract).
**Where**: `packages/shared/src/shared/reimbursement/use_cases/apply_decision.py`
**Depends on**: T2
**Reuses**: `escalate_existing`'s `UUID | None` ghost-signaling convention.
**Requirement**: Supports AGD-21..23.

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `apply_decision` exists with the signature above.
- [x] New `packages/shared/tests/reimbursement/use_cases/test_apply_decision.py`: one case asserts a real row's `status`/`decision_reason` update and a returned `UUID`; one case asserts `None` on a ghost `uuid`.
- [x] Gate check passes: `uv run pytest packages/shared -q -m "not integration"`.
- [x] Test count: +2 from this task (439 → 441).

**Tests**: unit
**Gate**: quick

---

### T4: Refactor `escalate_existing` to delegate to `apply_decision`; verify no regression

**What**: `escalate_existing(conn, uuid, errors, max_message_chars) -> UUID | None` in `send_human_review.py` keeps its exact signature but its body becomes `return await apply_decision(conn, uuid, "human-review", render_history(errors, max_message_chars))`, replacing its direct call to (the now-removed) `update_human_review`. Also update `packages/reimbursement/tests/agent_fakes.py`'s `FakeConnection.execute`/`FakePool.update` to accept the generic `(uuid, status, reason)` shape `update_decision` now sends, instead of the old hardcoded-to-`"human-review"` two-arg form.
**Where**: `packages/shared/src/shared/reimbursement/use_cases/send_human_review.py`, `packages/reimbursement/tests/agent_fakes.py`
**Depends on**: T3
**Reuses**: `apply_decision` (T3).
**Requirement**: None new — regression safety for `agent-consume-reimbursement` (already shipped, PASS on `feature/6_reimbursement_consumer`).

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `escalate_existing`'s body delegates to `apply_decision`; no direct repository import remains in `send_human_review.py` for the update path.
- [x] `agent_fakes.py`'s fake pool/connection accept a `status` argument on its update path; `test_validation.py`'s existing assertions on `pool.updated[uuid]` still pass, adapted if the fake's stored shape changed.
- [x] `packages/shared/tests/reimbursement/use_cases/test_send_human_review.py` — every existing case passes **unchanged in assertion content** (ghost → `None`, success → uuid, `decision_reason` text) — run, not assumed.
- [x] `packages/reimbursement/tests/test_validation.py`'s `DescribeRetryCeilingEscalation` cases pass unchanged.
- [x] Gate check passes: `uv run pytest -q -m "not integration"` (full workspace, not just `shared`/`reimbursement` — this is the explicit regression check design.md calls for).
- [x] Test count: 441 passed, 10 deselected, no unexplained deltas.

**Tests**: unit
**Gate**: quick

---

### T5: Add Ollama settings to `reimbursement`'s own config (not `shared.config`)

**What**: Extend `packages/reimbursement/src/reimbursement/config.py`'s `AgentConfig` (or add a sibling frozen dataclass in the same file) with `ollama_model: str` and `ollama_base_url: str`, read via `os.getenv` inside the existing `load_agent_config()` `@lru_cache`d loader — same pattern as `consumer_group_id`. **Not** placed in `shared.config.Config`: single-service tuning belongs in that service's own package (mirrors `PublisherConfig`'s precedent) — Ollama is `reimbursement`-only today.
**Where**: `packages/reimbursement/src/reimbursement/config.py`
**Depends on**: None
**Reuses**: `AgentConfig`'s existing `@dataclass(frozen=True)` + `@lru_cache(maxsize=1)` loader shape.
**Requirement**: Supports AGD-01, AGD-17 (enables the LLM steps).

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `AgentConfig` (or a new sibling dataclass) carries `ollama_model`/`ollama_base_url`, both env-var-driven with sane placeholder defaults (e.g. `OLLAMA_MODEL` default a small, named placeholder model; `OLLAMA_BASE_URL` default `http://localhost:11434`).
- [x] `packages/reimbursement/tests/test_config.py` gains a case asserting the env-var read and its default, matching the file's existing style.
- [x] Gate check passes: `uv run pytest packages/reimbursement -q -m "not integration"`.
- [x] Test count: +1 from this task.

**Tests**: unit
**Gate**: quick

---

### T6: Add `langchain-ollama` dependency

**What**: Add `langchain-ollama` to `packages/reimbursement/pyproject.toml`'s `dependencies`, regenerate `uv.lock`.
**Where**: `packages/reimbursement/pyproject.toml`, `uv.lock`
**Depends on**: None
**Reuses**: N/A.
**Requirement**: Supports AGD-01, AGD-17.

**Tools**:
- MCP: `context7` (confirm the current `langchain-ollama` version/extras before pinning — do not guess a version string)
- Skill: NONE

**Done when**:
- [x] `langchain-ollama` present in `packages/reimbursement/pyproject.toml`.
- [x] `uv lock` runs clean, `uv.lock` updated.
- [x] Gate check passes: `uv run pytest -q -m "not integration"` (confirms nothing else broke from the lock update).
- [x] Test count: unchanged.

**Tests**: none (dependency/build-gate only)
**Gate**: build

---

### T7: Extend `schema.py`'s graph state; add the `Node` Protocol

**What**: Per `design.md` Component — add `missing_fields`, `requires_llm_judgment`, `status`, `decision_reason`, `persisted`, `guardrail_verdict` to `State`; add `ExtractedFields`; add the `Node` Protocol (`async def __call__(self, state: State, config: RunnableConfig) -> dict[str, Any]`).
**Where**: `packages/reimbursement/src/reimbursement/schema.py`
**Depends on**: T1 (its `Reimbursement` import must resolve)
**Reuses**: `Reimbursement` (T1).
**Requirement**: Supports all of AGD-01..26 (shared data contract).

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `State` and `ExtractedFields` match `design.md`'s Component 1 exactly.
- [x] `Node` Protocol defined and importable.
- [x] No behavior to test at this layer (TypedDict/Protocol) — build gate only, per the coverage matrix.
- [x] Gate check passes: `uv run pytest -q -m "not integration"` (nothing should break from a pure type-shape change).

**Tests**: none
**Gate**: build

---

### T8: `ExtractFields` node

**What**: `class ExtractFields` in `agent/nodes/extract_fields.py` — constructor takes `model` (Ollama chat model bound with `.with_structured_output(ExtractedFieldsSchema)`) and `prompt`; `__call__` resolves `value`/`currency`/`receipts_date` (AGD-01..04), optionally pre-seeded from `claimed_amount_brl` when present. Also creates `agent/prompts/extract_fields.py` as a placeholder prompt.
**Where**: `packages/reimbursement/src/reimbursement/agent/nodes/extract_fields.py`, `packages/reimbursement/src/reimbursement/agent/prompts/extract_fields.py`
**Depends on**: T5, T6, T7
**Reuses**: `Node` Protocol shape (T7).
**Requirement**: AGD-01, AGD-02, AGD-03, AGD-04.

**Tools**:
- MCP: `context7` (confirm `.with_structured_output` usage against the pinned `langchain`/`langchain-ollama` versions)
- Skill: NONE

**Done when**:
- [x] `ExtractFields(model=fake_model, prompt=PLACEHOLDER_PROMPT).__call__(state, config)` resolves all three fields from a `sample.json`-shaped payload, using a fake/stub model (no real Ollama call in unit tests).
- [x] `logger.info("FLOW: Executing 'extract_fields' node")` present and asserted via `caplog`.
- [x] Never returns a `status` key (Risks & Concerns discipline check).
- [x] Gate check passes: `uv run pytest packages/reimbursement -q -m "not integration"`.
- [x] Test count: +N (new test file/class) — record actual count in the commit.

**Tests**: unit
**Gate**: quick

---

### T9: `Validate` node

**What**: `class Validate` in `agent/nodes/validate.py` — no constructor dependencies (kept as a class for uniform shape per your review). `__call__` returns `missing_fields`; when non-empty, also authors `status="human-review"`/`decision_reason` (AGD-09/10). Add `route_after_validate`.
**Where**: `packages/reimbursement/src/reimbursement/agent/nodes/validate.py`
**Depends on**: T7
**Reuses**: `Node` Protocol shape.
**Requirement**: AGD-09, AGD-10, AGD-11.

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] Missing-value and missing-date cases each produce `status="human-review"` naming the specific missing field in `decision_reason`.
- [x] Both-fields-present case returns `missing_fields=[]` and no `status`.
- [x] `route_after_validate` returns `"apply_policies"` when `missing_fields` is empty, `"apply_agent_decision"` otherwise.
- [x] `FLOW:` log line asserted.
- [x] Gate check passes: `uv run pytest packages/reimbursement -q -m "not integration"`.
- [x] Test count: +N.

**Tests**: unit
**Gate**: quick

---

### T10: `ApplyPolicies` node

**What**: `class ApplyPolicies` in `agent/nodes/apply_policies.py` — constructor takes `apply_decision` (the use-case callable from T3, injected). `__call__` checks reject (>90 days, first) → `≤200` → `>2000`, in that order; when one fires, authors `status`/`decision_reason` and calls `self._apply_decision(config["configurable"]["conn"], uuid, status, decision_reason)`, returns `persisted`. When none fire (ambiguous zone), returns `requires_llm_judgment=True` only, no use-case call.
**Where**: `packages/reimbursement/src/reimbursement/agent/nodes/apply_policies.py`
**Depends on**: T7, T3
**Reuses**: `apply_decision` (T3), `Node` Protocol.
**Requirement**: AGD-05, AGD-06, AGD-07, AGD-08, AGD-12, AGD-13, AGD-14, AGD-15, AGD-16.

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] Reject fires at 91 days, does not fire at exactly 90 (AGD-05, AGD-08 boundary).
- [x] Reject wins over the `>2000` rule when both would otherwise apply (AGD-07 ordering, per AD-030).
- [x] `≤200` (inclusive at exactly 200) auto-approves with no use-case-skipping regression — `apply_decision` is called with `"auto-approved"`.
- [x] `>2000` (exclusive at exactly 2000 — stays in the ambiguous zone) routes to `human-review` via `apply_decision`.
- [x] Ambiguous zone (`200 < value ≤ 2000`, reject clear) returns `requires_llm_judgment=True`, calls `apply_decision` zero times — asserted via a call-count on the injected fake.
- [x] Injected `apply_decision` fake proves constructor-injection works (no monkeypatching a module import).
- [x] `FLOW:` log line + which rule fired, asserted.
- [x] Gate check passes: `uv run pytest packages/reimbursement -q -m "not integration"`.
- [x] Test count: +N.

**Tests**: unit
**Gate**: quick

---

### T11: `Analysis` node

**What**: `class Analysis` in `agent/nodes/analysis.py` — constructor takes `model` (Ollama chat model with the guardrail's own structured-output schema) and `prompt`. `__call__` computes `guardrail_verdict` and authors `status`/`decision_reason` from it (`"auto-approved"` when consistent, `"human-review"` with the guardrail's stated reasoning otherwise) — never calls `apply_decision` itself. Also creates `agent/prompts/analysis.py` as a placeholder.
**Where**: `packages/reimbursement/src/reimbursement/agent/nodes/analysis.py`, `packages/reimbursement/src/reimbursement/agent/prompts/analysis.py`
**Depends on**: T5, T6, T7
**Reuses**: `Node` Protocol shape.
**Requirement**: AGD-17, AGD-18, AGD-19, AGD-20.

**Tools**:
- MCP: `context7` (confirm `.with_structured_output` usage, same as T8)
- Skill: NONE

**Done when**:
- [x] Consistent verdict → `status="auto-approved"`, using a fake/stub model.
- [x] Contradictory/uncertain verdict → `status="human-review"` with the guardrail's own reasoning in `decision_reason`.
- [x] Never calls any DB/use-case function — confirmed by the test providing no `conn`/`apply_decision` dependency at all (a `None` in `config["configurable"]` proves this node never reaches for it).
- [x] `FLOW:` log line + verdict, asserted.
- [x] Gate check passes: `uv run pytest packages/reimbursement -q -m "not integration"`.
- [x] Test count: +N.

**Tests**: unit
**Gate**: quick

---

### T12: `ApplyAgentDecision` node

**What**: `class ApplyAgentDecision` in `agent/nodes/apply_agent_decision.py` — constructor takes the same `apply_decision` reference as T10 (same object, injected twice — not a separate import). `__call__` calls `self._apply_decision(config["configurable"]["conn"], uuid, state["status"], state["decision_reason"])`, returns `{"persisted": ...}`. Never authors its own `status`/`decision_reason`.
**Where**: `packages/reimbursement/src/reimbursement/agent/nodes/apply_agent_decision.py`
**Depends on**: T7, T3
**Reuses**: `apply_decision` (T3), `Node` Protocol.
**Requirement**: AGD-18, AGD-19 (the persistence half).

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] Given a `state` with `status`/`decision_reason` already set (by a fake predecessor), calls `apply_decision` with exactly those values.
- [x] Ghost case (`apply_decision` returns `None`) → `persisted=False`, no exception raised.
- [x] Never invents its own `status`/`decision_reason` — a test asserts the node is a no-op on those two keys beyond echoing what was already there.
- [x] `FLOW:` log line + outcome, asserted.
- [x] Gate check passes: `uv run pytest packages/reimbursement -q -m "not integration"`.
- [x] Test count: +N.

**Tests**: unit
**Gate**: quick

---

### T13: Graph wiring — `agent/agent.py`

**What**: `build_graph()` constructs the Ollama client(s) (via T5's config), instantiates all 5 node classes with their dependencies, wires `START → extract_fields → validate`, `route_after_validate`, `route_after_apply_policies`, `analysis → apply_agent_decision → END`, compiles. `get_graph()` is the `@lru_cache(maxsize=1)` singleton (AD-023 pattern). `decide(reimbursement, conn)` invokes it with `config={"configurable": {"conn": conn}, "callbacks": [langfuse_handler()]}`.
**Where**: `packages/reimbursement/src/reimbursement/agent/agent.py`
**Depends on**: T8, T9, T10, T11, T12
**Reuses**: `@lru_cache` singleton pattern (AD-023), all 5 node classes.
**Requirement**: AGD-21..25 (traceability — LangFuse callback wiring), plus the "compiled once" proxy from `design.md` (L-003).

**Tools**:
- MCP: `context7` (confirm current `StateGraph`/`add_conditional_edges`/`langfuse.langchain.CallbackHandler` call shapes before wiring — already verified once in Design, re-confirm if the pinned versions differ from what T6 actually locked)
- Skill: NONE

**Done when**:
- [x] `get_graph() is get_graph()` (singleton proxy).
- [x] `build_graph()` (and therefore every node's `__init__`, every Ollama client constructor) is called exactly once across repeated `get_graph()` calls — asserted via a call-counter on a patched `build_graph`, not just object identity (L-003's "observable proxy" requirement).
- [x] Six routing tests, each with fakes at every LLM/DB boundary: reject → `auto-rejected`; `≤200` → `auto-approved`; `>2000` → `human-review`; ambiguous+consistent → `auto-approved`; ambiguous+contradictory → `human-review`; missing-field → `human-review`.
- [x] Every one of the six asserts a non-null `decision_reason` and a `logging`-captured `FLOW:` line per node actually visited on that path (AGD-21..23 end-to-end, at the graph level).
- [x] Gate check passes: `uv run pytest packages/reimbursement -q -m "not integration"`.
- [x] Test count: +N (at least 8: singleton ×2, routing ×6).

**Tests**: unit
**Gate**: quick

---

### T14: `validation.py` — invoke the graph from `_resolve`, with the R-011 interim floor

**What**: Inside `_resolve`'s existing `async with deps.pool.acquire(...) as conn:` block (no second acquire), right where it currently returns `MessageOutcome.RESOLVED`: build `reimbursement = Reimbursement.from_record(row)`, then call `final_state = await agent.decide(reimbursement, conn)` inside its own `try`/`except`. On success, log `final_state["status"]` and `final_state["persisted"]` (to distinguish a genuine write from the ghost case), return `MessageOutcome.RESOLVED`. On exception, write a durable `failure_log` record (`reimbursement.decision_failed`, uuid + exception detail, same shape `_failure_record` already produces) and return `MessageOutcome.LOGGED` — **this is the R-011 interim floor**: no retry, no auto-escalation to human-review.
**Where**: `packages/reimbursement/src/reimbursement/validation.py`
**Depends on**: T13
**Reuses**: The existing `conn` already open in `_resolve`; `Reimbursement.from_record` (T1); `_failure_record`, `failure_log.write` (already in this module).

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `_resolve`'s resolved path now calls `agent.decide()` and logs its result instead of the current single "resolved" log line and nothing else.
- [x] A new `DescribeDecideIntegration`-style test class in `test_validation.py`, using a fake graph/`agent.decide`, proves: success is logged with `status`; `persisted=False` (ghost) is logged distinctly; an exception from `decide()` is caught, written to `failure_log` (asserted via a fake/spy), and returns `MessageOutcome.LOGGED` — **does not propagate** (matches the R-011 interim floor, not the old "propagate uncaught" draft).
- [x] All of `test_validation.py`'s pre-existing cases (ghost-drop, stale-ignored, retry-ceiling escalation, malformed message) still pass unchanged.
- [x] Gate check passes: `uv run pytest packages/reimbursement -q -m "not integration"`.
- [x] Test count: +N.

**Tests**: unit
**Gate**: quick

**Commit**: `feat(reimbursement): invoke the decision graph from the resolved-message path`

---

### T15: End-to-end integration proof

**What**: Extend `packages/reimbursement/tests/test_integration.py` with one `@pytest.mark.integration` case: a real Postgres row (via `migrated_db`), a real `Reimbursement` message consumed, the real `agent.decide()` call path exercised with a **fake** model injected (via `build_graph`'s dependency points, or a test-only graph-construction override) so no real Ollama call happens, asserting the row's `status`/`decision_reason` actually landed in Postgres via `apply_decision`.
**Where**: `packages/reimbursement/tests/test_integration.py`
**Depends on**: T14
**Reuses**: `migrated_db` (root `conftest.py`), `helpers.valid_reimbursement_item`, the existing `_run_agent`-style harness already in this file.

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] One integration case: a `≤200`, fresh-receipt item ends with `status="auto-approved"` and a non-null `decision_reason` in the real database row.
- [x] No real network call to Ollama occurs (confirmed by the fake model raising if actually invoked with unexpected args, or by asserting no `httpx`/socket activity — whichever the existing harness makes easiest).
- [x] Gate check passes: `uv run pytest -q` (full, Docker running).
- [x] Test count recorded explicitly in the commit message (integration suite total, not just unit).

**Tests**: integration
**Gate**: full

**Commit**: `test(reimbursement): prove the decision graph writes a real status/decision_reason end to end`

---

## Phase Execution Map

```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6

Phase 1:  T1 ──→ T2 ──→ T3 ──→ T4
Phase 2:  T5 ──→ T6
Phase 3:  T7
Phase 4:  T8 ──→ T9 ──→ T10 ──→ T11 ──→ T12
Phase 5:  T13
Phase 6:  T14 ──→ T15
```

Execution is strictly sequential — there is no intra-phase parallelism. 15 tasks total, packed into two ~7-8-task batches if sub-agent delegation is accepted (see the offer below).

---

## Task Granularity Check

| Task | Scope | Status |
| --- | --- | --- |
| T1: `Reimbursement` model | 1 class + `from_record` + its test class | ✅ Granular |
| T2: `update_decision` in repository | 1 function + its test class | ✅ Granular |
| T3: `apply_decision` use case | 1 new file, 1 function | ✅ Granular |
| T4: `escalate_existing` refactor + fake update | 2 tightly-coupled files (refactor + the fake it breaks) | ✅ Granular (cohesive, same change) |
| T5: Ollama config | 1 file, 1 dataclass extension | ✅ Granular |
| T6: `langchain-ollama` dependency | 1 dependency + lock | ✅ Granular |
| T7: `schema.py` state extension | 1 file | ✅ Granular |
| T8: `ExtractFields` node | 1 class + its prompt placeholder | ✅ Granular |
| T9: `Validate` node | 1 class | ✅ Granular |
| T10: `ApplyPolicies` node | 1 class | ✅ Granular |
| T11: `Analysis` node | 1 class + its prompt placeholder | ✅ Granular |
| T12: `ApplyAgentDecision` node | 1 class | ✅ Granular |
| T13: Graph wiring | 1 file (`agent.py`) | ✅ Granular |
| T14: `validation.py` extension | 1 file (modify) | ✅ Granular |
| T15: Integration proof | 1 test file (modify) | ✅ Granular |

---

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| --- | --- | --- | --- |
| T1 | None | — | ✅ Match |
| T2 | None | T1 → T2 (phase order) | ✅ Match |
| T3 | T2 | T2 → T3 | ✅ Match |
| T4 | T3 | T3 → T4 | ✅ Match |
| T5 | None | — | ✅ Match |
| T6 | None | T5 → T6 (phase order) | ✅ Match |
| T7 | T1 | T1 → T7 (cross-phase) | ✅ Match |
| T8 | T5, T6, T7 | T7 → T8; T6 → T8 (cross-phase) | ✅ Match |
| T9 | T7 | T8 → T9 (phase order) | ✅ Match |
| T10 | T7, T3 | T9 → T10 (phase order); T3 → T10 (cross-phase) | ✅ Match |
| T11 | T5, T6, T7 | T10 → T11 (phase order) | ✅ Match |
| T12 | T7, T3 | T11 → T12 (phase order) | ✅ Match |
| T13 | T8, T9, T10, T11, T12 | Phase 4 → Phase 5 | ✅ Match |
| T14 | T13 | Phase 5 → Phase 6 | ✅ Match |
| T15 | T14 | T14 → T15 | ✅ Match |

No task depends on a later-phase task; all cross-phase dependencies (T1→T7, T3→T10/T12, T5/T6→T8/T11) point backward only.

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| --- | --- | --- | --- | --- |
| T1 | `shared.models.Reimbursement` | unit | unit | ✅ OK |
| T2 | Repository | unit | unit | ✅ OK |
| T3 | Use case | unit | unit | ✅ OK |
| T4 | Use case (refactor) | unit | unit | ✅ OK |
| T5 | Config | unit | unit | ✅ OK |
| T6 | Dependency/build | none | none | ✅ OK |
| T7 | Schema (TypedDict/Protocol) | none | none | ✅ OK |
| T8 | Graph node | unit | unit | ✅ OK |
| T9 | Graph node | unit | unit | ✅ OK |
| T10 | Graph node | unit | unit | ✅ OK |
| T11 | Graph node | unit | unit | ✅ OK |
| T12 | Graph node | unit | unit | ✅ OK |
| T13 | Graph wiring | unit | unit | ✅ OK |
| T14 | Consumer integration seam | unit | unit | ✅ OK |
| T15 | Consumer end-to-end | integration | integration | ✅ OK |

All ✅ — no restructuring required before presenting.

---

## Tools

For each task, I'll use: Bash + Read/Edit/Write for all implementation and test work; `context7` MCP specifically for T6, T8, T11, T13 (LangChain/Ollama/LangGraph/Langfuse API shapes — never guessed, per this repo's own standing instruction). No other project-specific skills apply to this feature's tasks.

---

## Sub-Agent Delegation

15 tasks — above the ~8-task inline threshold. Proposed split: **Batch 1 = Phase 1–3 (T1–T7, 7 tasks)**, **Batch 2 = Phase 4 (T8–T12, 5 tasks)**, **Batch 3 = Phase 5–6 (T13–T15, 3 tasks)** — or execute inline in this session. Want me to dispatch sub-agent batches, or run it inline here?
