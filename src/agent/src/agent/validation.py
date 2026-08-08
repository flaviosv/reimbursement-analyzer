"""The agent's decision tree — retry-ceiling short-circuit, resolve by
uuid, staleness compare, error branching.

Kept apart from consumer.py so every branch below unit-tests with no real
Kafka consumer, mirroring why publisher.processing sits apart from
publisher.consumer.
"""

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

import asyncpg
from confluent_kafka.aio import AIOProducer
from pydantic import ValidationError
from shared import failure_log
from shared.config import MAX_RETRY, Config
from shared.errors import sanitize
from shared.models import ReimbursementEnvelope
from shared.reimbursement.use_cases.send_human_review import escalate_existing

logger = logging.getLogger(__name__)

GHOST_DROPPED_EVENT = "reimbursement.ghost_dropped"
STALE_IGNORED_EVENT = "reimbursement.stale_ignored"
RESOLVED_EVENT = "reimbursement.resolved"
RESOLVE_FAILED_EVENT = "reimbursement.resolve_failed"
ESCALATED_EVENT = "reimbursement.escalated"
ESCALATION_FAILED_EVENT = "reimbursement.escalation_failed"
MALFORMED_MESSAGE_EVENT = "reimbursement.malformed_message"


class MessageOutcome(Enum):
    """How one message settled. Every branch below maps to a value here
    rather than to an exception, mirroring publisher.processing.ItemOutcome —
    the decision tree never raises."""

    RESOLVED = "resolved"
    STALE = "stale"
    GHOST = "ghost"
    REQUEUED = "requeued"
    ESCALATED = "escalated"
    LOGGED = "logged"
    INVALID = "invalid"


@dataclass(frozen=True)
class Dependencies:
    config: Config
    pool: asyncpg.Pool
    producer: AIOProducer


async def handle_message(deps: Dependencies, raw: bytes) -> MessageOutcome:
    """Turn one consumed `Reimbursement` message into one outcome. Never
    raises, so one bad message can never stop the loop behind it (AGT-20)."""
    try:
        envelope = ReimbursementEnvelope.model_validate_json(raw)
    except ValidationError as exc:
        logger.error("message could not be parsed as a ReimbursementEnvelope: %s", sanitize(exc))
        failure_log.write(
            deps.config.failure_log,
            {
                "event": MALFORMED_MESSAGE_EVENT,
                "outcome": MessageOutcome.INVALID.value,
                "message": raw.decode("utf-8", "replace"),
                "error": str(exc),
            },
        )
        return MessageOutcome.INVALID

    # Checked once, before the normal resolve flow: retry is message-level,
    # and past the ceiling there is nothing left to retry (SCOPE.md's Agent
    # Error Handling section, mirroring the publisher's own check-before-
    # processing ordering).
    if envelope.retry > MAX_RETRY:
        return await _escalate(deps, envelope)
    return await _resolve(deps, envelope)


async def _escalate(deps: Dependencies, envelope: ReimbursementEnvelope) -> MessageOutcome:
    """Preserve the row for a human, explained. Never publishes, never
    requeues: past the ceiling there is nothing left to retry. Never
    raises."""
    try:
        async with deps.pool.acquire() as conn:
            result = await escalate_existing(conn, envelope.uuid, envelope.errors)
    except Exception as exc:
        logger.error("uuid=%s could not be escalated: %s", envelope.uuid, sanitize(exc))
        failure_log.write(
            deps.config.failure_log,
            _failure_record(ESCALATION_FAILED_EVENT, envelope, error=str(exc)),
        )
        return MessageOutcome.LOGGED

    if result is None:
        # Ghost + retry>3 (AGT-18): the UPDATE affected zero rows — nothing
        # to escalate, but a durable record still needs to exist somewhere.
        failure_log.write(
            deps.config.failure_log,
            _failure_record(ESCALATION_FAILED_EVENT, envelope, reason="uuid has no matching row"),
        )
        return MessageOutcome.LOGGED

    logger.error(
        json.dumps({"event": ESCALATED_EVENT, "uuid": str(envelope.uuid), "retry": envelope.retry})
    )
    return MessageOutcome.ESCALATED


async def _resolve(deps: Dependencies, envelope: ReimbursementEnvelope) -> MessageOutcome:
    raise NotImplementedError  # completed in the next task (resolve-by-uuid, staleness, requeue)


def _failure_record(
    event: str, envelope: ReimbursementEnvelope, **extra: Any
) -> dict[str, Any]:
    """The shape every last-resort record shares — carries the item's own
    identifying data and full error history, inside the same trust boundary
    as the payload (mirrors publisher.processing.failure_record)."""
    return {
        "event": event,
        "uuid": str(envelope.uuid),
        "retry": envelope.retry,
        "errors": [error.model_dump(mode="json") for error in envelope.errors],
        **extra,
    }
