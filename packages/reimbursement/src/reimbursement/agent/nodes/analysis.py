"""The LLM-as-judge guardrail for the ambiguous zone only (AGD-17..20) —
never runs for the other three outcomes. Decides, doesn't persist."""

import json
import logging
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableConfig
from pydantic import BaseModel

from reimbursement.schema import State
from reimbursement.agent.prompts.analysis import get_analysis_prompt

logger = logging.getLogger(__name__)


class GuardrailVerdict(BaseModel):
    """This node's own structured-output contract — bound onto `model` via
    `.with_structured_output` at graph-build time (agent.py)."""

    consistent: bool
    reasoning: str


class Analysis:
    def __init__(self, model: Runnable, model_name: str) -> None:
        self._model = model
        # AGD-23/24's LangFuse trace already captures the full prompt/
        # response per call; this is the durable, no-cross-reference-needed
        # record of which model authored this specific decision_reason.
        self._model_name = model_name

    async def __call__(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        logger.info("FLOW: Executing 'analysis' node")

        extracted = state["extracted"]
        messages = [get_analysis_prompt(extracted)]
        verdict = await self._model.ainvoke(messages)

        status = "auto-approved" if verdict.consistent else "human-review"
        logger.info(
            "FLOW: analysis guardrail_verdict=%s status=%s model=%s",
            verdict.consistent,
            status,
            self._model_name,
        )

        return {
            "guardrail_verdict": verdict.consistent,
            "status": status,
            "decision_reason": verdict.reasoning,
        }
