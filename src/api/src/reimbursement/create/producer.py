import asyncio
import logging
from datetime import UTC, datetime

from confluent_kafka.aio import AIOProducer
from shared.config import REQUEST_TOPIC
from shared.errors import PublishFailed

logger = logging.getLogger(__name__)

PUBLISH_TIMEOUT_SECONDS = 10


def build_envelope(raw: bytes, published_at: datetime) -> bytes:
    """Splice the envelope around the raw, already-validated body bytes
    rather than re-serialising a parsed model. `raw` was already proven to
    be valid JSON by validate_batch, so splicing it into a JSON object is
    safe — this is what makes the payload byte-for-byte identical to the
    request body true by construction."""
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
        # The body is never logged — it may be up to MAX_BODY_BYTES and may
        # carry PII. request_ids come from the already-validated batch.
        # Broad on purpose (broker errors, timeouts, and anything else all
        # collapse to one failure mode); the exception's own class name is
        # folded into the message so responses/logs still identify which
        # failure actually fired.
        logger.error("failed to publish requests %s: %s", request_ids, exc)
        raise PublishFailed(f"{type(exc).__name__}: {exc}") from exc
