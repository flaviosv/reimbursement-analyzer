"""Gate on completeness (AGD-09..11) — routes the graph, and is one of the
three nodes that decides (the missing-field -> human-review case is its own
decision)."""

import logging
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig

from reimbursement.schema import State

logger = logging.getLogger(__name__)


class Validate:
    async def __call__(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        uuid = state["reimbursement"].uuid
        logger.info("FLOW: Executing 'validate' node uuid=%s", uuid)

        extracted = state["extracted"]
        missing: list[str] = []
        if extracted.get("value") is None:
            missing.append("value")
        if extracted.get("receipts_date") is None:
            missing.append("receipts_date")

        logger.info("FLOW: validate found missing_fields=%s uuid=%s", missing, uuid)

        if not missing:
            return {"missing_fields": missing}

        return {
            "missing_fields": missing,
            "status": "human-review",
            "decision_reason": f"required field(s) unresolved by extraction: {', '.join(missing)}",
        }


def route_after_validate(state: State) -> Literal["apply_policies", "apply_agent_decision"]:
    return "apply_policies" if not state["missing_fields"] else "apply_agent_decision"
