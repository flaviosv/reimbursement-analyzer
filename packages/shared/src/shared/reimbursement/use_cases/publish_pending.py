"""Insert a pending request and publish its Reimbursement message, as one unit of work."""

from datetime import UTC, datetime
from typing import Any

import asyncpg
from confluent_kafka.aio import AIOProducer

from shared.config import REIMBURSEMENT_TOPIC
from shared.models import AttemptError, ReimbursementEnvelope
from shared.producer import publish
from shared.reimbursement.repository import insert_pending


async def publish_pending(
    conn: asyncpg.Connection,
    producer: AIOProducer,
    item: dict[str, Any],
    errors: list[AttemptError],
    publish_timeout_seconds: float,
) -> None:
    """Insert `item` and publish its `Reimbursement` message.

    The insert-and-publish counterpart to `send_human_review` — both of the
    domain's item-level actions live at this layer, not half in `shared` and
    half inline in the publisher (AD-025). Runs inside the caller's own
    transaction: a publish failure must roll the insert back, which only
    works if this function does not open one of its own.
    """
    uuid = await insert_pending(conn, item)
    message = ReimbursementEnvelope(
        uuid=uuid, retry=0, published_at=datetime.now(UTC), errors=errors
    )
    await publish(
        producer, REIMBURSEMENT_TOPIC, message.model_dump_json().encode(), publish_timeout_seconds
    )
