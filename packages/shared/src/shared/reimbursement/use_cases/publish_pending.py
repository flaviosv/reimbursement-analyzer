"""Insert a pending request and publish its Reimbursement message; on a
publish failure, compensate with a delete and a durable trace."""

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
from confluent_kafka.aio import AIOProducer

from shared import failure_log
from shared.config import REIMBURSEMENT_TOPIC, FailureLogConfig
from shared.errors import PublishFailed
from shared.models import AttemptError, ReimbursementEnvelope
from shared.producer import publish
from shared.reimbursement.repository import delete_pending, insert_pending

logger = logging.getLogger(__name__)

COMPENSATING_DELETE_EVENT = "reimbursement.compensating_delete"
COMPENSATING_DELETE_NOOP_EVENT = "reimbursement.compensating_delete_noop"
COMPENSATING_DELETE_FAILED_EVENT = "reimbursement.compensating_delete_failed"


async def publish_pending(
    conn: asyncpg.Connection,
    producer: AIOProducer,
    item: dict[str, Any],
    errors: list[AttemptError],
    publish_timeout_seconds: float,
    failure_log_config: FailureLogConfig,
    retry: int,
) -> None:
    """Insert `item`, let it commit, then publish its `Reimbursement` message.

    No transaction spans the publish (AD-033, amending AD-017 for this unit
    of work only): `insert_pending` commits on its own, and a publish
    failure is compensated by an explicit delete rather than a rollback.
    Every delete outcome (removed, no-op, or itself failed) is durably
    logged before this re-raises `PublishFailed` for the caller's existing
    requeue path (`publisher.processing._requeue`) — unchanged by this.
    """
    uuid = await insert_pending(conn, item)
    message = ReimbursementEnvelope(
        uuid=uuid, retry=0, published_at=datetime.now(UTC), errors=errors
    )
    try:
        await publish(
            producer, REIMBURSEMENT_TOPIC, message.model_dump_json().encode(), publish_timeout_seconds
        )
    except PublishFailed as exc:
        await _compensate(conn, uuid, item, retry, exc, failure_log_config)
        raise


async def _compensate(
    conn: asyncpg.Connection,
    uuid: UUID,
    item: dict[str, Any],
    retry: int,
    publish_exc: PublishFailed,
    failure_log_config: FailureLogConfig,
) -> None:
    """Never raises: a failure here must not shadow the `PublishFailed` its
    caller is about to re-raise, or block the existing requeue path."""
    request_id = item.get("request_id")
    try:
        deleted = await delete_pending(conn, uuid)
    except Exception as delete_exc:
        failure_log.write(
            failure_log_config,
            {
                "event": COMPENSATING_DELETE_FAILED_EVENT,
                "uuid": str(uuid),
                "request_id": request_id,
                "item": item,
                "retry": retry,
                "publish_error_type": type(publish_exc).__name__,
                "publish_error": str(publish_exc),
                "delete_error_type": type(delete_exc).__name__,
                "delete_error": str(delete_exc),
            },
        )
        return

    event = COMPENSATING_DELETE_EVENT if deleted else COMPENSATING_DELETE_NOOP_EVENT
    logger.info(
        json.dumps(
            {
                "event": event,
                "uuid": str(uuid),
                "request_id": request_id,
                "retry": retry,
                "publish_error_type": type(publish_exc).__name__,
            }
        )
    )
