"""Test doubles and DB seed helpers for the contracts `shared` itself
defines, importable by any service's own test suite via a normal package
import — not bare-name pythonpath resolution, so there is nothing to
collide with.

Production code never imports this module.
"""

import asyncio
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg


class FakeProducer:
    """Stands in for `confluent_kafka.aio.AIOProducer`'s `produce()`
    contract, the one `shared.producer.publish` and every caller depend on.
    Records every produced message. `errors` maps a topic to the exception
    its delivery future carries, so a delivery failure can be injected
    independently per topic."""

    def __init__(self, *, errors: dict[str, Exception] | None = None) -> None:
        self.errors = errors or {}
        self.produced: list[tuple[str, bytes]] = []

    async def produce(self, topic: str, value: bytes, **kwargs: object) -> asyncio.Future:
        await asyncio.sleep(0)
        self.produced.append((topic, value))
        future = asyncio.get_running_loop().create_future()
        error = self.errors.get(topic)
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(object())
        return future

    def messages(self, topic: str) -> list[dict[str, Any]]:
        return [json.loads(value) for produced, value in self.produced if produced == topic]


_SEED_REIMBURSEMENT = """
    INSERT INTO reimbursement (uuid, request_id, original_payload, status, created_at)
    VALUES ($1, $2, '{}'::jsonb, $3, $4)
"""

_SEED_REIMBURSEMENT_WITH_RECEIPTS = """
    INSERT INTO reimbursement (
        uuid, request_id, original_payload, status, receipts_value, receipts_date, currency
    )
    VALUES ($1, $2, '{}'::jsonb, $3, $4, $5, $6)
"""

_SEED_HUMAN_REVIEW = """
    INSERT INTO human_review (uuid, reimbursement_uuid, status, reviewed_by, reason, created_at)
    VALUES ($1, $2, $3, $4, $5, $6)
"""


async def seed_reimbursement(
    db: asyncpg.Connection,
    request_id: str,
    *,
    status: str = "human-review",
    created_at: datetime | None = None,
) -> UUID:
    """Row-level seed shared by shared's own repository and
    review_reimbursement use-case tests — both need a reimbursement row
    present at a given status without going through the real insert/approve
    flow."""
    uuid = uuid4()
    await db.execute(_SEED_REIMBURSEMENT, uuid, request_id, status, created_at or datetime.now(UTC))
    return uuid


async def seed_reimbursement_with_receipts(
    db: asyncpg.Connection,
    request_id: str,
    *,
    status: str = "human-review",
    receipts_value: Decimal | None = Decimal("100.00"),
    receipts_date: date | None = date(2026, 1, 1),
    currency: str | None = "BRL",
) -> UUID:
    uuid = uuid4()
    await db.execute(
        _SEED_REIMBURSEMENT_WITH_RECEIPTS, uuid, request_id, status, receipts_value, receipts_date, currency
    )
    return uuid


async def seed_human_review(
    db: asyncpg.Connection,
    reimbursement_uuid: UUID,
    *,
    status: str = "approved",
    reviewed_by: str = "reviewer@example.com",
    reason: str = "looks good",
    created_at: datetime | None = None,
) -> None:
    await db.execute(
        _SEED_HUMAN_REVIEW,
        uuid4(),
        reimbursement_uuid,
        status,
        reviewed_by,
        reason,
        created_at or datetime.now(UTC),
    )
