"""Every metric object owned exclusively by `publisher`.

Every metric object here is a module-level constant, constructed once at
import time — never per-request/per-call — so re-importing this module
never raises `prometheus_client`'s duplicate-registration error."""

from prometheus_client import Counter

# Constructed without the `_total` suffix — prometheus_client appends it at
# exposition time (spec.md's Counter naming rule).
publisher_messages_consumed_total = Counter(
    "publisher_messages_consumed",
    "Kafka messages consumed by publisher",
    labelnames=["topic"],
)

publisher_messages_requeued_total = Counter(
    "publisher_messages_requeued",
    "Messages requeued by publisher after a transient processing failure",
    labelnames=["topic"],
)

publisher_duplicate_dropped_total = Counter(
    "publisher_duplicate_dropped",
    "Duplicate messages dropped by publisher's idempotency handling",
)
