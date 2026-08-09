import logging
from uuid import uuid4

import pytest
from agent_fakes import FakeApplyDecision
from shared.models import Reimbursement

from reimbursement.agent.nodes.apply_agent_decision import ApplyAgentDecision

pytestmark = pytest.mark.anyio


def _state(status: str, decision_reason: str, uuid: object = None) -> dict:
    return {
        "reimbursement": Reimbursement(uuid=uuid or uuid4(), original_payload={}),
        "status": status,
        "decision_reason": decision_reason,
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
