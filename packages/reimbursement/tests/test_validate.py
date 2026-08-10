import logging
from datetime import date
from uuid import uuid4

import pytest

from reimbursement.agent.nodes.validate import Validate, route_after_validate
from reimbursement.models import Reimbursement

pytestmark = pytest.mark.anyio

_UUID = uuid4()


def _state(*, value: float | None, receipts_date: date | None) -> dict:
    return {
        "reimbursement": Reimbursement(uuid=_UUID, original_payload={}),
        "extracted": {"value": value, "currency": "BRL", "receipts_date": receipts_date},
    }


class DescribeValidate:
    async def it_routes_a_missing_value_to_human_review_naming_the_field(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        node = Validate()

        with caplog.at_level(logging.INFO):
            result = await node(_state(value=None, receipts_date=date(2026, 4, 9)), {})

        assert result["missing_fields"] == ["value"]
        assert result["status"] == "human-review"
        assert "value" in result["decision_reason"]
        assert any("FLOW: Executing 'validate' node" in r.message for r in caplog.records)

    async def it_routes_a_missing_receipts_date_to_human_review_naming_the_field(self) -> None:
        node = Validate()

        result = await node(_state(value=100.0, receipts_date=None), {})

        assert result["missing_fields"] == ["receipts_date"]
        assert result["status"] == "human-review"
        assert "receipts_date" in result["decision_reason"]

    async def it_names_both_fields_and_never_auto_decides_when_both_are_missing(self) -> None:
        node = Validate()

        result = await node(_state(value=None, receipts_date=None), {})

        assert result["missing_fields"] == ["value", "receipts_date"]
        assert result["status"] == "human-review"
        assert result["status"] not in {"auto-approved", "auto-rejected"}
        assert "value" in result["decision_reason"]
        assert "receipts_date" in result["decision_reason"]

    async def it_returns_no_missing_fields_and_no_status_when_both_fields_are_present(self) -> None:
        node = Validate()

        result = await node(_state(value=100.0, receipts_date=date(2026, 4, 9)), {})

        assert result == {"missing_fields": []}
        assert "status" not in result


class DescribeRouteAfterValidate:
    def it_routes_to_apply_policies_when_no_fields_are_missing(self) -> None:
        assert route_after_validate({"missing_fields": []}) == "apply_policies"

    def it_routes_to_apply_agent_decision_when_a_field_is_missing(self) -> None:
        assert route_after_validate({"missing_fields": ["value"]}) == "apply_agent_decision"
