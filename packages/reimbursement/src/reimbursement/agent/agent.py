"""Graph construction and the singleton entry point.

`build_graph()` is the only place any node's dependencies — most importantly
the two Groq client(s) — get constructed. `get_graph()` wraps it in the same
`@lru_cache(maxsize=1)` singleton shape as `shared.config.load_config`
(AD-023), so those dependencies are built once per process, not once per
message (L-003's "compiled once" proxy). `_wire` is split out from
`build_graph` so tests can wire the same graph shape with fakes at every
LLM/DB boundary, without constructing a real Groq client."""

import logging
from functools import lru_cache
from typing import Any

import asyncpg
from langchain.chat_models import init_chat_model
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from shared import failure_log
from shared.config import load_config
from shared.reimbursement.use_cases.apply_decision import apply_decision

from reimbursement.agent.nodes.analysis import Analysis, GuardrailVerdict
from reimbursement.agent.nodes.apply_agent_decision import ApplyAgentDecision
from reimbursement.agent.nodes.apply_policies import (
    ApplyPolicies,
    route_after_apply_policies,
)
from reimbursement.agent.nodes.extract_fields import (
    ExtractedFieldsSchema,
    ExtractFields,
)
from reimbursement.agent.nodes.validate import Validate, route_after_validate

from reimbursement.agent.types import Node
from reimbursement.config import load_agent_config
from reimbursement.models import Reimbursement
from reimbursement.schema import State

logger = logging.getLogger(__name__)


def _wire(nodes: dict[str, Node]) -> CompiledStateGraph:
    """The graph shape (Architecture Overview diagram), parameterized by
    already-constructed node instances — shared between `build_graph`
    (real dependencies) and the routing tests (fakes at every LLM/DB
    boundary)."""
    graph = StateGraph(State)
    graph.add_node("extract_fields", nodes["extract_fields"])
    graph.add_node("validate", nodes["validate"])
    graph.add_node("apply_policies", nodes["apply_policies"])
    graph.add_node("analysis", nodes["analysis"])
    graph.add_node("apply_agent_decision", nodes["apply_agent_decision"])

    graph.add_edge(START, "extract_fields")
    graph.add_edge("extract_fields", "validate")
    graph.add_conditional_edges(
        "validate",
        route_after_validate,
        {"apply_policies": "apply_policies", "apply_agent_decision": "apply_agent_decision"},
    )
    graph.add_conditional_edges(
        "apply_policies", route_after_apply_policies, {"analysis": "analysis", "__end__": END}
    )
    graph.add_edge("analysis", "apply_agent_decision")
    graph.add_edge("apply_agent_decision", END)

    return graph.compile()


def build_graph() -> CompiledStateGraph:
    config = load_agent_config()

    extract_model = init_chat_model(
        f"groq:{config.models.extract_fields.model_name}",
        api_key=config.ai.api_key,
        temperature=config.models.extract_fields.temperature,
        timeout=config.ai.timeout_seconds,
    ).with_structured_output(ExtractedFieldsSchema)
    analysis_model = init_chat_model(
        f"groq:{config.models.analysis.model_name}",
        api_key=config.ai.api_key,
        temperature=config.models.analysis.temperature,
        timeout=config.ai.timeout_seconds,
    ).with_structured_output(GuardrailVerdict)

    nodes: dict[str, Node] = {
        "extract_fields": ExtractFields(
            model=extract_model, model_name=config.models.extract_fields.model_name
        ),
        "validate": Validate(),
        "apply_policies": ApplyPolicies(apply_decision=apply_decision),
        "analysis": Analysis(model=analysis_model, model_name=config.models.analysis.model_name),
        "apply_agent_decision": ApplyAgentDecision(apply_decision=apply_decision),
    }
    return _wire(nodes)


@lru_cache(maxsize=1)
def get_graph() -> CompiledStateGraph:
    return build_graph()


LANGFUSE_FALLBACK_EVENT = "reimbursement.langfuse_fallback"


@lru_cache(maxsize=1)
def _langfuse_handlers() -> list[Any]:
    """AGD-23/24: every LLM invocation traced via LangFuse, durable
    `failure_log` fallback when LangFuse is unreachable. Memoized like
    `get_graph()` (AD-023) — `CallbackHandler()` wraps a long-lived client,
    not a per-request call, so it's built once per process rather than once
    per message; also means the "unavailable" fallback logs at most once per
    process instead of flooding `failure_log` on every message."""
    try:
        from langfuse.langchain import CallbackHandler
    except ImportError:
        logger.info("FLOW: langfuse not installed; LLM trace falls back to file logging")
        failure_log.write(
            load_config().failure_log,
            {"event": LANGFUSE_FALLBACK_EVENT, "reason": "langfuse not installed"},
        )
        return []

    try:
        return [CallbackHandler()]
    except Exception as exc:
        logger.exception("FLOW: langfuse handler unavailable; LLM trace falls back to file logging")
        failure_log.write(
            load_config().failure_log,
            {
                "event": LANGFUSE_FALLBACK_EVENT,
                "reason": "langfuse handler unavailable",
                "error": str(exc),
            },
        )
        return []


async def decide(
    reimbursement: Reimbursement, pool: asyncpg.Pool, *, acquire_timeout_seconds: float
) -> State:
    """Threads the pool itself into the graph, not a live connection: the
    two write nodes (ApplyPolicies/ApplyAgentDecision) each acquire their
    own connection only for the duration of their own write, so a pool
    connection isn't held checked-out for the LLM round-trips in between."""
    graph = get_graph()
    result = await graph.ainvoke(
        {"reimbursement": reimbursement},
        config={
            "configurable": {"pool": pool, "acquire_timeout_seconds": acquire_timeout_seconds},
            "callbacks": _langfuse_handlers(),
            "metadata": {"langfuse_session_id": str(reimbursement.uuid)},
        },
    )
    return result
