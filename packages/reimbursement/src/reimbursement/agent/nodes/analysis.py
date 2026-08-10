"""The LLM-as-judge guardrail for the ambiguous zone only (AGD-17..20) —
never runs for the other three outcomes. Decides, doesn't persist."""

import logging
from typing import Any, Literal

from langchain_core.runnables import Runnable, RunnableConfig
from pydantic import BaseModel

from reimbursement.schema import State
from reimbursement.agent.prompts.analysis import get_analysis_prompt

logger = logging.getLogger(__name__)


class GuardrailVerdict(BaseModel):
    """This node's own structured-output contract — bound onto `model` via
    `.with_structured_output` at graph-build time (agent.py). Mirrors the
    {"status": ..., "reason": ...} shape the prompt (analysis.py) already
    instructs the model to produce, so the schema and the prompt agree."""

    status: Literal["auto-approved", "human-review"]
    reason: str


class Analysis:
    def __init__(self, model: Runnable, model_name: str) -> None:
        self._model = model
        # AGD-23/24's LangFuse trace already captures the full prompt/
        # response per call; this is the durable, no-cross-reference-needed
        # record of which model authored this specific decision_reason.
        self._model_name = model_name

    async def __call__(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        uuid = state["reimbursement"].uuid
        logger.info("FLOW: Executing 'analysis' node uuid=%s", uuid)

        extracted = state["extracted"]
        found_data = {
            "currency": extracted["currency"],
            "receipt_date": extracted["receipts_date"],
            "receipt_value": extracted["value"],
        }

        payload = state["reimbursement"].original_payload
        # AGD-26: submitted_by is PII and must never reach the analysis
        # prompt — mirrors extract_fields.py's existing pattern.
        request_data = {key: value for key, value in payload.items() if key != "submitted_by"}

        messages = [get_analysis_prompt(request_data, found_data)]
        verdict = await self._model.ainvoke(messages)

        guardrail_verdict = verdict.status == "auto-approved"
        logger.info(
            "FLOW: analysis guardrail_verdict=%s status=%s model=%s uuid=%s",
            guardrail_verdict,
            verdict.status,
            self._model_name,
            uuid,
        )

        return {
            "guardrail_verdict": guardrail_verdict,
            "status": verdict.status,
            "decision_reason": verdict.reason,
        }
