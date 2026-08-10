"""The three purely-deterministic outcomes (AGD-05..08 reject, AGD-12..16
<=200/>2000), reject checked first — decides and persists."""

import logging
from datetime import UTC, datetime
from decimal import Decimal
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
        reimbursement = state["reimbursement"]
        uuid = reimbursement.uuid
        logger.info("FLOW: Executing 'apply_policies' node uuid=%s", uuid)

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
            logger.info(
                "FLOW: apply_policies fired no deterministic rule; requires_llm_judgment=True uuid=%s",
                uuid,
            )
            return {"requires_llm_judgment": True}

        logger.info("FLOW: apply_policies rule fired: status=%s uuid=%s", status, uuid)
        # receipts_value's own DB column has a >=0 CHECK constraint; spec.md
        # deliberately lets a zero/negative extracted value clear the <=200
        # ceiling unmodified (no floor on the threshold rule itself), so a
        # negative value is skipped here rather than persisted and failing
        # the write outright.
        receipts_value = Decimal(str(value)) if value is not None and value >= 0 else None
        # Acquired only for this write, not held across the LLM round-trip(s)
        # earlier in the graph (agent.decide() threads the pool, not a live
        # connection, for exactly this reason).
        pool = config["configurable"]["pool"]
        timeout = config["configurable"]["acquire_timeout_seconds"]
        async with pool.acquire(timeout=timeout) as conn:
            result = await self._apply_decision(
                conn,
                reimbursement.uuid,
                status,
                decision_reason,
                receipts_value=receipts_value,
                receipts_date=receipts_date,
                currency=extracted["currency"],
            )
        persisted = result is not None
        logger.info("FLOW: apply_policies apply_decision outcome: persisted=%s uuid=%s", persisted, uuid)

        return {
            "status": status,
            "decision_reason": decision_reason,
            "persisted": persisted,
            "requires_llm_judgment": False,
        }


def route_after_apply_policies(state: State) -> Literal["analysis", "__end__"]:
    return "analysis" if state["requires_llm_judgment"] else "__end__"
