"""The single, decision-agnostic finalizer — persists whatever status/
decision_reason is already in state, regardless of whether validate or
analysis put it there. Never authors its own status/decision_reason."""

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from reimbursement.agent.types import ApplyDecision
from reimbursement.schema import State

logger = logging.getLogger(__name__)


class ApplyAgentDecision:
    def __init__(self, apply_decision: ApplyDecision) -> None:
        self._apply_decision = apply_decision

    async def __call__(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        logger.info("FLOW: Executing 'apply_agent_decision' node")

        reimbursement = state["reimbursement"]
        result = await self._apply_decision(
            config["configurable"]["conn"],
            reimbursement.uuid,
            state["status"],
            state["decision_reason"],
        )
        persisted = result is not None
        logger.info("FLOW: apply_agent_decision outcome: persisted=%s", persisted)

        return {"persisted": persisted}
