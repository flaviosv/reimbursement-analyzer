import asyncio
import logging
from datetime import UTC, datetime

from confluent_kafka.aio import AIOProducer

from api.errors import PublishFailed

logger = logging.getLogger(__name__)

# Slice-local: nothing outside reimbursement/create reads either constant
# (design slice rule 4).
REQUEST_TOPIC = "Request"
PUBLISH_TIMEOUT_SECONDS = 10


def build_envelope(raw: bytes, published_at: datetime) -> bytes:
    """Splice the envelope around the raw, already-validated body bytes
    rather than re-serialising a parsed model. `raw` was already proven to
    be valid JSON by validate_batch, so splicing it into a JSON object is
    safe — this is what makes the payload byte-for-byte identical to the
    request body true by construction (RCV-04)."""
    stamp = published_at.astimezone(UTC).isoformat().encode()
    prefix = b'{"retry":0,"published_at":"' + stamp + b'","payload":'
    return prefix + raw + b"}"


async def publish(producer: AIOProducer, raw: bytes, request_ids: list[str]) -> None:
    """Build the envelope, produce to REQUEST_TOPIC, and await the
    per-message delivery Future. Raises PublishFailed on a broker error or
    on timeout — never a bare exception, so the route's error handling stays
    uniform."""
    envelope = build_envelope(raw, datetime.now(UTC))
    try:
        delivery_future = await producer.produce(topic=REQUEST_TOPIC, value=envelope)
        await asyncio.wait_for(delivery_future, timeout=PUBLISH_TIMEOUT_SECONDS)
    except Exception as exc:
        # The body is never logged — it may be 25 MB and may carry PII
        # (RCV-17). request_ids come from the already-validated batch.
        logger.error("failed to publish requests %s: %s", request_ids, exc)
        raise PublishFailed(str(exc)) from exc
