import logging
from datetime import date
from uuid import uuid4

import pytest
from agent_fakes import FakeStructuredModel
from shared.models import Reimbursement

from reimbursement.agent.nodes.extract_fields import ExtractedFieldsSchema, ExtractFields
from reimbursement.agent.prompts.extract_fields import PLACEHOLDER_PROMPT

pytestmark = pytest.mark.anyio

# sample.json-shaped: claimed_amount_brl present, receipts date only inside
# raw_ocr_text — mirrors docs/original/sample.json's REQ-0001.
_PAYLOAD = {
    "request_id": "REQ-0001",
    "submitted_by": "ana.silva@company.com",
    "submitted_at": "2026-04-10T09:15:00Z",
    "raw_ocr_text": "BOM SABOR RESTAURANT LTD\nDATE 09/04/2026\nTOTAL R$ 93.50",
    "claimed_category": "meals",
    "claimed_amount_brl": 93.5,
}


def _state(payload: dict) -> dict:
    return {"reimbursement": Reimbursement(uuid=uuid4(), original_payload=payload)}


class DescribeExtractFields:
    async def it_resolves_all_three_fields_from_a_sample_shaped_payload(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        model = FakeStructuredModel(
            result=ExtractedFieldsSchema(value=93.5, currency="BRL", receipts_date=date(2026, 4, 9))
        )
        node = ExtractFields(model=model, prompt=PLACEHOLDER_PROMPT)

        with caplog.at_level(logging.INFO):
            result = await node(_state(_PAYLOAD), {"configurable": {}})

        assert result["extracted"] == {
            "value": 93.5,
            "currency": "BRL",
            "receipts_date": date(2026, 4, 9),
        }
        # AGD-03: exactly one LLM invocation for the reimbursement.
        assert len(model.calls) == 1
        assert any("FLOW: Executing 'extract_fields' node" in r.message for r in caplog.records)

    async def it_invokes_the_model_unconditionally_even_when_claimed_amount_brl_is_present(
        self,
    ) -> None:
        # AGD-04: the call still happens, and its own (here, unresolved)
        # answer is used as-is — claimed_amount_brl being present does not
        # skip the call or silently substitute for a field the model itself
        # could not resolve.
        model = FakeStructuredModel(
            result=ExtractedFieldsSchema(value=None, currency=None, receipts_date=None)
        )
        node = ExtractFields(model=model, prompt=PLACEHOLDER_PROMPT)

        result = await node(_state(_PAYLOAD), {"configurable": {}})

        assert len(model.calls) == 1
        assert result["extracted"] == {"value": None, "currency": None, "receipts_date": None}

    async def it_never_returns_a_status_key(self) -> None:
        model = FakeStructuredModel(
            result=ExtractedFieldsSchema(value=64.8, currency="BRL", receipts_date=date(2026, 4, 11))
        )
        node = ExtractFields(model=model, prompt=PLACEHOLDER_PROMPT)

        result = await node(_state(_PAYLOAD), {"configurable": {}})

        assert "status" not in result
