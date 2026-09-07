"""Every metric object owned exclusively by `reimbursement`.

Every metric object here is a module-level constant, constructed once at
import time — never per-request/per-call — so re-importing this module
never raises `prometheus_client`'s duplicate-registration error."""

from prometheus_client import Counter, Histogram

# Sub-second to 2 minutes — bounded above by AIConfig.timeout_seconds's 30s
# default times up to 2 sequential LLM calls. Deliberately shares no bucket
# list with reimbursement_time_to_decision_seconds (spec.md AC2's literal
# text) or reimbursement_agent_node_duration_seconds (finer-grained).
reimbursement_agent_decision_duration_seconds = Histogram(
    "reimbursement_agent_decision_duration_seconds",
    "Wall-clock time of the full agent.decide() graph run",
    buckets=(0.1, 0.5, 1, 2, 5, 10, 20, 30, 60, 120),
)

# 1 second to 6 hours — includes Kafka queue wait, deliberately not sharing
# agent_decision_duration_seconds's bucket list.
reimbursement_time_to_decision_seconds = Histogram(
    "reimbursement_time_to_decision_seconds",
    "End-to-end time from request creation to a terminal automated decision, including Kafka queue wait",
    buckets=(1, 5, 15, 30, 60, 300, 900, 1800, 3600, 21600),
)

# Finer-grained than the whole-graph histogram — a single node is always
# faster than the full run.
reimbursement_agent_node_duration_seconds = Histogram(
    "reimbursement_agent_node_duration_seconds",
    "Per-node latency inside the decision graph",
    labelnames=["node", "model"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

# Constructed without the `_total` suffix — prometheus_client appends it at
# exposition time (spec.md's Counter naming rule).
reimbursement_agent_llm_calls_total = Counter(
    "reimbursement_agent_llm_calls",
    "LLM invocations, by model and success/failure",
    labelnames=["model", "outcome"],
)

reimbursement_policy_rule_triggered_total = Counter(
    "reimbursement_policy_rule_triggered",
    "Which deterministic policy rule fired",
    labelnames=["rule"],
)

reimbursement_decision_failure_escalations_total = Counter(
    "reimbursement_decision_failure_escalations",
    "Decisions force-escalated to human-review after the LLM/agent pipeline failed and retries were exhausted",
)

reimbursement_messages_consumed_total = Counter(
    "reimbursement_messages_consumed",
    "Kafka messages consumed by reimbursement",
    labelnames=["topic"],
)

reimbursement_messages_requeued_total = Counter(
    "reimbursement_messages_requeued",
    "Messages requeued by reimbursement after a transient processing failure",
    labelnames=["topic"],
)
