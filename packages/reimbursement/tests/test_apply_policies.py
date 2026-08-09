import logging
from datetime import date
from uuid import uuid4

import pytest
from agent_fakes import FakeAcquirePool, FakeApplyDecision
from reimbursement.agent.nodes.apply_policies import (
    ApplyPolicies,
    route_after_apply_policies,
)
from reimbursement.models import Reimbursement

pytestmark = pytest.mark.anyio

# 2026-04-10 — the reference date every days_old computation below is
# measured against.
_SUBMITTED_AT = "2026-04-10T09:15:00Z"


def _state(*, value: float, receipts_date: date, uuid: object = None) -> dict:
    return {
        "reimbursement": Reimbursement(
            uuid=uuid or uuid4(), original_payload={"submitted_at": _SUBMITTED_AT}
        ),
        "extracted": {"value": value, "currency": "BRL", "receipts_date": receipts_date},
    }


def _config(conn: object) -> dict:
    return {"configurable": {"pool": FakeAcquirePool(conn), "acquire_timeout_seconds": 5.0}}


class DescribeApplyPolicies:
    async def it_rejects_a_receipt_91_days_old_regardless_of_value(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()
        conn = object()
        fake = FakeApplyDecision(result=uuid)
        node = ApplyPolicies(apply_decision=fake)
        state = _state(value=5000, receipts_date=date(2026, 1, 9), uuid=uuid)  # 91 days before

        with caplog.at_level(logging.INFO):
            result = await node(state, _config(conn))

        assert result["status"] == "auto-rejected"
        assert "2026-01-09" in result["decision_reason"]
        assert "2026-04-10" in result["decision_reason"]
        assert result["persisted"] is True
        assert fake.calls == [(conn, uuid, "auto-rejected", result["decision_reason"])]
        assert any("FLOW: Executing 'apply_policies' node" in r.message for r in caplog.records)

    async def it_does_not_reject_a_receipt_exactly_90_days_old(self) -> None:
        fake = FakeApplyDecision(result=uuid4())
        node = ApplyPolicies(apply_decision=fake)
        state = _state(value=150, receipts_date=date(2026, 1, 10))  # exactly 90 days before

        result = await node(state, _config(object()))

        assert result["status"] != "auto-rejected"
        assert result["status"] == "auto-approved"

    async def it_rejects_ahead_of_the_mandatory_over_2000_human_review_rule(self) -> None:
        fake = FakeApplyDecision(result=uuid4())
        node = ApplyPolicies(apply_decision=fake)
        # Old AND > 2000: reject must win (AD-030, AGD-07).
        state = _state(value=5000, receipts_date=date(2026, 1, 1))

        result = await node(state, _config(object()))

        assert result["status"] == "auto-rejected"

    async def it_auto_approves_at_exactly_the_200_ceiling_via_apply_decision(self) -> None:
        uuid = uuid4()
        conn = object()
        fake = FakeApplyDecision(result=uuid)
        node = ApplyPolicies(apply_decision=fake)
        state = _state(value=200, receipts_date=date(2026, 4, 1), uuid=uuid)

        result = await node(state, _config(conn))

        assert result["status"] == "auto-approved"
        assert fake.calls == [(conn, uuid, "auto-approved", result["decision_reason"])]
        # AGD-13: reason names the ceiling rule and the resolved value.
        assert "200" in result["decision_reason"]

    async def it_routes_a_value_over_2000_to_human_review_via_apply_decision(self) -> None:
        uuid = uuid4()
        conn = object()
        fake = FakeApplyDecision(result=uuid)
        node = ApplyPolicies(apply_decision=fake)
        state = _state(value=2000.01, receipts_date=date(2026, 4, 1), uuid=uuid)

        result = await node(state, _config(conn))

        assert result["status"] == "human-review"
        assert fake.calls == [(conn, uuid, "human-review", result["decision_reason"])]
        # AGD-16: reason names the floor rule and the resolved value.
        assert "2000.01" in result["decision_reason"]

    async def it_leaves_exactly_2000_in_the_ambiguous_zone_not_the_mandatory_rule(self) -> None:
        fake = FakeApplyDecision(result=uuid4())
        node = ApplyPolicies(apply_decision=fake)
        state = _state(value=2000, receipts_date=date(2026, 4, 1))

        result = await node(state, _config(object()))

        assert result == {"requires_llm_judgment": True}

    async def it_returns_requires_llm_judgment_for_the_ambiguous_zone_without_calling_apply_decision(
        self,
    ) -> None:
        fake = FakeApplyDecision(result=uuid4())
        node = ApplyPolicies(apply_decision=fake)
        state = _state(value=1000, receipts_date=date(2026, 4, 1))

        result = await node(state, _config(object()))

        assert result == {"requires_llm_judgment": True}
        assert len(fake.calls) == 0


class DescribeRouteAfterApplyPolicies:
    def it_routes_to_analysis_when_llm_judgment_is_required(self) -> None:
        assert route_after_apply_policies({"requires_llm_judgment": True}) == "analysis"

    def it_routes_to_end_when_a_deterministic_rule_already_decided(self) -> None:
        assert route_after_apply_policies({"requires_llm_judgment": False}) == "__end__"
