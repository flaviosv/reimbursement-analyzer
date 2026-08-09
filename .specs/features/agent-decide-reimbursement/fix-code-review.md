# Comment-Triage Plan — PR #10

Review `4892390236` (submitted, state COMMENTED). 41 threads fetched, 0 resolved, 0 pending, 0 with a user comment on them.

Classification rule applied uniformly: no user comment on a thread → **auto-fix**. Several threads are duplicate/overlapping observations of the same root cause (code-review and tests-code-review independently flagged the same lines); those are grouped into one fix unit — resolving the code resolves every thread in the group, applied as one atomic commit.

User decisions taken before drafting (both raised as genuinely open questions, not mechanical):
- **LangFuse (FU-1):** infra already fully provisioned in `docker-compose.yml` (`LANGFUSE_HOST`/`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` already passed to the `reimbursement` service) and CLAUDE.md states wiring is required before this code path ships — not a new decision, wiring it for real.
- **Placeholder prompts (FU-2):** user confirmed nothing is live yet and said to just fix it directly — writing real prompts, no feature-flag gate.

## Fix Units (## Parallel unless noted — no two units in the same group touch the same file)

### Group A — done directly (Sonnet), high-stakes / cross-cutting, sequential within the group where files overlap

| Unit | Threads | Files | Direction |
|---|---|---|---|
| FU-1 LangFuse wiring | s8, tf, uM, tR, ti, tU, v1, vW, ul, vx | `agent/agent.py`, `pyproject.toml`, `uv.lock`, `tests/test_langfuse.py`, `tests/test_agent.py` | Add real `langfuse` dependency (verify current SDK API via Context7), wire `CallbackHandler()`, memoize it in `build_graph()`, stop CRITICAL-level flood on the always-fires branch, add tests for the "installed but unreachable" branch, `decide()`'s callback wiring, and `build_graph()`'s real body |
| FU-3 Pool/timeout scoping | tM, uQ | `validation.py`, node call sites | Don't hold the pool connection for the LLM round-trips; add an explicit LLM timeout |
| FU-13/FU-14 validation.py hardening | ux, vu | `validation.py` | `DECIDED_EVENT` gains `decision_reason`; `Reimbursement.from_record` moved inside `_decide`'s try/except |
| FU-2 Real prompts | uH | `agent/prompts/extract_fields.py`, `agent/prompts/analysis.py` | Replace TODO placeholders with real prompts |
| FU-4 Auto-approve bounds check | tB | `agent/nodes/apply_policies.py` or `extract_fields.py` | Reject non-plausible (≤0) extracted values before the `<=200` auto-approve fast path |
| FU-5 Model/prompt attribution | tG | `agent/nodes/analysis.py` / `apply_agent_decision.py` | Persist model identifier alongside `decision_reason` |
| FU-11 receipts_value/date/currency persistence | tl | `repository.py`, `apply_decision.py` | `update_decision` also persists resolved value/date/currency |
| FU-12 Move Reimbursement out of shared | tp | `shared/models.py` → `reimbursement/...`, import call sites | Single-consumer class relocated per CONVENTIONS.md |
| FU-15 apply_policies.py test hardening | vh, vm, u2, v_, v6 | `tests/test_apply_policies.py` | Parametrize threshold cases; add non-positive value, ghost-write, malformed `submitted_at` cases |
| FU-20 Integration test coverage | up, vR | `tests/test_integration.py` | Assert `LANGFUSE_FALLBACK_EVENT`; add an `ApplyAgentDecision`-path case against real Postgres |
| FU-21 spec.md AC amendment | ta | `spec.md` | Reconcile AGD-01 AC1 wording with the AGD-26 allow-list |
| FU-18 test_models.py malformed JSON | vA | wherever FU-12 lands `models.py`'s tests | Add malformed-JSON case |

### Group B — delegated to Haiku fix-drafting (parallel, capped at 4)

| Unit | Threads | Files | Direction |
|---|---|---|---|
| FU-6 Types consolidation | uC, ts, ue | new `agent/types.py`, `schema.py`, `apply_policies.py`, `apply_agent_decision.py`, `agent.py` | Move `Node` + de-duplicated `ApplyDecision` (typed `asyncpg.Connection`, not `Any`) into one shared location |
| FU-7 DecisionStatus Literal | tw, uZ | `apply_decision.py`, `repository.py`, `schema.py` | `Literal["auto-approved","auto-rejected","human-review"]` at the signature level |
| FU-8 route_after_apply_policies typing | t0 | `apply_policies.py` | `-> Literal["analysis", "__end__"]` |
| FU-9 Config default dedup | t6 | `config.py` | Single source per default, referenced from both places |
| FU-10 Ollama infra doc | t- | `docker-compose.yml` or `STACK.md`/`INTEGRATIONS.md` | Add compose service or document external requirement |
| FU-16 agent_fakes.py error injection | u6, vO | `tests/agent_fakes.py` + node test files | `error=` case coverage for `ExtractFields`/`Analysis`; `FakeApplyDecision` gains an `error` param |
| FU-17 test_apply_decision.py parametrize | u9 | `shared/tests/reimbursement/use_cases/test_apply_decision.py` | Cover all 3 status values against real Postgres |
| FU-19 test_repository.py isolation fix | vH, vb | `shared/tests/reimbursement/test_repository.py` | Relative-order assertion instead of exact-slice + magic future date |

## Resolution

All 41 threads resolve silently (no reply comment — auto-fix classification) once their fix unit's commit lands.
