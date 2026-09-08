from publisher.metrics import (
    publisher_duplicate_dropped_total,
    publisher_messages_consumed_total,
    publisher_messages_requeued_total,
)
from shared.testing import metric_value


class DescribePublisherMessagesConsumedTotal:
    def it_is_constructed_with_a_topic_labelname(self) -> None:
        assert publisher_messages_consumed_total._labelnames == ("topic",)

    def it_increments_on_inc(self) -> None:
        before = metric_value(publisher_messages_consumed_total, "Request")

        publisher_messages_consumed_total.labels("Request").inc()

        after = metric_value(publisher_messages_consumed_total, "Request")
        assert after == before + 1


class DescribePublisherMessagesRequeuedTotal:
    def it_is_constructed_with_a_topic_labelname(self) -> None:
        assert publisher_messages_requeued_total._labelnames == ("topic",)

    def it_increments_on_inc(self) -> None:
        before = metric_value(publisher_messages_requeued_total, "Request")

        publisher_messages_requeued_total.labels("Request").inc()

        after = metric_value(publisher_messages_requeued_total, "Request")
        assert after == before + 1


class DescribePublisherDuplicateDroppedTotal:
    def it_has_no_labelnames(self) -> None:
        assert publisher_duplicate_dropped_total._labelnames == ()

    def it_is_callable_without_labels_and_increments_on_inc(self) -> None:
        before = metric_value(publisher_duplicate_dropped_total)

        publisher_duplicate_dropped_total.inc()

        after = metric_value(publisher_duplicate_dropped_total)
        assert after == before + 1
