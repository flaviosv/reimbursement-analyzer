import logging

import pytest
from agent_fakes import FakeStructuredModel
from reimbursement.agent.nodes.analysis import Analysis, GuardrailVerdict
from reimbursement.agent.prompts.analysis import PLACEHOLDER_PROMPT

pytestmark = pytest.mark.anyio


def _state() -> dict:
    return {"extracted": {"value": 1000, "currency": "BRL", "receipts_date": None}}


class DescribeAnalysis:
    async def it_auto_approves_on_a_consistent_verdict(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        model = FakeStructuredModel(
            result=GuardrailVerdict(consistent=True, reasoning="amount matches receipt text")
        )
        node = Analysis(model=model, prompt=PLACEHOLDER_PROMPT, model_name="llama3.2")

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
        assert any("model=llama3.2" in r.message for r in caplog.records)

    async def it_routes_to_human_review_on_a_contradictory_verdict_with_the_guardrails_own_reasoning(
        self,
    ) -> None:
        model = FakeStructuredModel(
            result=GuardrailVerdict(
                consistent=False, reasoning="claimed amount contradicts the OCR total"
            )
        )
        node = Analysis(model=model, prompt=PLACEHOLDER_PROMPT, model_name="llama3.2")

        result = await node(_state(), {"configurable": None})

        assert result["status"] == "human-review"
        assert result["guardrail_verdict"] is False
        assert result["decision_reason"] == "claimed amount contradicts the OCR total"

    async def it_invokes_the_guardrail_exactly_once(self) -> None:
        model = FakeStructuredModel(result=GuardrailVerdict(consistent=True, reasoning="ok"))
        node = Analysis(model=model, prompt=PLACEHOLDER_PROMPT, model_name="llama3.2")

        await node(_state(), {"configurable": None})

        assert len(model.calls) == 1

    async def it_propagates_an_llm_failure_uncaught(self) -> None:
        # R-011's interim floor (validation.py's _decide) is the layer that
        # catches this — the node itself must not swallow it.
        model = FakeStructuredModel(error=RuntimeError("ollama unreachable"))
        node = Analysis(model=model, prompt=PLACEHOLDER_PROMPT, model_name="llama3.2")

        with pytest.raises(RuntimeError, match="ollama unreachable"):
            await node(_state(), {"configurable": None})
