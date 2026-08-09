"""Test doubles, DB seed helpers, and generic cross-service test-provisioning
utilities, importable by any service's own test suite via a normal package
import — not bare-name pythonpath resolution, so there is nothing to
collide with.

Production code never imports this module.
"""

import asyncio
import json
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from secrets import token_hex
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import asyncpg

# Matches the postgres service in docker-compose.yml, so tests exercise the
# same major version the stack runs.
POSTGRES_IMAGE = "postgres:18"
# Dropping and creating a database needs a session that is not attached to it;
# 'postgres' always exists on the server.
MAINTENANCE_DATABASE = "postgres"


def database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def with_database(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path=f"/{name}"))


def maintenance_url(url: str) -> str:
    return with_database(url, MAINTENANCE_DATABASE)


def disposable_database_name() -> str:
    """A database name no concurrent run can collide with.

    The suite drops its database WITH (FORCE), which terminates whatever
    backends are attached. Under a shared constant name that is not a race but
    mutual destruction -- two runs against one server tear each other down
    mid-assertion -- and pytest-xdist cannot work at all.
    """
    return f"reimbursementanalyzer_{os.getpid()}_{token_hex(4)}_test"


def guard_is_test_database(url: str) -> None:
    name = database_name(url)
    if not name.endswith("_test"):
        raise RuntimeError(
            f"refusing to run against database {name!r}: the test suite drops "
            "and recreates its database, so the name must end in '_test'"
        )


def valid_reimbursement_item(request_id: str = "REQ-0001", **extra: object) -> dict:
    """The canonical minimal-valid POST /api/v1/reimbursement item shape —
    a single source of truth across api's, publisher's, and reimbursement's
    own test suites, which each need a slightly different usage pattern
    (a fixed dict vs. a request_id-keyed factory) but previously maintained
    independently hand-copied versions of this shape."""
    return {
        "request_id": request_id,
        "submitted_by": "person@example.com",
        "submitted_at": "2026-01-01T12:00:00Z",
        **extra,
    }


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
    VALUES ($1, $2, $3::text::jsonb, $4, $5)
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
    original_payload: dict[str, Any] | None = None,
) -> UUID:
    """Row-level seed shared across every service's own test suite — each
    needs a reimbursement row present at a given status without going
    through the real insert/approve flow."""
    uuid = uuid4()
    await db.execute(
        _SEED_REIMBURSEMENT,
        uuid,
        request_id,
        json.dumps(original_payload or {}),
        status,
        created_at or datetime.now(UTC),
    )
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
