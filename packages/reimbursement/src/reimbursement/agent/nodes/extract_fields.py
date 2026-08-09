"""Resolve `value`, `currency`, `receipts_date` from the full payload — one
unconditional LLM call (AGD-01..04). Runs regardless of whether the payload
already carries a directly-usable field such as `claimed_amount_brl`: that
field is already part of the payload dump the model sees, so no separate
pre-check skips or short-circuits the call."""

import json
import logging
from datetime import date
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableConfig
from pydantic import BaseModel

from reimbursement.schema import ExtractedFields, State

logger = logging.getLogger(__name__)


class ExtractedFieldsSchema(BaseModel):
    """This node's own structured-output contract — bound onto `model` via
    `.with_structured_output` at graph-build time (agent.py)."""

    value: float | None = None
    currency: str | None = None
    receipts_date: date | None = None


class ExtractFields:
    def __init__(self, model: Runnable, prompt: ChatPromptTemplate) -> None:
        self._model = model
        self._prompt = prompt

    async def __call__(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        logger.info("FLOW: Executing 'extract_fields' node")

        payload = state["reimbursement"].original_payload
        messages = self._prompt.format_messages(payload=json.dumps(payload))
        result = await self._model.ainvoke(messages)

        extracted: ExtractedFields = {
            "value": result.value,
            "currency": result.currency,
            "receipts_date": result.receipts_date,
        }
        logger.info(
            "FLOW: extract_fields resolved value=%s currency=%s receipts_date=%s",
            extracted["value"],
            extracted["currency"],
            extracted["receipts_date"],
        )
        return {"extracted": extracted}
