"""The publisher's decision tree — the only module carrying business branching.

Kept apart from consumer.py so every branch below unit-tests with no Kafka
consumer at all.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import asyncpg
from confluent_kafka.aio import AIOProducer
from pydantic import ValidationError
from shared import failure_log
from shared.config import MAX_RETRY, REIMBURSEMENT_TOPIC, REQUEST_TOPIC, Config
from shared.errors import PublishFailed, sanitize
from shared.models import (
    AttemptError,
    ReimbursementEnvelope,
    ReimbursementRequest,
    RequestEnvelope,
    Stage,
)
from shared.producer import publish
from shared.reimbursement import repository
from shared.reimbursement.use_cases.send_human_review import send_human_review

logger = logging.getLogger(__name__)

DUPLICATE_DROPPED_EVENT = "reimbursement.duplicate_dropped"
EMPTY_PAYLOAD_EVENT = "reimbursement.empty_payload"
ESCALATION_FAILED_EVENT = "reimbursement.escalation_failed"
INVALID_ITEM_EVENT = "reimbursement.invalid_item"
ITEM_FAILED_EVENT = "reimbursement.item_failed"
MALFORMED_MESSAGE_EVENT = "reimbursement.malformed_message"


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
    """Turn one consumed `Request` message into one outcome per item. Never
    raises, so one bad message can never stop the loop behind it (PUB-32)."""
    try:
        envelope = RequestEnvelope.model_validate_json(raw)
    except ValidationError as exc:
        logger.error("message could not be parsed as a RequestEnvelope: %s", sanitize(exc))
        failure_log.write(
            deps.config.failure_log,
            {
                "event": MALFORMED_MESSAGE_EVENT,
                "outcome": ItemOutcome.LOGGED.value,
                "message": raw.decode("utf-8", "replace"),
                "error": str(exc),
            },
        )
        return [ItemOutcome.LOGGED]

    if not envelope.payload:
        # A bug or a hand-crafted message — the POST endpoint already rejects
        # [] at ingress — so it fails safe rather than crashing the loop.
        logger.info(json.dumps({"event": EMPTY_PAYLOAD_EVENT, "retry": envelope.retry}))
        return []

    # Once, before fan-out: retry is envelope-level, and past the ceiling
    # SCOPE.md:218 stops every other action for every item in the message.
    handler = escalate_item if envelope.retry > MAX_RETRY else process_item
    return await _fan_out(deps, envelope, handler)


async def _fan_out(
    deps: Dependencies,
    envelope: RequestEnvelope,
    handler: Callable[[Dependencies, RequestEnvelope, int, dict[str, Any]], Awaitable[ItemOutcome]],
) -> list[ItemOutcome]:
    semaphore = asyncio.Semaphore(deps.config.publisher.item_concurrency)

    async def guarded(index: int, item: dict[str, Any]) -> ItemOutcome:
        async with semaphore:
            return await handler(deps, envelope, index, item)

    # No return_exceptions: the handlers never raise, so anything escaping
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
    if not _accepts(deps, envelope, index, item):
        return ItemOutcome.INVALID
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


async def escalate_item(
    deps: Dependencies, envelope: RequestEnvelope, index: int, item: dict[str, Any]
) -> ItemOutcome:
    """Preserve the item for a human, explained. Never publishes and never
    requeues: past the ceiling there is nothing left to retry. Never raises."""
    if not _accepts(deps, envelope, index, item):
        return ItemOutcome.INVALID
    try:
        async with deps.pool.acquire() as conn:
            async with conn.transaction():
                await send_human_review(conn, item, envelope.errors)
    except Exception as exc:
        if repository.is_duplicate(exc):
            _log_duplicate(envelope, item, exc)
            return ItemOutcome.DUPLICATE
        logger.error("item %d could not be escalated: %s", index, sanitize(exc))
        failure_log.write(
            deps.config.failure_log,
            failure_record(
                ESCALATION_FAILED_EVENT,
                index,
                item,
                envelope.errors,
                outcome=ItemOutcome.LOGGED.value,
                error=str(exc),
            ),
        )
        return ItemOutcome.LOGGED
    return ItemOutcome.ESCALATED


def _accepts(
    deps: Dependencies, envelope: RequestEnvelope, index: int, item: dict[str, Any]
) -> bool:
    """Whether the item is a `ReimbursementRequest` at all.

    A rejected item is never retried and never escalated: `request_id` is NOT
    NULL, so a human-review row is impossible without it, and no number of
    retries fixes bad data (PUB-30)."""
    try:
        ReimbursementRequest.model_validate(item)
    except ValidationError as exc:
        logger.error("item %d is not a valid request: %s", index, sanitize(exc))
        failure_log.write(
            deps.config.failure_log,
            failure_record(
                INVALID_ITEM_EVENT,
                index,
                item,
                envelope.errors,
                outcome=ItemOutcome.INVALID.value,
                error=str(exc),
            ),
        )
        return False
    return True


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
