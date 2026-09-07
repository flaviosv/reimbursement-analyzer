from publisher.metrics import (
    publisher_duplicate_dropped_total,
    publisher_messages_consumed_total,
    publisher_messages_requeued_total,
)


class DescribePublisherMessagesConsumedTotal:
    def it_is_constructed_with_a_topic_labelname(self) -> None:
        assert publisher_messages_consumed_total._labelnames == ("topic",)

    def it_increments_on_inc(self) -> None:
        before = publisher_messages_consumed_total.labels("Request")._value.get()

        publisher_messages_consumed_total.labels("Request").inc()

        after = publisher_messages_consumed_total.labels("Request")._value.get()
        assert after == before + 1


class DescribePublisherMessagesRequeuedTotal:
    def it_is_constructed_with_a_topic_labelname(self) -> None:
        assert publisher_messages_requeued_total._labelnames == ("topic",)

    def it_increments_on_inc(self) -> None:
        before = publisher_messages_requeued_total.labels("Request")._value.get()

        publisher_messages_requeued_total.labels("Request").inc()

        after = publisher_messages_requeued_total.labels("Request")._value.get()
        assert after == before + 1


class DescribePublisherDuplicateDroppedTotal:
    def it_has_no_labelnames(self) -> None:
        assert publisher_duplicate_dropped_total._labelnames == ()

    def it_is_callable_without_labels_and_increments_on_inc(self) -> None:
        before = publisher_duplicate_dropped_total._value.get()

        publisher_duplicate_dropped_total.inc()

        after = publisher_duplicate_dropped_total._value.get()
        assert after == before + 1
