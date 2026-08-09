from datetime import date
from typing import TypedDict

from shared.models import DecisionStatus

from reimbursement.models import Reimbursement


class ExtractedFields(TypedDict, total=False):
    value: float | None
    currency: str | None
    receipts_date: date | None


class State(TypedDict):
    reimbursement: Reimbursement
    extracted: ExtractedFields

    # Routing-only signals — read by conditional-edge functions, never by
    # another node's decision logic.
    missing_fields: list[str]
    requires_llm_judgment: bool

    # The decision — written ONCE, by exactly one of the three deciding
    # nodes per run (validate | apply_policies | analysis).
    status: DecisionStatus | None
    decision_reason: str | None

    # Set by whichever node actually calls apply_decision (apply_policies or
    # apply_agent_decision) — False on the 0-rows-affected ghost case.
    persisted: bool | None

    guardrail_verdict: bool | None
