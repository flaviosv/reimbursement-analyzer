"""The LLM-as-judge guardrail for the ambiguous zone only (AGD-17..20) —
never runs for the other three outcomes. Decides, doesn't persist."""

import json
import logging
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableConfig
from pydantic import BaseModel

from reimbursement.schema import State

logger = logging.getLogger(__name__)


class GuardrailVerdict(BaseModel):
    """This node's own structured-output contract — bound onto `model` via
    `.with_structured_output` at graph-build time (agent.py)."""

    consistent: bool
    reasoning: str


class Analysis:
    def __init__(self, model: Runnable, prompt: ChatPromptTemplate) -> None:
        self._model = model
        self._prompt = prompt

    async def __call__(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        logger.info("FLOW: Executing 'analysis' node")

        extracted = state["extracted"]
        messages = self._prompt.format_messages(extracted=json.dumps(extracted, default=str))
        verdict = await self._model.ainvoke(messages)

        status = "auto-approved" if verdict.consistent else "human-review"
        logger.info("FLOW: analysis guardrail_verdict=%s status=%s", verdict.consistent, status)

        return {
            "guardrail_verdict": verdict.consistent,
            "status": status,
            "decision_reason": verdict.reasoning,
        }
