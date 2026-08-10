# Agent Model Config Validation

**Date**: 2026-08-09
**Spec**: `.specs/features/agent-model-config/spec.md`
**Diff range**: `main..HEAD` (8 commits, 104d313..459bc5d)
**Verifier**: independent sub-agent (author ≠ verifier)

---

## Task Completion

| Task | Status  | Notes |
| ---- | ------- | ----- |
| T1   | ✅ Done | `AIConfig`/`ModelConfig`/`AgentModelsConfig`/`_require_env` added, `ollama_*` fields removed (`config.py`) |
| T2   | ✅ Done | Autouse fixture in `conftest.py` sets all six vars |
| T3   | ✅ Done | `langchain-ollama` → `langchain-groq` in `pyproject.toml`/`uv.lock` |
| T4   | ✅ Done | `GuardrailVerdict.reasoning` → `reason`; dead `self._prompt`/unused imports removed |
| T5   | ✅ Done | `ExtractFields` gains `model_name` attribution logging; AGD-26 `submitted_by` filter added (documented deviation, verified correct — see below) |
| T6   | ✅ Done | `build_graph()` constructs two independent `init_chat_model("groq:...")` calls |
| T7   | ✅ Done | `.env.sample`/`.env` carry all six vars with non-blank placeholders; parity test added |
| T8   | ✅ Done | Zero `ollama` matches remain; full suite passes (115) |

---

## Spec-Anchored Acceptance Criteria

### P1: Reimbursement agent runs its LLM nodes on Groq, each independently configured

| Criterion (WHEN X THEN Y) | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --- | --- | --- |
| AMC-01: `extract_fields` model built from its own `model_name`/`temperature` | `init_chat_model("groq:<extract_fields.model_name>", temperature=<extract_fields.temperature>, ...)` | `agent.py:74-79` construction; `tests/test_agent.py:329-330` — `assert extract_call["model"] == "groq:model-a"`, `assert extract_call["temperature"] == 0.1` | ✅ PASS |
| AMC-02: `analysis` model built independently | `init_chat_model("groq:<analysis.model_name>", temperature=<analysis.temperature>, ...)` | `agent.py:80-85`; `tests/test_agent.py:331-332` — `assert analysis_call["model"] == "groq:model-b"`, `assert analysis_call["temperature"] == 0.9` | ✅ PASS |
| AMC-03: two different `model_name`s → two distinct model instances | Distinct constructed instances, not one shared instance read twice | `tests/test_agent.py:340` — `assert extract_call["model"] != analysis_call["model"]` (values `groq:model-a` vs `groq:model-b`, from two separate `init_chat_model` calls, `calls` list len 2 at `:327`) | ✅ PASS |
| AMC-04: `pyproject.toml` shows `langchain-ollama` removed, `langchain-groq` present | Exact dependency swap | `packages/reimbursement/pyproject.toml:13` — `"langchain-groq>=1.1.3"` present; `langchain-ollama` absent (diff confirmed); `uv.lock` shows `ollama`/`langchain-ollama` packages removed, `groq`/`langchain-groq` added | ✅ PASS |
| AMC-05: zero `ollama`/`Ollama`/`OLLAMA` matches in source+tests | Zero matches | `grep -ril "ollama" packages/reimbursement` → empty (exit 1, 0 matches) — verified directly by this Verifier | ✅ PASS |

### P1: Configuration fails fast on missing required values

| Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --- | --- | --- |
| AMC-06: `GROQ_API_KEY` unset → error naming it | `ValueError` message contains `GROQ_API_KEY` | `config.py:20-24` `_require_env`; `tests/test_config.py:45-49` — `pytest.raises(ValueError, match="GROQ_API_KEY")` | ✅ PASS |
| AMC-07: `EXTRACT_FIELDS_MODEL_NAME` unset → error naming it | `ValueError` message contains the var name | `tests/test_config.py:51-55` — `pytest.raises(ValueError, match="EXTRACT_FIELDS_MODEL_NAME")` | ✅ PASS |
| AMC-08: `ANALYSIS_MODEL_NAME` unset → error naming it | `ValueError` message contains the var name | `tests/test_config.py:57-61` — `pytest.raises(ValueError, match="ANALYSIS_MODEL_NAME")` | ✅ PASS |
| AMC-09: `*_TEMPERATURE` unset → defaults `0.0`, no raise | `temperature == 0.0` | `tests/test_config.py:70-75` (`extract_fields.temperature == 0.0`), `:77-82` (`analysis.temperature == 0.0`) | ✅ PASS |
| AMC-10: `AI_TIMEOUT_SECONDS` unset → defaults `30.0` | `timeout_seconds == 30.0` | `tests/test_config.py:63-68` — `assert config.ai.timeout_seconds == 30.0` | ✅ PASS |
| AMC-11: `.env.sample` shows all six vars, non-blank | All six keys present, non-blank | `.env.sample:41-52`  (`GROQ_API_KEY=gsk_changeme...`, `AI_TIMEOUT_SECONDS=30`, `EXTRACT_FIELDS_MODEL_NAME=llama-3.3-70b-versatile`, `EXTRACT_FIELDS_TEMPERATURE=0.0`, `ANALYSIS_MODEL_NAME=llama-3.3-70b-versatile`, `ANALYSIS_TEMPERATURE=0.0`); `tests/test_config.py:180-188` — `DescribeEnvSampleParity.it_carries_all_six_ai_env_vars_with_non_blank_placeholders` reads `.env.sample` directly and asserts non-blank | ✅ PASS |
| AMC-12: `.env` mirrors `.env.sample`'s six vars | Same six vars set locally | Verified directly by this Verifier: `.env` (gitignored, not in diff) contains all six keys with matching placeholder shape (`GROQ_API_KEY=gsk_changeme...`, `AI_TIMEOUT_SECONDS=30`, both model names `llama-3.3-70b-versatile`, both temperatures `0.0`) | ✅ PASS |

### P2: Groq credential is centralized, not duplicated per node

| Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --- | --- | --- |
| AMC-13: both nodes source `api_key` from single `AIConfig.api_key` | One field, one read, both nodes reference it | `agent.py:76,82` — both `init_chat_model` calls pass `api_key=config.ai.api_key`; `tests/test_agent.py:335` — `assert extract_call["api_key"] == analysis_call["api_key"] == "gsk_shared_key"`; structurally: `tests/test_config.py:103-111` — `assert "api_key" not in model_config_fields` / `assert "api_key" in ai_config_fields` | ✅ PASS |
| AMC-14: `GROQ_API_KEY` change propagates to both nodes from one read | Same value reaches both nodes' models from a single config read | Same evidence as AMC-13 (`test_agent.py:335`) — both call sites read from the one `config.ai.api_key` object constructed once per `load_agent_config()` call; no per-node duplication exists to drift. Spec's "reloads its config" phrasing describes the pre-existing `@lru_cache`/process-restart mechanic (unchanged by this feature, not independently re-tested) | ✅ PASS (mechanism-level; reload semantics unchanged from pre-existing `lru_cache` pattern, not re-tested — reasonable, not a gap) |

### P2: `extract_fields` gets model-attribution logging parity with `analysis`

| Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --- | --- | --- |
| AMC-15: `extract_fields` completion log includes `model_name` | Log line contains configured `model_name`, `analysis`-style | `extract_fields.py:49-55` — `logger.info("FLOW: extract_fields resolved value=%s currency=%s receipts_date=%s model=%s", ..., self._model_name)`; `tests/test_extract_fields.py:52-65` — `assert any("model=llama-3.3-70b-versatile" in line for line in completion_lines)` | ✅ PASS |

### P3: `analysis`'s structured-output field renamed for clarity

| Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --- | --- | --- |
| AMC-16: `GuardrailVerdict` exposes exactly `consistent`, `reason` | Exact two-field set, `reason` not `reasoning` | `analysis.py:16-20` — `consistent: bool`, `reason: str`; `tests/test_analysis.py:14-15` — `assert set(GuardrailVerdict.model_fields) == {"consistent", "reason"}` | ✅ PASS |
| AMC-17: `status` derivation from `consistent` unchanged | `consistent=True` → `"auto-approved"`, `consistent=False` → `"human-review"` | `analysis.py:39` — `status = "auto-approved" if verdict.consistent else "human-review"`; `tests/test_analysis.py::it_auto_approves_on_a_consistent_verdict` (line ~19), `::it_routes_to_human_review_on_a_contradictory_verdict...` (line ~42) — both assert `result["status"]` against the derived value; also re-confirmed live by this Verifier's mutation #3 (see Discrimination Sensor) | ✅ PASS |

**Status**: ✅ All 17 ACs covered — 17/17 matched spec-defined outcome, 0 spec-precision gaps.

---

## Deliberate Deviation Check (T5's AGD-26 PII fix)

`extract_fields.py:40-44` filters `submitted_by` out of the payload before
building the prompt. Verified:

- `extract_fields.py:44` — `prompt_payload = {key: value for key, value in payload.items() if key != "submitted_by"}`, then `get_extract_fields_prompt(prompt_payload)` (not the raw `payload`).
- `tests/test_extract_fields.py:95-106` — `it_never_includes_submitted_by_in_the_rendered_prompt` constructs a payload containing `submitted_by`, invokes the node, inspects the actual rendered message content sent to the fake model, and asserts the PII string is absent: `assert _PAYLOAD["submitted_by"] not in rendered`.
- Commit `ee06b25` documents this as an intentional bundled fix, consistent with the task instructions given to this Verifier.

**Result**: Correct and properly tested — confirmed as intentional, not flagged as a gap.

---

## Discrimination Sensor

| Mutation | File:line | Description | Killed? |
| --- | --- | --- | --- |
| 1 | `packages/reimbursement/src/reimbursement/config.py:22` | `_require_env`: flipped `if not value:` → `if value:` (inverts fail-fast condition) | ✅ Killed — 14 of 16 `test_config.py` tests failed (`ValueError: GROQ_API_KEY environment variable is required` raised even with the var set) |
| 2 | `packages/reimbursement/src/reimbursement/agent/agent.py:75` | `extract_model`'s `init_chat_model` call changed to read `config.models.analysis.model_name` instead of `config.models.extract_fields.model_name` (collapses per-node distinctness) | ✅ Killed — `test_agent.py::DescribeBuildGraph::it_constructs_two_independent_groq_models_one_per_node` failed (`AssertionError: assert 'groq:model-b' == 'groq:model-a'`) |
| 3 | `packages/reimbursement/src/reimbursement/agent/nodes/analysis.py:39` | Flipped status derivation: `"human-review" if verdict.consistent else "auto-approved"` (inverted ternary) | ✅ Killed — 4 tests failed across `test_analysis.py` (2) and `test_agent.py` (2) routing/analysis assertions |

Each mutation was applied individually, confirmed to fail its targeted test(s), then reverted via `git checkout --`; `git status --short` confirmed a clean tree before proceeding to the next mutation and again after the third.

**Sensor depth**: lightweight (3 targeted mutations, default tier)
**Result**: 3/3 killed — PASS ✅

---

## Code Quality

| Principle | Status |
| --- | --- |
| Minimum code | ✅ — new dataclasses/helper are the minimum shape needed; no speculative fields |
| Surgical changes | ✅ — only files required by the design's Components section were touched; `prompts/*.py` (pre-existing broken-template bug, out of scope) untouched |
| No scope creep | ✅ — the one deviation (AGD-26 `submitted_by` filter in T5) is explicitly documented in the commit message and spec-task instructions as an intentional, justified bundle, not silent scope creep |
| Matches patterns | ✅ — `AIConfig`/`ModelConfig`/`AgentModelsConfig` mirror `shared.config`'s frozen-dataclass + `@lru_cache` shape exactly, as design.md specifies |
| Spec-anchored outcome check (asserted values match spec) | ✅ — see AC table above, 17/17 |
| Per-layer Coverage Expectation met (domain 1:1 ACs; routes happy+edge+error) | ✅ — `test_config.py` covers all 3 required + 3 optional branches individually; `test_agent.py` covers construction, distinctness, and the no-monkeypatch real-wiring path |
| Every test maps to a spec requirement — no unclaimed tests | ✅ — spot-checked; every new/changed test traces to an AMC-* ID or an edge case (fail-fast tests → AMC-06..10, distinctness → AMC-03, attribution → AMC-15, rename → AMC-16/17, env parity → AMC-11) |
| Documented guidelines followed | ✅ — `docs/codebase/TESTING.md`'s `Describe*`/`it_*` convention followed throughout (cited directly in tasks.md's Test Coverage Matrix) |

Additional observations (not gaps, noted for completeness):
- `config.py`'s stale comment block referencing "Ollama-only... reimbursement-only today" was updated in the same file already being rewritten (T1) — not an out-of-scope drive-by, since the whole `ollama_*` field block it commented on was removed.
- `analysis.py`/`extract_fields.py`'s dead `self._prompt = prompt` line and unused `ChatPromptTemplate`/`json` imports were removed exactly as tasks.md's T4/T5 specified (pre-flagged in design.md's Risks table as a pre-existing `NameError` bug, not a drive-by refactor).

---

## Edge Cases

- [x] Groq unreachable/timeout → same unchanged failure propagation: `tests/test_analysis.py::it_propagates_an_llm_failure_uncaught` (raises `RuntimeError("groq unreachable")` uncaught), `tests/test_extract_fields.py::it_propagates_an_llm_failure_uncaught` (same pattern), `tests/test_validation.py` (`"groq unreachable"` surfaces through `_decide`'s failure_log path) — all pass, no new catch/retry introduced
- [x] Real graph construction with no monkeypatching succeeds without live network/credentials: `tests/test_agent.py::it_wires_the_real_groq_bound_models_into_the_expected_node_set` passes using conftest's autouse placeholder env vars (T2)
- [x] Unrelated tests calling `load_agent_config()` still succeed: full 115-test suite passes; `conftest.py`'s `_default_agent_ai_env` autouse fixture (T2) supplies all six vars package-wide

---

## Gate Check

- **Gate command**: `uv run pytest packages/reimbursement` (Full gate, per tasks.md's Gate Check Commands table — Docker running, integration tests included)
- **Result**: 115 passed, 0 failed, 0 skipped
- **Test count before feature (main, changed files only)**: 52 (`test_config.py`:9, `test_analysis.py`:4, `test_extract_fields.py`:5, `test_agent.py`:10, `test_integration.py`:6, `test_validation.py`:18 — counted via `def it_`/`async def it_` occurrences)
- **Test count after feature (changed files only)**: 62 (`test_config.py`:16, `test_analysis.py`:5, `test_extract_fields.py`:6, `test_agent.py`:11, `test_integration.py`:6, `test_validation.py`:18)
- **Delta**: +10 new tests, 0 deletions — matches the author's own per-commit "N passed" notes in commit messages (T8's commit records "115 passed", matching this run exactly)
- **Skipped tests**: none
- **Failures**: none

---

## Fix Plans (if issues found)

None — no gaps found.

---

## Requirement Traceability Update

| Requirement | Previous Status | New Status |
| --- | --- | --- |
| AMC-01 | Pending | ✅ Verified |
| AMC-02 | Pending | ✅ Verified |
| AMC-03 | Pending | ✅ Verified |
| AMC-04 | Pending | ✅ Verified |
| AMC-05 | Pending | ✅ Verified |
| AMC-06 | Pending | ✅ Verified |
| AMC-07 | Pending | ✅ Verified |
| AMC-08 | Pending | ✅ Verified |
| AMC-09 | Pending | ✅ Verified |
| AMC-10 | Pending | ✅ Verified |
| AMC-11 | Pending | ✅ Verified |
| AMC-12 | Pending | ✅ Verified |
| AMC-13 | Pending | ✅ Verified |
| AMC-14 | Pending | ✅ Verified |
| AMC-15 | Pending | ✅ Verified |
| AMC-16 | Pending | ✅ Verified |
| AMC-17 | Pending | ✅ Verified |

---

## Summary

**Overall**: ✅ Ready

**Spec-anchored check**: 17/17 ACs matched spec outcome, 0 spec-precision gaps
**Sensor**: 3/3 mutations killed
**Gate**: 115 passed, 0 failed, 0 skipped

**What works**: Full Groq migration (config, both node constructors, `build_graph()`), fail-fast config with precise error messages, centralized single-sourced `api_key`, `extract_fields` attribution logging, `GuardrailVerdict` rename, zero remaining Ollama references, `.env.sample`/`.env` parity, and the pre-existing PII leak (AGD-26) fixed at the same touched call site.

**Issues found**: None.

**Next steps**: None — feature ready to ship as-is.
