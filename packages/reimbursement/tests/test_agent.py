import logging
from datetime import date
from uuid import uuid4

import pytest
import reimbursement.agent.agent as agent
from agent_fakes import (
    DEFAULT_TEST_MODEL_NAME,
    FakeAcquirePool,
    FakeApplyDecision,
    FakeStructuredModel,
)
from reimbursement.agent.nodes.analysis import Analysis, GuardrailVerdict
from reimbursement.agent.nodes.apply_agent_decision import ApplyAgentDecision
from reimbursement.agent.nodes.apply_policies import ApplyPolicies
from reimbursement.agent.nodes.extract_fields import (
    ExtractedFieldsSchema,
    ExtractFields,
)
from reimbursement.agent.nodes.validate import Validate
from reimbursement.models import Reimbursement

pytestmark = pytest.mark.anyio

# 2026-04-10 — the reference date every days_old computation below is
# measured against, matching test_apply_policies.py's own fixed reference.
_SUBMITTED_AT = "2026-04-10T09:15:00Z"


class _Fakes:
    def __init__(
        self,
        *,
        extracted: ExtractedFieldsSchema,
        guardrail: GuardrailVerdict,
        apply_policies_result: object,
        apply_agent_decision_result: object,
    ) -> None:
        self.extract_model = FakeStructuredModel(result=extracted)
        self.analysis_model = FakeStructuredModel(result=guardrail)
        self.apply_policies_decision = FakeApplyDecision(result=apply_policies_result)
        self.apply_agent_decision_decision = FakeApplyDecision(result=apply_agent_decision_result)

    def wire(self) -> object:
        nodes = {
            "extract_fields": ExtractFields(
                model=self.extract_model, model_name=DEFAULT_TEST_MODEL_NAME
            ),
            "validate": Validate(),
            "apply_policies": ApplyPolicies(apply_decision=self.apply_policies_decision),
            "analysis": Analysis(model=self.analysis_model, model_name=DEFAULT_TEST_MODEL_NAME),
            "apply_agent_decision": ApplyAgentDecision(
                apply_decision=self.apply_agent_decision_decision
            ),
        }
        return agent._wire(nodes)


def _initial_state(uuid: object) -> dict:
    return {"reimbursement": Reimbursement(uuid=uuid, original_payload={"submitted_at": _SUBMITTED_AT})}


def _flow_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.message for r in caplog.records if r.message.startswith("FLOW: Executing")]


class DescribeGetGraphSingleton:
    def it_returns_the_same_compiled_graph_on_repeated_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent.get_graph.cache_clear()
        monkeypatch.setattr(agent, "build_graph", lambda: object())

        first = agent.get_graph()
        second = agent.get_graph()

        assert first is second

    def it_calls_build_graph_exactly_once_across_repeated_get_graph_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent.get_graph.cache_clear()
        calls: list[int] = []

        def _counting_build_graph() -> object:
            calls.append(1)
            return object()

        monkeypatch.setattr(agent, "build_graph", _counting_build_graph)

        agent.get_graph()
        agent.get_graph()
        agent.get_graph()

        assert len(calls) == 1


class DescribeGraphRouting:
    async def it_rejects_a_stale_receipt_regardless_of_value(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()
        fakes = _Fakes(
            extracted=ExtractedFieldsSchema(value=5000, currency="BRL", receipts_date=date(2026, 1, 9)),
            guardrail=GuardrailVerdict(consistent=True, reason="unused"),
            apply_policies_result=uuid,
            apply_agent_decision_result=uuid,
        )
        graph = fakes.wire()

        with caplog.at_level(logging.INFO):
            result = await graph.ainvoke(
                _initial_state(uuid), config={"configurable": {"pool": FakeAcquirePool(object()), "acquire_timeout_seconds": 5.0}}
            )

        assert result["status"] == "auto-rejected"
        assert result["decision_reason"] is not None
        assert fakes.apply_policies_decision.calls[0][1:] == (
            uuid,
            "auto-rejected",
            result["decision_reason"],
        )
        assert len(fakes.apply_agent_decision_decision.calls) == 0
        assert len(fakes.analysis_model.calls) == 0
        lines = _flow_lines(caplog)
        assert any("extract_fields" in line for line in lines)
        assert any("validate" in line for line in lines)
        assert any("apply_policies" in line for line in lines)
        assert not any("analysis" in line for line in lines)
        assert not any("apply_agent_decision" in line for line in lines)

    async def it_auto_approves_at_or_below_the_200_ceiling(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()
        fakes = _Fakes(
            extracted=ExtractedFieldsSchema(value=200, currency="BRL", receipts_date=date(2026, 4, 1)),
            guardrail=GuardrailVerdict(consistent=True, reason="unused"),
            apply_policies_result=uuid,
            apply_agent_decision_result=uuid,
        )
        graph = fakes.wire()

        with caplog.at_level(logging.INFO):
            result = await graph.ainvoke(
                _initial_state(uuid), config={"configurable": {"pool": FakeAcquirePool(object()), "acquire_timeout_seconds": 5.0}}
            )

        assert result["status"] == "auto-approved"
        assert result["decision_reason"] is not None
        assert fakes.apply_policies_decision.calls[0][1:] == (
            uuid,
            "auto-approved",
            result["decision_reason"],
        )
        assert len(fakes.apply_agent_decision_decision.calls) == 0
        assert len(fakes.analysis_model.calls) == 0
        lines = _flow_lines(caplog)
        assert any("apply_policies" in line for line in lines)
        assert not any("analysis" in line for line in lines)

    async def it_routes_a_value_over_2000_to_human_review_without_the_guardrail(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()
        fakes = _Fakes(
            extracted=ExtractedFieldsSchema(
                value=2000.01, currency="BRL", receipts_date=date(2026, 4, 1)
            ),
            guardrail=GuardrailVerdict(consistent=True, reason="unused"),
            apply_policies_result=uuid,
            apply_agent_decision_result=uuid,
        )
        graph = fakes.wire()

        with caplog.at_level(logging.INFO):
            result = await graph.ainvoke(
                _initial_state(uuid), config={"configurable": {"pool": FakeAcquirePool(object()), "acquire_timeout_seconds": 5.0}}
            )

        assert result["status"] == "human-review"
        assert result["decision_reason"] is not None
        assert fakes.apply_policies_decision.calls[0][1:] == (
            uuid,
            "human-review",
            result["decision_reason"],
        )
        assert len(fakes.apply_agent_decision_decision.calls) == 0
        assert len(fakes.analysis_model.calls) == 0
        lines = _flow_lines(caplog)
        assert any("apply_policies" in line for line in lines)
        assert not any("analysis" in line for line in lines)

    async def it_auto_approves_the_ambiguous_zone_on_a_consistent_guardrail_verdict(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()
        fakes = _Fakes(
            extracted=ExtractedFieldsSchema(value=1000, currency="BRL", receipts_date=date(2026, 4, 1)),
            guardrail=GuardrailVerdict(consistent=True, reason="amount matches receipt text"),
            apply_policies_result=uuid,
            apply_agent_decision_result=uuid,
        )
        graph = fakes.wire()

        with caplog.at_level(logging.INFO):
            result = await graph.ainvoke(
                _initial_state(uuid), config={"configurable": {"pool": FakeAcquirePool(object()), "acquire_timeout_seconds": 5.0}}
            )

        assert result["status"] == "auto-approved"
        assert result["decision_reason"] == "amount matches receipt text"
        assert len(fakes.apply_policies_decision.calls) == 0
        assert fakes.apply_agent_decision_decision.calls[0][1:] == (
            uuid,
            "auto-approved",
            "amount matches receipt text",
        )
        lines = _flow_lines(caplog)
        assert any("apply_policies" in line for line in lines)
        assert any("analysis" in line for line in lines)
        assert any("apply_agent_decision" in line for line in lines)

    async def it_routes_the_ambiguous_zone_to_human_review_on_a_contradictory_guardrail_verdict(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()
        fakes = _Fakes(
            extracted=ExtractedFieldsSchema(value=1000, currency="BRL", receipts_date=date(2026, 4, 1)),
            guardrail=GuardrailVerdict(
                consistent=False, reason="claimed amount contradicts the OCR total"
            ),
            apply_policies_result=uuid,
            apply_agent_decision_result=uuid,
        )
        graph = fakes.wire()

        with caplog.at_level(logging.INFO):
            result = await graph.ainvoke(
                _initial_state(uuid), config={"configurable": {"pool": FakeAcquirePool(object()), "acquire_timeout_seconds": 5.0}}
            )

        assert result["status"] == "human-review"
        assert result["decision_reason"] == "claimed amount contradicts the OCR total"
        assert len(fakes.apply_policies_decision.calls) == 0
        assert fakes.apply_agent_decision_decision.calls[0][1:] == (
            uuid,
            "human-review",
            "claimed amount contradicts the OCR total",
        )
        lines = _flow_lines(caplog)
        assert any("analysis" in line for line in lines)
        assert any("apply_agent_decision" in line for line in lines)

    async def it_routes_a_missing_field_straight_to_human_review_via_apply_agent_decision(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        uuid = uuid4()
        fakes = _Fakes(
            extracted=ExtractedFieldsSchema(value=None, currency=None, receipts_date=None),
            guardrail=GuardrailVerdict(consistent=True, reason="unused"),
            apply_policies_result=uuid,
            apply_agent_decision_result=uuid,
        )
        graph = fakes.wire()

        with caplog.at_level(logging.INFO):
            result = await graph.ainvoke(
                _initial_state(uuid), config={"configurable": {"pool": FakeAcquirePool(object()), "acquire_timeout_seconds": 5.0}}
            )

        assert result["status"] == "human-review"
        assert result["decision_reason"] is not None
        assert len(fakes.apply_policies_decision.calls) == 0
        assert len(fakes.analysis_model.calls) == 0
        assert fakes.apply_agent_decision_decision.calls[0][1:] == (
            uuid,
            "human-review",
            result["decision_reason"],
        )
        lines = _flow_lines(caplog)
        assert any("extract_fields" in line for line in lines)
        assert any("validate" in line for line in lines)
        assert not any("apply_policies" in line for line in lines)
        assert not any("analysis" in line for line in lines)
        assert any("apply_agent_decision" in line for line in lines)


class DescribeBuildGraph:
    def it_wires_the_real_dependencies_into_the_expected_node_set(self) -> None:
        # No monkeypatching: proves the real body (init_chat_model calls,
        # config.ai/config.models plumbing, schema binding) constructs
        # without error and without a live network call — only asserts the
        # node-key set; it_constructs_two_independent_groq_models_one_per_node
        # (above) is the one that inspects the constructed models themselves.
        # Construction alone never calls .ainvoke() — confirmed empirically
        # against langchain-groq (design.md's Risks table flagged this as
        # unconfirmed pre-implementation); every other test substitutes
        # build_graph or _wire's node set entirely.
        graph = agent.build_graph()

        assert set(graph.get_graph().nodes.keys()) == {
            "__start__",
            "__end__",
            "extract_fields",
            "validate",
            "apply_policies",
            "analysis",
            "apply_agent_decision",
        }

    def it_constructs_two_independent_groq_models_one_per_node(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "gsk_shared_key")
        monkeypatch.setenv("EXTRACT_FIELDS_MODEL_NAME", "model-a")
        monkeypatch.setenv("EXTRACT_FIELDS_TEMPERATURE", "0.1")
        monkeypatch.setenv("ANALYSIS_MODEL_NAME", "model-b")
        monkeypatch.setenv("ANALYSIS_TEMPERATURE", "0.9")
        monkeypatch.setenv("AI_TIMEOUT_SECONDS", "12")

        calls: list[dict] = []
        instances: list["_StubChatModel"] = []

        class _StubChatModel:
            def with_structured_output(self, schema: object) -> "_StubChatModel":
                self.schema = schema
                return self

        def _spy_init_chat_model(model: str, **kwargs: object) -> _StubChatModel:
            calls.append({"model": model, **kwargs})
            instance = _StubChatModel()
            instances.append(instance)
            return instance

        monkeypatch.setattr(agent, "init_chat_model", _spy_init_chat_model)

        agent.build_graph()

        assert len(calls) == 2
        extract_call, analysis_call = calls
        extract_instance, analysis_instance = instances
        assert extract_call["model"] == "groq:model-a"
        assert extract_call["temperature"] == 0.1
        assert analysis_call["model"] == "groq:model-b"
        assert analysis_call["temperature"] == 0.9
        # AMC-13/14: both nodes' models are sourced from the one AIConfig
        # read, not a per-node credential/timeout.
        assert extract_call["api_key"] == analysis_call["api_key"] == "gsk_shared_key"
        assert extract_call["timeout"] == analysis_call["timeout"] == 12.0
        # AMC-03: two independently-configured model_names really reach two
        # distinct constructed model instances, not one shared instance
        # whose value happens to be read twice.
        assert extract_call["model"] != analysis_call["model"]
        # Proves the two structured-output schemas are bound to the right
        # node, not swapped — a schema swap between extract_fields/analysis
        # would silently pass every other assertion here.
        assert extract_instance.schema is ExtractedFieldsSchema
        assert analysis_instance.schema is GuardrailVerdict


class DescribeDecide:
    async def it_threads_the_langfuse_callback_handlers_into_graph_ainvoke(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict] = []

        class _FakeGraph:
            async def ainvoke(self, state: dict, config: dict) -> dict:
                calls.append(config)
                return {"status": "auto-approved"}

        monkeypatch.setattr(agent, "get_graph", lambda: _FakeGraph())
        expected_handlers = agent._langfuse_handlers()
        pool = FakeAcquirePool(object())

        result = await agent.decide(
            Reimbursement(uuid=uuid4(), original_payload={}), pool, acquire_timeout_seconds=5.0
        )

        assert result == {"status": "auto-approved"}
        assert len(calls) == 1
        assert calls[0]["callbacks"] == expected_handlers
        assert calls[0]["configurable"] == {"pool": pool, "acquire_timeout_seconds": 5.0}
