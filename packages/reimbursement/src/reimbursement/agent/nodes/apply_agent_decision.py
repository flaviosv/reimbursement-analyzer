"""The single, decision-agnostic finalizer — persists whatever status/
decision_reason is already in state, regardless of whether validate or
analysis put it there. Never authors its own status/decision_reason."""

import logging
from decimal import Decimal
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
        extracted = state.get("extracted") or {}
        value = extracted.get("value")
        # Mirrors ApplyPolicies's own guard: receipts_value's DB column has
        # a >=0 CHECK constraint.
        receipts_value = Decimal(str(value)) if value is not None and value >= 0 else None
        # Acquired only for this write — see agent.decide()'s own docstring
        # for why the graph is handed a pool, not a live connection.
        pool = config["configurable"]["pool"]
        timeout = config["configurable"]["acquire_timeout_seconds"]
        async with pool.acquire(timeout=timeout) as conn:
            result = await self._apply_decision(
                conn,
                reimbursement.uuid,
                state["status"],
                state["decision_reason"],
                receipts_value=receipts_value,
                receipts_date=extracted.get("receipts_date"),
                currency=extracted.get("currency"),
            )
        persisted = result is not None
        logger.info("FLOW: apply_agent_decision outcome: persisted=%s", persisted)

        return {"persisted": persisted}
