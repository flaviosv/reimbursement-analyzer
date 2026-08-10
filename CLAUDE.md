# Project Directives — reimbursementanalyzer

Project-specific instructions for Claude Code in this repo. For project context, see `docs/codebase/PROJECT.md` (overview/vision), `docs/SCOPE.md` (the project scope documentation — full requirements), and `.specs/STATE.md` (decisions/risks).

## Non-Functional Requirement — Full Traceability (Hard Requirement)

This service is mission-critical and operates in a financial domain: it drives monetary decisions partly from probabilistic LLM output. **Full traceability of every operation is a functional requirement, not an aspirational goal.** It applies to all new development — treat it with the same weight as correctness.

Source of truth for the requirement text: `docs/SCOPE.md` (`Non-functional`, `Audit trail`, `Traceability` sections). Update that doc first if the requirement itself changes — this file governs how Claude enforces it during implementation.

For any change that creates, modifies, or influences a reimbursement decision (deterministic rule evaluation, LLM/agent call, human review action):

- **Capture inputs and outputs.** Log the request payload, the rule(s) evaluated (with pass/fail), the LLM prompt/response (or a reference to a stored trace), and the final decision — enough to reconstruct *why* a decision was made without re-running it.
- **Attribute every action.** Record which actor produced it — the specific rule ID, the model/prompt version, or the human reviewer's identity — with a timestamp.
- **Wire up LLM tracing.** LangFuse is wired into `reimbursement`'s LangGraph decision graph (`docs/codebase/INTEGRATIONS.md`), and every trace carries `langfuse_session_id=<reimbursement uuid>`, so a trace is queryable by the row it decided. Any new agent/LLM code path must emit a trace to it, session-correlated the same way — this is required before that code path ships, not deferred as follow-up.
- **No silent fallbacks.** If a traceability sink (e.g., LangFuse) is unavailable, fall back to durable file/structured logging rather than dropping the trace. A decision must never happen without a durable record of how it was reached.
- **PII** Be careful with PII, do not output/log PII information