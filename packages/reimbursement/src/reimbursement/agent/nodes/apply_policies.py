"""The three purely-deterministic outcomes (AGD-05..08 reject, AGD-12..16
<=200/>2000), reject checked first — decides and persists."""

import logging
from datetime import UTC, datetime
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig

from reimbursement.agent.types import ApplyDecision
from reimbursement.schema import State

logger = logging.getLogger(__name__)

REJECT_THRESHOLD_DAYS = 90
AUTO_APPROVE_CEILING = 200
HUMAN_REVIEW_FLOOR = 2000


class ApplyPolicies:
    def __init__(self, apply_decision: ApplyDecision) -> None:
        self._apply_decision = apply_decision

    async def __call__(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        logger.info("FLOW: Executing 'apply_policies' node")

        reimbursement = state["reimbursement"]
        extracted = state["extracted"]
        value = extracted["value"]
        receipts_date = extracted["receipts_date"]

        submitted_at = datetime.fromisoformat(reimbursement.original_payload["submitted_at"])
        submitted_date = submitted_at.astimezone(UTC).date()
        days_old = (submitted_date - receipts_date).days

        if days_old > REJECT_THRESHOLD_DAYS:
            status = "auto-rejected"
            decision_reason = (
                f"reject rule: receipt dated {receipts_date.isoformat()} is {days_old} days "
                f"before submission ({submitted_date.isoformat()}), older than the "
                f"{REJECT_THRESHOLD_DAYS}-day limit"
            )
        elif value <= AUTO_APPROVE_CEILING:
            status = "auto-approved"
            decision_reason = (
                f"auto-approve rule: resolved value {value} is within the "
                f"{AUTO_APPROVE_CEILING} ceiling"
            )
        elif value > HUMAN_REVIEW_FLOOR:
            status = "human-review"
            decision_reason = (
                f"mandatory human-review rule: resolved value {value} exceeds the "
                f"{HUMAN_REVIEW_FLOOR} floor"
            )
        else:
            logger.info("FLOW: apply_policies fired no deterministic rule; requires_llm_judgment=True")
            return {"requires_llm_judgment": True}

        logger.info("FLOW: apply_policies rule fired: status=%s", status)
        result = await self._apply_decision(
            config["configurable"]["conn"], reimbursement.uuid, status, decision_reason
        )
        persisted = result is not None
        logger.info("FLOW: apply_policies apply_decision outcome: persisted=%s", persisted)

        return {
            "status": status,
            "decision_reason": decision_reason,
            "persisted": persisted,
            "requires_llm_judgment": False,
        }


def route_after_apply_policies(state: State) -> Literal["analysis", "__end__"]:
    return "analysis" if state["requires_llm_judgment"] else "__end__"
