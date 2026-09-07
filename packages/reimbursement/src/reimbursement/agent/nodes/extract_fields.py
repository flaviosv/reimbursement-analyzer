"""Resolve `value`, `currency`, `receipts_date` from the full payload — one
unconditional LLM call (AGD-01..04). Runs regardless of whether the payload
already carries a directly-usable field such as `claimed_amount_brl`: that
field is already part of the payload dump the model sees, so no separate
pre-check skips or short-circuits the call."""

import logging
from datetime import date
from typing import Any

from langchain_core.runnables import Runnable, RunnableConfig
from pydantic import BaseModel

from reimbursement.metrics import reimbursement_agent_llm_calls_total
from reimbursement.schema import ExtractedFields, State
from reimbursement.agent.prompts.extract_fields import get_extract_fields_prompt

logger = logging.getLogger(__name__)

class ExtractedFieldsSchema(BaseModel):
    """This node's own structured-output contract — bound onto `model` via
    `.with_structured_output` at graph-build time (agent.py)."""

    value: float | None = None
    currency: str | None = None
    receipts_date: date | None = None


class ExtractFields:
    def __init__(self, model: Runnable, model_name: str) -> None:
        self._model = model
        # Matches Analysis's own attribution logging — the project's
        # traceability NFR requires attributing every LLM-influenced step to
        # the specific model version that performed it.
        self._model_name = model_name

    async def __call__(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        uuid = state["reimbursement"].uuid
        logger.info("FLOW: Executing 'extract_fields' node uuid=%s", uuid)

        payload = state["reimbursement"].original_payload
        # AGD-26: submitted_by is PII and must never reach the extraction
        # prompt — previously unenforced (masked at collection time by an
        # unrelated import bug; surfaced and fixed here since this is the
        # exact call site the model-config rewiring already touches).
        prompt_payload = {key: value for key, value in payload.items() if key != "submitted_by"}

        messages = [get_extract_fields_prompt(prompt_payload)]
        try:
            result = await self._model.ainvoke(messages)
        except Exception:
            reimbursement_agent_llm_calls_total.labels(self._model_name, "failure").inc()
            raise
        reimbursement_agent_llm_calls_total.labels(self._model_name, "success").inc()

        extracted: ExtractedFields = {
            "value": result.value,
            "currency": result.currency,
            "receipts_date": result.receipts_date,
        }
        logger.info(
            "FLOW: extract_fields resolved value=%s currency=%s receipts_date=%s model=%s uuid=%s",
            extracted["value"],
            extracted["currency"],
            extracted["receipts_date"],
            self._model_name,
            uuid,
        )
        return {"extracted": extracted}
