"""The publisher's decision tree — the only module carrying business branching.

Kept apart from consumer.py so every branch below unit-tests with no Kafka
consumer at all.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import asyncpg
from confluent_kafka.aio import AIOProducer
from shared import failure_log
from shared.config import REIMBURSEMENT_TOPIC, REQUEST_TOPIC, Config
from shared.errors import PublishFailed, sanitize
from shared.models import AttemptError, ReimbursementEnvelope, RequestEnvelope, Stage
from shared.producer import publish
from shared.reimbursement import repository

logger = logging.getLogger(__name__)

DUPLICATE_DROPPED_EVENT = "reimbursement.duplicate_dropped"
ITEM_FAILED_EVENT = "reimbursement.item_failed"


class ItemOutcome(Enum):
    """How one item settled. Every failure maps to a value here rather than
    to an exception, which is what keeps one item's fate independent of
    another's under asyncio.gather — and gives a monitor something countable
    while R-004's real metric is outstanding."""

    PUBLISHED = "published"
    DUPLICATE = "duplicate"
    REQUEUED = "requeued"
    ESCALATED = "escalated"
    LOGGED = "logged"
    INVALID = "invalid"


@dataclass(frozen=True)
class Dependencies:
    config: Config
    pool: asyncpg.Pool
    producer: AIOProducer


async def handle_message(deps: Dependencies, raw: bytes) -> list[ItemOutcome]:
    """Turn one consumed `Request` message into one outcome per item."""
    envelope = RequestEnvelope.model_validate_json(raw)
    return await _fan_out(deps, envelope)


async def _fan_out(deps: Dependencies, envelope: RequestEnvelope) -> list[ItemOutcome]:
    semaphore = asyncio.Semaphore(deps.config.publisher.item_concurrency)

    async def guarded(index: int, item: dict[str, Any]) -> ItemOutcome:
        async with semaphore:
            return await process_item(deps, envelope, index, item)

    # No return_exceptions: process_item never raises, so anything escaping
    # here is a real defect and should be loud rather than absorbed.
    return list(
        await asyncio.gather(
            *(guarded(index, item) for index, item in enumerate(envelope.payload))
        )
    )


async def process_item(
    deps: Dependencies, envelope: RequestEnvelope, index: int, item: dict[str, Any]
) -> ItemOutcome:
    """Insert the item and publish its `Reimbursement` message as one unit of
    work. Never raises — every failure becomes an ItemOutcome."""
    try:
        await _insert_and_publish(deps, envelope, item)
    except PublishFailed as exc:
        return await _requeue(deps, envelope, index, item, "publish", exc)
    except Exception as exc:
        if repository.is_duplicate(exc):
            _log_duplicate(envelope, item, exc)
            return ItemOutcome.DUPLICATE
        return await _requeue(deps, envelope, index, item, "db-insert", exc)
    return ItemOutcome.PUBLISHED


async def _insert_and_publish(
    deps: Dependencies, envelope: RequestEnvelope, item: dict[str, Any]
) -> None:
    async with deps.pool.acquire() as conn:
        # The publish sits *inside* the transaction, so a delivery failure
        # rolls the insert back without an explicit rollback call (PUB-09).
        async with conn.transaction():
            uuid = await repository.insert_pending(conn, item)
            message = ReimbursementEnvelope(uuid=uuid, retry=0, published_at=datetime.now(UTC))
            await publish(
                deps.producer,
                REIMBURSEMENT_TOPIC,
                message.model_dump_json().encode(),
                deps.config.kafka.publish_timeout_seconds,
            )


async def _requeue(
    deps: Dependencies,
    envelope: RequestEnvelope,
    index: int,
    item: dict[str, Any],
    stage: Stage,
    exc: Exception,
) -> ItemOutcome:
    logger.error("item %d failed at stage %s: %s", index, stage, sanitize(exc))
    errors = [*envelope.errors, AttemptError.next(envelope.errors, stage, exc)]
    retried = RequestEnvelope(
        retry=envelope.retry + 1,
        published_at=datetime.now(UTC),
        errors=errors,
        payload=[item],
    )
    try:
        await publish(
            deps.producer,
            REQUEST_TOPIC,
            retried.model_dump_json().encode(),
            deps.config.kafka.publish_timeout_seconds,
        )
    except PublishFailed as requeue_exc:
        failure_log.write(
            deps.config.failure_log,
            failure_record(
                ITEM_FAILED_EVENT,
                index,
                item,
                errors,
                stage=stage,
                outcome=ItemOutcome.LOGGED.value,
                requeue_error=str(requeue_exc),
            ),
        )
        return ItemOutcome.LOGGED
    return ItemOutcome.REQUEUED


def _log_duplicate(envelope: RequestEnvelope, item: dict[str, Any], exc: BaseException) -> None:
    """Emit the drop as a countable structured event: a system discarding
    thousands of requests must not look identical to one discarding none
    (R-004's interim mitigation)."""
    logger.info(
        json.dumps(
            {
                "event": DUPLICATE_DROPPED_EVENT,
                "request_id": _request_id(item),
                "constraint": getattr(exc, "constraint_name", None),
                "retry": envelope.retry,
            }
        )
    )


def failure_record(
    event: str,
    index: int,
    item: Any,
    errors: list[AttemptError],
    **extra: Any,
) -> dict[str, Any]:
    """The shape every last-resort record shares. Unlike the stdout logs
    above it carries the item verbatim — the failure log exists precisely so
    an unhandleable item is not lost, and it sits inside the payload's own
    trust boundary."""
    return {
        "event": event,
        "item_index": index,
        "request_id": _request_id(item),
        "item": item,
        "errors": [error.model_dump(mode="json") for error in errors],
        **extra,
    }


def _request_id(item: Any) -> str | None:
    return item.get("request_id") if isinstance(item, dict) else None
