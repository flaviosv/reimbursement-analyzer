import logging
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from agent_fakes import FakeAcquirePool, FakeApplyDecision
from reimbursement.agent.nodes import apply_policies as apply_policies_module
from reimbursement.agent.nodes.apply_policies import (
    ApplyPolicies,
    PolicyRule,
    route_after_apply_policies,
)
from reimbursement.metrics import reimbursement_policy_rule_triggered_total
from reimbursement.models import Reimbursement

pytestmark = pytest.mark.anyio


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz: object = None) -> datetime:
        return datetime(2026, 4, 10, 9, 15, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch: pytest.MonkeyPatch) -> None:
    # apply_policies.py's reject rule must measure staleness against the
    # server clock, never the client-supplied submitted_at (AD-030-adjacent:
    # submitted_at is unauthenticated and DB-constrained only against the
    # future, so a requester could otherwise backdate it to dodge the rule).
    # 2026-04-10 is the reference date every days_old computation below is
    # measured against.
    monkeypatch.setattr(apply_policies_module, "datetime", _FixedDatetime)


def _state(*, value: float, receipts_date: date, uuid: object = None) -> dict:
    return {
        "reimbursement": Reimbursement(uuid=uuid or uuid4(), original_payload={}),
        "extracted": {"value": value, "currency": "BRL", "receipts_date": receipts_date},
    }


def _config(conn: object) -> dict:
    return {"configurable": {"pool": FakeAcquirePool(conn), "acquire_timeout_seconds": 5.0}}


class DescribeApplyPolicies:
    @pytest.mark.parametrize(
        ("value", "receipts_date", "expected_status"),
        [
            pytest.param(5000, date(2026, 1, 9), "auto-rejected", id="91_days_old_regardless_of_value"),
            pytest.param(150, date(2026, 1, 10), "auto-approved", id="exactly_90_days_old_not_rejected"),
            pytest.param(5000, date(2026, 1, 1), "auto-rejected", id="old_and_over_2000_reject_wins"),
            pytest.param(200, date(2026, 4, 1), "auto-approved", id="exactly_200_ceiling"),
            pytest.param(2000.01, date(2026, 4, 1), "human-review", id="just_over_2000_floor"),
            pytest.param(0, date(2026, 4, 1), "auto-approved", id="zero_value_clears_the_ceiling"),
            pytest.param(-5, date(2026, 4, 1), "auto-approved", id="negative_value_clears_the_ceiling"),
        ],
    )
    async def it_resolves_the_expected_status_at_each_threshold(
        self, value: float, receipts_date: date, expected_status: str
    ) -> None:
        # Zero/negative: spec.md states no floor/sanity check beyond the
        # stated thresholds — the ceiling rule doesn't distinguish them from
        # any other small value.
        fake = FakeApplyDecision(result=uuid4())
        node = ApplyPolicies(apply_decision=fake)
        state = _state(value=value, receipts_date=receipts_date)

        result = await node(state, _config(object()))

        assert result["status"] == expected_status

    async def it_persists_the_reject_reason_and_calls_apply_decision_with_the_resolved_conn(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()
        conn = object()
        fake = FakeApplyDecision(result=uuid)
        node = ApplyPolicies(apply_decision=fake)
        state = _state(value=5000, receipts_date=date(2026, 1, 9), uuid=uuid)  # 91 days old

        with caplog.at_level(logging.INFO):
            result = await node(state, _config(conn))

        assert "2026-01-09" in result["decision_reason"]
        assert "2026-04-10" in result["decision_reason"]  # the frozen "today"
        assert result["persisted"] is True
        assert fake.calls == [(conn, uuid, "auto-rejected", result["decision_reason"])]
        assert any("FLOW: Executing 'apply_policies' node" in r.message for r in caplog.records)

    async def it_names_the_ceiling_rule_and_resolved_value_in_the_200_reason(self) -> None:
        uuid = uuid4()
        conn = object()
        fake = FakeApplyDecision(result=uuid)
        node = ApplyPolicies(apply_decision=fake)
        state = _state(value=200, receipts_date=date(2026, 4, 1), uuid=uuid)

        result = await node(state, _config(conn))

        assert fake.calls == [(conn, uuid, "auto-approved", result["decision_reason"])]
        # AGD-13: reason names the ceiling rule and the resolved value.
        assert "200" in result["decision_reason"]

    async def it_names_the_floor_rule_and_resolved_value_in_the_2000_01_reason(self) -> None:
        uuid = uuid4()
        conn = object()
        fake = FakeApplyDecision(result=uuid)
        node = ApplyPolicies(apply_decision=fake)
        state = _state(value=2000.01, receipts_date=date(2026, 4, 1), uuid=uuid)

        result = await node(state, _config(conn))

        assert fake.calls == [(conn, uuid, "human-review", result["decision_reason"])]
        # AGD-16: reason names the floor rule and the resolved value.
        assert "2000.01" in result["decision_reason"]

    async def it_skips_persisting_a_negative_receipts_value_but_still_auto_approves(self) -> None:
        # receipts_value's DB column has a >=0 CHECK constraint; the
        # threshold rule itself doesn't gate on this (spec.md), the
        # persistence layer does.
        fake = FakeApplyDecision(result=uuid4())
        node = ApplyPolicies(apply_decision=fake)
        state = _state(value=-5, receipts_date=date(2026, 4, 1))

        await node(state, _config(object()))

        assert fake.receipts_calls[0][0] is None

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

    async def it_reports_persisted_false_without_raising_on_a_ghost_uuid(self) -> None:
        fake = FakeApplyDecision(result=None)
        node = ApplyPolicies(apply_decision=fake)
        state = _state(value=150, receipts_date=date(2026, 4, 1))

        result = await node(state, _config(object()))

        assert result["persisted"] is False

    async def it_rejects_a_stale_receipt_even_when_submitted_at_matches_the_receipt_date(
        self,
    ) -> None:
        # submitted_at is client-supplied and unauthenticated: backdating it
        # to equal receipts_date used to zero out days_old and defeat the
        # reject rule entirely. The rule must measure against the server
        # clock (frozen to 2026-04-10 above), not this payload field.
        fake = FakeApplyDecision(result=uuid4())
        node = ApplyPolicies(apply_decision=fake)
        state = {
            "reimbursement": Reimbursement(
                uuid=uuid4(),
                original_payload={"submitted_at": "2026-01-09T00:00:00Z"},
            ),
            "extracted": {"value": 80, "currency": "BRL", "receipts_date": date(2026, 1, 9)},
        }

        result = await node(state, _config(object()))

        assert result["status"] == "auto-rejected"


class DescribePolicyRuleTriggeredMetric:
    async def it_increments_the_stale_receipt_reject_label_when_that_rule_fires(self) -> None:
        fake = FakeApplyDecision(result=uuid4())
        node = ApplyPolicies(apply_decision=fake)
        state = _state(value=5000, receipts_date=date(2026, 1, 9))  # 91 days old
        before = reimbursement_policy_rule_triggered_total.labels(
            PolicyRule.STALE_RECEIPT_REJECT.value
        )._value.get()

        result = await node(state, _config(object()))

        assert result["status"] == "auto-rejected"
        after = reimbursement_policy_rule_triggered_total.labels(
            PolicyRule.STALE_RECEIPT_REJECT.value
        )._value.get()
        assert after == before + 1

    async def it_increments_the_low_value_auto_approve_label_when_that_rule_fires(self) -> None:
        fake = FakeApplyDecision(result=uuid4())
        node = ApplyPolicies(apply_decision=fake)
        state = _state(value=150, receipts_date=date(2026, 4, 1))
        before = reimbursement_policy_rule_triggered_total.labels(
            PolicyRule.LOW_VALUE_AUTO_APPROVE.value
        )._value.get()

        result = await node(state, _config(object()))

        assert result["status"] == "auto-approved"
        after = reimbursement_policy_rule_triggered_total.labels(
            PolicyRule.LOW_VALUE_AUTO_APPROVE.value
        )._value.get()
        assert after == before + 1

    async def it_increments_the_high_value_human_review_label_when_that_rule_fires(self) -> None:
        fake = FakeApplyDecision(result=uuid4())
        node = ApplyPolicies(apply_decision=fake)
        state = _state(value=2000.01, receipts_date=date(2026, 4, 1))
        before = reimbursement_policy_rule_triggered_total.labels(
            PolicyRule.HIGH_VALUE_HUMAN_REVIEW.value
        )._value.get()

        result = await node(state, _config(object()))

        assert result["status"] == "human-review"
        after = reimbursement_policy_rule_triggered_total.labels(
            PolicyRule.HIGH_VALUE_HUMAN_REVIEW.value
        )._value.get()
        assert after == before + 1

    async def it_does_not_increment_any_rule_label_on_the_llm_judgment_path(self) -> None:
        fake = FakeApplyDecision(result=uuid4())
        node = ApplyPolicies(apply_decision=fake)
        state = _state(value=1000, receipts_date=date(2026, 4, 1))
        before = {
            rule.value: reimbursement_policy_rule_triggered_total.labels(rule.value)._value.get()
            for rule in PolicyRule
        }

        result = await node(state, _config(object()))

        assert result == {"requires_llm_judgment": True}
        for rule in PolicyRule:
            assert (
                reimbursement_policy_rule_triggered_total.labels(rule.value)._value.get()
                == before[rule.value]
            )

    async def it_increments_exactly_once_regardless_of_the_dbs_write_outcome(self) -> None:
        # A ghost apply_decision write (persisted=False) still means the
        # rule genuinely fired — the counter tracks rule evaluation, not the
        # DB write's success.
        fake = FakeApplyDecision(result=None)
        node = ApplyPolicies(apply_decision=fake)
        state = _state(value=150, receipts_date=date(2026, 4, 1))
        before = reimbursement_policy_rule_triggered_total.labels(
            PolicyRule.LOW_VALUE_AUTO_APPROVE.value
        )._value.get()

        result = await node(state, _config(object()))

        assert result["persisted"] is False
        after = reimbursement_policy_rule_triggered_total.labels(
            PolicyRule.LOW_VALUE_AUTO_APPROVE.value
        )._value.get()
        assert after == before + 1


class DescribeRouteAfterApplyPolicies:
    def it_routes_to_analysis_when_llm_judgment_is_required(self) -> None:
        assert route_after_apply_policies({"requires_llm_judgment": True}) == "analysis"

    def it_routes_to_end_when_a_deterministic_rule_already_decided(self) -> None:
        assert route_after_apply_policies({"requires_llm_judgment": False}) == "__end__"
