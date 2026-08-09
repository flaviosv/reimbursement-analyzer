import logging
from datetime import UTC, datetime

from confluent_kafka.aio import AIOProducer
from shared.config import REQUEST_TOPIC, KafkaConfig
from shared.errors import PublishFailed
from shared.producer import publish as publish_to_kafka

logger = logging.getLogger(__name__)


def build_envelope(raw: bytes, published_at: datetime) -> bytes:
    """Splice the envelope around the raw, already-validated body bytes
    rather than re-serialising a parsed model. `raw` was already proven to
    be valid JSON by validate_batch, so splicing it into a JSON object is
    safe — this is what makes the payload byte-for-byte identical to the
    request body true by construction."""
    stamp = published_at.astimezone(UTC).isoformat().encode()
    prefix = b'{"retry":0,"published_at":"' + stamp + b'","errors":[],"payload":'
    return prefix + raw + b"}"


async def publish(producer: AIOProducer, raw: bytes, request_ids: list[str], config: KafkaConfig) -> None:
    """Build the envelope and hand it to shared.producer.publish, the
    technology-agnostic publisher. request_ids are never part of the
    published payload — they exist only so a delivery failure can be logged
    against the business IDs it affects."""
    envelope = build_envelope(raw, datetime.now(UTC))
    try:
        await publish_to_kafka(producer, REQUEST_TOPIC, envelope, config.publish_timeout_seconds)
    except PublishFailed as exc:
        # Body is never logged — may be up to MAX_BODY_BYTES and may carry PII.
        logger.error("failed to publish requests %s: %s", request_ids, exc)
        raise
