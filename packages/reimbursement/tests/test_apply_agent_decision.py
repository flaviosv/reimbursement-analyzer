import logging
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from agent_fakes import FakeApplyDecision
from reimbursement.agent.nodes.apply_agent_decision import ApplyAgentDecision
from reimbursement.models import Reimbursement

pytestmark = pytest.mark.anyio


def _state(
    status: str, decision_reason: str, uuid: object = None, extracted: dict | None = None
) -> dict:
    return {
        "reimbursement": Reimbursement(uuid=uuid or uuid4(), original_payload={}),
        "status": status,
        "decision_reason": decision_reason,
        "extracted": extracted or {},
    }


class DescribeApplyAgentDecision:
    async def it_persists_exactly_the_status_and_reason_already_set_by_a_predecessor(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()
        conn = object()
        fake = FakeApplyDecision(result=uuid)
        node = ApplyAgentDecision(apply_decision=fake)
        state = _state(
            "human-review", "required field(s) unresolved by extraction: value", uuid=uuid
        )

        with caplog.at_level(logging.INFO):
            result = await node(state, {"configurable": {"conn": conn}})

        assert fake.calls == [
            (conn, uuid, "human-review", "required field(s) unresolved by extraction: value")
        ]
        assert result == {"persisted": True}
        assert any(
            "FLOW: Executing 'apply_agent_decision' node" in r.message for r in caplog.records
        )

    async def it_reports_persisted_false_without_raising_on_a_ghost_uuid(self) -> None:
        fake = FakeApplyDecision(result=None)
        node = ApplyAgentDecision(apply_decision=fake)
        state = _state("auto-approved", "value 150 <= 200 threshold")

        result = await node(state, {"configurable": {"conn": object()}})

        assert result == {"persisted": False}

    async def it_never_invents_its_own_status_or_decision_reason(self) -> None:
        fake = FakeApplyDecision(result=uuid4())
        node = ApplyAgentDecision(apply_decision=fake)
        state = _state("human-review", "original reason from a predecessor")

        result = await node(state, {"configurable": {"conn": object()}})

        assert "status" not in result
        assert "decision_reason" not in result
        assert fake.calls[0][2] == "human-review"
        assert fake.calls[0][3] == "original reason from a predecessor"

    async def it_backfills_the_resolved_extraction_onto_apply_decision(self) -> None:
        fake = FakeApplyDecision(result=uuid4())
        node = ApplyAgentDecision(apply_decision=fake)
        state = _state(
            "auto-approved",
            "consistent per the guardrail",
            extracted={"value": 1000.0, "currency": "BRL", "receipts_date": date(2026, 4, 1)},
        )

        await node(state, {"configurable": {"conn": object()}})

        assert fake.receipts_calls == [(Decimal("1000.0"), date(2026, 4, 1), "BRL")]

    async def it_skips_a_negative_extracted_value_rather_than_persisting_it(self) -> None:
        # receipts_value's DB column has a >=0 CHECK constraint.
        fake = FakeApplyDecision(result=uuid4())
        node = ApplyAgentDecision(apply_decision=fake)
        state = _state(
            "human-review",
            "unused",
            extracted={"value": -5.0, "currency": "BRL", "receipts_date": date(2026, 4, 1)},
        )

        await node(state, {"configurable": {"conn": object()}})

        assert fake.receipts_calls[0][0] is None

    async def it_propagates_an_apply_decision_write_failure_uncaught(self) -> None:
        # R-011's interim floor (validation.py's _decide) is the layer that
        # catches this — the node itself must not swallow it.
        fake = FakeApplyDecision(error=RuntimeError("connection reset"))
        node = ApplyAgentDecision(apply_decision=fake)
        state = _state("human-review", "required field(s) unresolved by extraction: value")

        with pytest.raises(RuntimeError, match="connection reset"):
            await node(state, {"configurable": {"conn": object()}})
