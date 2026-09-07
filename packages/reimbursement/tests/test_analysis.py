import logging
from uuid import uuid4

import pytest
from agent_fakes import DEFAULT_TEST_MODEL_NAME, FakeStructuredModel
from reimbursement.agent.nodes.analysis import Analysis, GuardrailVerdict
from reimbursement.agent.prompts.analysis import get_analysis_prompt
from reimbursement.metrics import reimbursement_agent_llm_calls_total
from reimbursement.models import Reimbursement

pytestmark = pytest.mark.anyio


_PAYLOAD = {
    "request_id": "REQ-0001",
    "submitted_by": "ana.silva@company.com",
    "submitted_at": "2026-04-10T09:15:00Z",
    "raw_ocr_text": "BOM SABOR RESTAURANT LTD\nDATE 09/04/2026\nTOTAL R$ 93.50",
    "claimed_category": "meals",
    "claimed_amount_brl": 93.5,
}


def _state(payload: dict | None = None) -> dict:
    return {
        "reimbursement": Reimbursement(
            uuid=uuid4(), original_payload=payload if payload is not None else {}
        ),
        "extracted": {"value": 1000, "currency": "BRL", "receipts_date": None},
    }


class DescribeGuardrailVerdict:
    def it_exposes_exactly_status_and_reason(self) -> None:
        assert set(GuardrailVerdict.model_fields) == {"status", "reason"}


class DescribeGetAnalysisPrompt:
    def it_renders_a_literal_placeholder_substring_in_a_value_without_double_substitution(
        self,
    ) -> None:
        # Regression: chained .replace() calls re-scan an already-substituted
        # value for the other placeholder — a request_data/found_data value
        # (e.g. attacker-controlled raw_ocr_text) containing the literal
        # substring "{found_data}" would then get corrupted by the second
        # .replace() call. The single-pass substitution must render it as-is.
        request_data = {"raw_ocr_text": "injected {found_data} marker"}
        found_data = {"currency": "BRL"}

        message = get_analysis_prompt(request_data, found_data)

        assert "injected {found_data} marker" in str(message.content)


class DescribeAnalysis:
    async def it_auto_approves_on_a_consistent_verdict(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        model = FakeStructuredModel(
            result=GuardrailVerdict(status="auto-approved", reason="amount matches receipt text")
        )
        node = Analysis(model=model, model_name=DEFAULT_TEST_MODEL_NAME)

        # A None "configurable" proves this node never reaches for a
        # conn/apply_decision dependency — it would raise on a subscript of
        # None otherwise.
        with caplog.at_level(logging.INFO):
            result = await node(_state(), {"configurable": None})

        assert result["status"] == "auto-approved"
        assert result["guardrail_verdict"] is True
        assert result["decision_reason"] == "amount matches receipt text"
        assert any("FLOW: Executing 'analysis' node" in r.message for r in caplog.records)
        assert any("guardrail_verdict=True" in r.message for r in caplog.records)
        # AGD-23/24: attributes which model authored this decision_reason.
        assert any(f"model={DEFAULT_TEST_MODEL_NAME}" in r.message for r in caplog.records)

    async def it_routes_to_human_review_on_a_contradictory_verdict_with_the_guardrails_own_reasoning(
        self,
    ) -> None:
        model = FakeStructuredModel(
            result=GuardrailVerdict(
                status="human-review", reason="claimed amount contradicts the OCR total"
            )
        )
        node = Analysis(model=model, model_name=DEFAULT_TEST_MODEL_NAME)

        result = await node(_state(), {"configurable": None})

        assert result["status"] == "human-review"
        assert result["guardrail_verdict"] is False
        assert result["decision_reason"] == "claimed amount contradicts the OCR total"

    async def it_invokes_the_guardrail_exactly_once(self) -> None:
        model = FakeStructuredModel(result=GuardrailVerdict(status="auto-approved", reason="ok"))
        node = Analysis(model=model, model_name=DEFAULT_TEST_MODEL_NAME)

        await node(_state(), {"configurable": None})

        assert len(model.calls) == 1

    async def it_propagates_an_llm_failure_uncaught(self) -> None:
        # R-011's interim floor (validation.py's _decide) is the layer that
        # catches this — the node itself must not swallow it.
        model = FakeStructuredModel(error=RuntimeError("groq unreachable"))
        node = Analysis(model=model, model_name=DEFAULT_TEST_MODEL_NAME)

        with pytest.raises(RuntimeError, match="groq unreachable"):
            await node(_state(), {"configurable": None})

    async def it_increments_the_llm_calls_counter_with_outcome_success(self) -> None:
        model = FakeStructuredModel(result=GuardrailVerdict(status="auto-approved", reason="ok"))
        node = Analysis(model=model, model_name=DEFAULT_TEST_MODEL_NAME)
        before = reimbursement_agent_llm_calls_total.labels(
            DEFAULT_TEST_MODEL_NAME, "success"
        )._value.get()

        await node(_state(), {"configurable": None})

        after = reimbursement_agent_llm_calls_total.labels(
            DEFAULT_TEST_MODEL_NAME, "success"
        )._value.get()
        assert after == before + 1

    async def it_increments_the_llm_calls_counter_with_outcome_failure_and_still_raises(
        self,
    ) -> None:
        model = FakeStructuredModel(error=RuntimeError("groq unreachable"))
        node = Analysis(model=model, model_name=DEFAULT_TEST_MODEL_NAME)
        before = reimbursement_agent_llm_calls_total.labels(
            DEFAULT_TEST_MODEL_NAME, "failure"
        )._value.get()

        with pytest.raises(RuntimeError, match="groq unreachable"):
            await node(_state(), {"configurable": None})

        after = reimbursement_agent_llm_calls_total.labels(
            DEFAULT_TEST_MODEL_NAME, "failure"
        )._value.get()
        assert after == before + 1

    async def it_includes_found_datas_mapped_values_in_the_rendered_prompt(self) -> None:
        # AGT-01: extracted's internal field names (value/receipts_date) must
        # be translated to the prompt's documented names (receipt_value/
        # receipt_date) before reaching the model.
        model = FakeStructuredModel(result=GuardrailVerdict(status="auto-approved", reason="ok"))
        node = Analysis(model=model, model_name=DEFAULT_TEST_MODEL_NAME)

        await node(_state(_PAYLOAD), {"configurable": None})

        assert len(model.calls) == 1
        rendered = " ".join(str(message.content) for message in model.calls[0])
        assert "1000" in rendered
        assert "BRL" in rendered
        assert "{found_data}" not in rendered

    async def it_includes_request_datas_payload_values_in_the_rendered_prompt(self) -> None:
        # AGT-01: the original (PII-stripped) payload must reach the prompt
        # as request_data so the guardrail can compare it against found_data.
        model = FakeStructuredModel(result=GuardrailVerdict(status="auto-approved", reason="ok"))
        node = Analysis(model=model, model_name=DEFAULT_TEST_MODEL_NAME)

        await node(_state(_PAYLOAD), {"configurable": None})

        assert len(model.calls) == 1
        rendered = " ".join(str(message.content) for message in model.calls[0])
        # str(dict) escapes newlines, so raw_ocr_text is checked line-by-line.
        assert _PAYLOAD["raw_ocr_text"].splitlines()[0] in rendered
        assert str(_PAYLOAD["claimed_amount_brl"]) in rendered
        assert "{request_data}" not in rendered

    async def it_never_includes_submitted_by_in_the_rendered_prompt(self) -> None:
        # AGD-26: submitted_by (PII) must not reach the analysis prompt.
        model = FakeStructuredModel(result=GuardrailVerdict(status="auto-approved", reason="ok"))
        node = Analysis(model=model, model_name=DEFAULT_TEST_MODEL_NAME)

        await node(_state(_PAYLOAD), {"configurable": None})

        assert len(model.calls) == 1
        rendered = " ".join(str(message.content) for message in model.calls[0])
        assert _PAYLOAD["submitted_by"] not in rendered
