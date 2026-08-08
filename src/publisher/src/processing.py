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
from shared.config import MAX_RETRY, REQUEST_TOPIC, Config
from shared.errors import PublishFailed, sanitize
from shared.models import AttemptError, ReimbursementRequest, RequestEnvelope, Stage
from shared.producer import publish
from shared.reimbursement import repository
from shared.reimbursement.use_cases.publish_pending import publish_pending
from shared.reimbursement.use_cases.send_human_review import send_human_review

from config import PublisherConfig

logger = logging.getLogger(__name__)


class _LazyJSON:
    """Defers json.dumps until the logging module actually formats this
    record — never on a call whose level isn't enabled. `logger.info(json
    .dumps(...))` paid the serialization cost unconditionally even when
    INFO was disabled, on a path that fires once per item and can repeat
    many times over on redelivery."""

    __slots__ = ("_value",)

    def __init__(self, value: Any) -> None:
        self._value = value

    def __str__(self) -> str:
        return json.dumps(self._value)


DUPLICATE_DROPPED_EVENT = "reimbursement.duplicate_dropped"
EMPTY_PAYLOAD_EVENT = "reimbursement.empty_payload"
ESCALATION_FAILED_EVENT = "reimbursement.escalation_failed"
INVALID_ITEM_EVENT = "reimbursement.invalid_item"
ITEM_FAILED_EVENT = "reimbursement.item_failed"
MALFORMED_MESSAGE_EVENT = "reimbursement.malformed_message"
NULL_VALUE_EVENT = "reimbursement.null_value"
MESSAGE_HANDLED_EVENT = "reimbursement.message_handled"


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
    publisher: PublisherConfig
    pool: asyncpg.Pool
    producer: AIOProducer


async def handle_message(deps: Dependencies, raw: bytes | None) -> list[ItemOutcome]:
    """Turn one consumed `Request` message into one outcome per item. Never
    raises, so one bad message can never stop the loop behind it (PUB-32).

    `raw` is `None` for a tombstone/null-value record — a valid Kafka
    message with no crash-worthy content, so it is logged and dropped the
    same way a malformed one is, not treated as an exceptional condition."""
    if raw is None:
        logger.error("consumed a null-valued Request record — logged and skipped")
        failure_log.write(
            deps.config.failure_log,
            {
                "event": NULL_VALUE_EVENT,
                "outcome": ItemOutcome.LOGGED.value,
            },
        )
        return [ItemOutcome.LOGGED]

    try:
        envelope = RequestEnvelope.model_validate_json(raw)
    except ValidationError as exc:
        logger.error("message could not be parsed as a RequestEnvelope: %s", sanitize(exc))
        failure_log.write(
            deps.config.failure_log,
            _malformed_message_record(raw, exc),
        )
        return [ItemOutcome.LOGGED]

    if not envelope.payload:
        # A bug or a hand-crafted message — the POST endpoint already rejects
        # [] at ingress — so it fails safe rather than crashing the loop.
        logger.info("%s", _LazyJSON({"event": EMPTY_PAYLOAD_EVENT, "retry": envelope.retry}))
        return []

    # Once, before fan-out: retry is envelope-level, and past the ceiling
    # SCOPE.md:218 stops every other action for every item in the message.
    handler = escalate_item if envelope.retry > MAX_RETRY else process_item
    return await _fan_out(deps, envelope, handler)


def _failure_record(
    event: str, index: int, item: Any, errors: list[AttemptError], **extra: Any
) -> dict[str, Any]:
    """The shape every last-resort record shares."""
    return {
        "event": event,
        "item_index": index,
        "request_id": _request_id(item),
        "item": item,
        "errors": [error.model_dump(mode="json") for error in errors],
        **extra,
    }


def _malformed_message_record(raw: bytes, exc: ValidationError) -> dict[str, Any]:
    """One failure-log entry per recoverable item, not one blob.

    `failure_log.write` truncates every string to `max_message_chars`
    independently — so a flat `{"message": <entire raw envelope>}` record
    loses everything past the first cut for a ceiling-sized batch (R1). When
    the bytes are still valid JSON with a `payload` list, log each item as
    its own field instead: truncation then trims each item, not the batch.
    Falls back to the raw bytes when even that much structure is gone."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None

    items = parsed.get("payload") if isinstance(parsed, dict) else None
    if isinstance(items, list) and items:
        return {
            "event": MALFORMED_MESSAGE_EVENT,
            "outcome": ItemOutcome.LOGGED.value,
            "error": str(exc),
            "items": items,
        }
    return {
        "event": MALFORMED_MESSAGE_EVENT,
        "outcome": ItemOutcome.LOGGED.value,
        "error": str(exc),
        "message": raw.decode("utf-8", "replace"),
    }


async def _fan_out(
    deps: Dependencies,
    envelope: RequestEnvelope,
    handler: Callable[[Dependencies, RequestEnvelope, int, dict[str, Any]], Awaitable[ItemOutcome]],
) -> list[ItemOutcome]:
    semaphore = asyncio.Semaphore(deps.publisher.item_concurrency)

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
        # Also catches a COMMIT that fails *after* a successful publish (the
        # transaction's implicit commit runs when the `async with` block in
        # _insert_and_publish exits) — mislabeled "db-insert" below even
        # though the insert itself succeeded. Accepted as-is: a redelivery
        # then produces a second row with a different uuid than the one
        # already published, the same consequence a plain publish failure has.
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
        # This transaction looks redundant for a single INSERT — it isn't:
        # without it, a caught UniqueViolationError below poisons the
        # connection's enclosing transaction state (removing it broke
        # duplicate-collision handling under the shared-connection test
        # setup). It acts as a savepoint boundary, not an atomicity guard.
        async with deps.pool.acquire(timeout=deps.config.database.acquire_timeout_seconds) as conn:
            async with conn.transaction():
                await send_human_review(
                    conn, item, envelope.errors, deps.config.failure_log.max_message_chars
                )
    except Exception as exc:
        if repository.is_duplicate(exc):
            _log_duplicate(envelope, item, exc)
            return ItemOutcome.DUPLICATE
        logger.error(
            "item %d request_id=%s could not be escalated: %s",
            index,
            _request_id(item),
            sanitize(exc),
        )
        failure_log.write(
            deps.config.failure_log,
            _failure_record(
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
        logger.error(
            "item %d request_id=%s is not a valid request: %s",
            index,
            _request_id(item),
            sanitize(exc),
        )
        failure_log.write(
            deps.config.failure_log,
            _failure_record(
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
    async with deps.pool.acquire(timeout=deps.config.database.acquire_timeout_seconds) as conn:
        # The publish sits *inside* the transaction, so a delivery failure
        # rolls the insert back without an explicit rollback call (PUB-09).
        async with conn.transaction():
            await publish_pending(
                conn,
                deps.producer,
                item,
                envelope.errors,
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
    logger.error(
        "item %d request_id=%s failed at stage %s: %s",
        index,
        _request_id(item),
        stage,
        sanitize(exc),
    )
    errors = [*envelope.errors, AttemptError.from_exception(len(envelope.errors) + 1, stage, exc)]
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
            _failure_record(
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
        "%s",
        _LazyJSON(
            {
                "event": DUPLICATE_DROPPED_EVENT,
                "request_id": _request_id(item),
                "constraint": getattr(exc, "constraint_name", None),
                "retry": envelope.retry,
            }
        ),
    )


def _request_id(item: Any) -> str | None:
    return item.get("request_id") if isinstance(item, dict) else None
