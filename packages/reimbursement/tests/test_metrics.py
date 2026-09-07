from reimbursement.metrics import (
    reimbursement_agent_decision_duration_seconds,
    reimbursement_agent_llm_calls_total,
    reimbursement_agent_node_duration_seconds,
    reimbursement_decision_failure_escalations_total,
    reimbursement_messages_consumed_total,
    reimbursement_messages_requeued_total,
    reimbursement_policy_rule_triggered_total,
    reimbursement_time_to_decision_seconds,
)
from shared.testing import histogram_sample_count, metric_value


class DescribeReimbursementAgentDecisionDurationSeconds:
    def it_has_no_labelnames(self) -> None:
        assert reimbursement_agent_decision_duration_seconds._labelnames == ()

    def it_has_sub_second_to_two_minute_buckets(self) -> None:
        assert reimbursement_agent_decision_duration_seconds._upper_bounds == [
            0.1, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, float("inf"),
        ]

    def it_can_observe_a_positive_float(self) -> None:
        before = histogram_sample_count(reimbursement_agent_decision_duration_seconds)

        reimbursement_agent_decision_duration_seconds.observe(1.5)

        after = histogram_sample_count(reimbursement_agent_decision_duration_seconds)
        assert after == before + 1


class DescribeReimbursementTimeToDecisionSeconds:
    def it_has_no_labelnames(self) -> None:
        assert reimbursement_time_to_decision_seconds._labelnames == ()

    def it_has_one_second_to_six_hour_buckets(self) -> None:
        assert reimbursement_time_to_decision_seconds._upper_bounds == [
            1, 5, 15, 30, 60, 300, 900, 1800, 3600, 21600, float("inf"),
        ]

    def it_uses_a_distinct_bucket_configuration_from_agent_decision_duration_seconds(self) -> None:
        # AC2's literal text: "sharing no bucket configuration with AC1's
        # histogram" — a distinct bucket *list*, not necessarily zero shared
        # individual boundary values (both are real, valid latency numbers).
        assert (
            reimbursement_time_to_decision_seconds._upper_bounds
            != reimbursement_agent_decision_duration_seconds._upper_bounds
        )

    def it_can_observe_a_positive_float(self) -> None:
        before = histogram_sample_count(reimbursement_time_to_decision_seconds)

        reimbursement_time_to_decision_seconds.observe(42.0)

        after = histogram_sample_count(reimbursement_time_to_decision_seconds)
        assert after == before + 1


class DescribeReimbursementAgentNodeDurationSeconds:
    def it_is_constructed_with_node_and_model_labelnames(self) -> None:
        assert reimbursement_agent_node_duration_seconds._labelnames == ("node", "model")

    def it_has_finer_grained_buckets_than_the_full_graph_histogram(self) -> None:
        assert reimbursement_agent_node_duration_seconds._upper_bounds == [
            0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, float("inf"),
        ]

    def it_can_observe_with_an_empty_model_label_for_non_llm_nodes(self) -> None:
        child = reimbursement_agent_node_duration_seconds.labels("validate", "")
        before = histogram_sample_count(child)

        child.observe(0.2)

        after = histogram_sample_count(child)
        assert after == before + 1

    def it_can_observe_with_a_populated_model_label_for_llm_nodes(self) -> None:
        child = reimbursement_agent_node_duration_seconds.labels("extract_fields", "llama-3.3-70b")
        before = histogram_sample_count(child)

        child.observe(0.8)

        after = histogram_sample_count(child)
        assert after == before + 1


class DescribeReimbursementAgentLlmCallsTotal:
    def it_is_constructed_with_model_and_outcome_labelnames(self) -> None:
        assert reimbursement_agent_llm_calls_total._labelnames == ("model", "outcome")

    def it_increments_with_labels(self) -> None:
        before = metric_value(reimbursement_agent_llm_calls_total, "model-a", "success")

        reimbursement_agent_llm_calls_total.labels("model-a", "success").inc()

        after = metric_value(reimbursement_agent_llm_calls_total, "model-a", "success")
        assert after == before + 1


class DescribeReimbursementPolicyRuleTriggeredTotal:
    def it_is_constructed_with_a_rule_labelname(self) -> None:
        assert reimbursement_policy_rule_triggered_total._labelnames == ("rule",)

    def it_increments_with_a_label(self) -> None:
        before = metric_value(reimbursement_policy_rule_triggered_total, "stale-receipt-reject")

        reimbursement_policy_rule_triggered_total.labels("stale-receipt-reject").inc()

        after = metric_value(reimbursement_policy_rule_triggered_total, "stale-receipt-reject")
        assert after == before + 1


class DescribeReimbursementDecisionFailureEscalationsTotal:
    def it_has_no_labelnames(self) -> None:
        assert reimbursement_decision_failure_escalations_total._labelnames == ()

    def it_increments_without_labels(self) -> None:
        before = metric_value(reimbursement_decision_failure_escalations_total)

        reimbursement_decision_failure_escalations_total.inc()

        after = metric_value(reimbursement_decision_failure_escalations_total)
        assert after == before + 1


class DescribeReimbursementMessagesConsumedTotal:
    def it_is_constructed_with_a_topic_labelname(self) -> None:
        assert reimbursement_messages_consumed_total._labelnames == ("topic",)

    def it_increments_with_a_label(self) -> None:
        before = metric_value(reimbursement_messages_consumed_total, "Reimbursement")

        reimbursement_messages_consumed_total.labels("Reimbursement").inc()

        after = metric_value(reimbursement_messages_consumed_total, "Reimbursement")
        assert after == before + 1


class DescribeReimbursementMessagesRequeuedTotal:
    def it_is_constructed_with_a_topic_labelname(self) -> None:
        assert reimbursement_messages_requeued_total._labelnames == ("topic",)

    def it_increments_with_a_label(self) -> None:
        before = metric_value(reimbursement_messages_requeued_total, "Reimbursement")

        reimbursement_messages_requeued_total.labels("Reimbursement").inc()

        after = metric_value(reimbursement_messages_requeued_total, "Reimbursement")
        assert after == before + 1
