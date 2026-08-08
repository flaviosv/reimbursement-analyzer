from datetime import UTC, datetime
from uuid import UUID

import asyncpg
import pytest
from fakes import FakeProducer
from helpers import valid_reimbursement_item
from shared.config import REIMBURSEMENT_TOPIC
from shared.models import AttemptError
from shared.reimbursement.use_cases.publish_pending import publish_pending

pytestmark = pytest.mark.anyio


def _error(attempt: int) -> AttemptError:
    return AttemptError(
        attempt=attempt,
        occurred_at=datetime(2026, 4, 10, 9, attempt, 0, tzinfo=UTC),
        stage="db-insert",
        error_type="PostgresConnectionError",
        message="connection reset by peer",
    )


class DescribePublishPending:
    async def it_stores_the_item_as_a_pending_row(self, db: asyncpg.Connection) -> None:
        item = valid_reimbursement_item("REQ-PUBLISH-PENDING")
        producer = FakeProducer()

        await publish_pending(db, producer, item, [], publish_timeout_seconds=5.0)

        row = await db.fetchrow(
            "SELECT status, request_id FROM reimbursement WHERE request_id = $1",
            "REQ-PUBLISH-PENDING",
        )
        assert row["status"] == "pending"

    async def it_publishes_the_stored_rows_uuid(self, db: asyncpg.Connection) -> None:
        item = valid_reimbursement_item("REQ-PUBLISH-UUID")
        producer = FakeProducer()

        await publish_pending(db, producer, item, [], publish_timeout_seconds=5.0)

        row = await db.fetchrow(
            "SELECT uuid FROM reimbursement WHERE request_id = $1", "REQ-PUBLISH-UUID"
        )
        published = producer.messages(REIMBURSEMENT_TOPIC)[0]
        assert UUID(published["uuid"]) == row["uuid"]

    async def it_forwards_the_error_history_onto_the_published_envelope(
        self, db: asyncpg.Connection
    ) -> None:
        # The one construction site of ReimbursementEnvelope previously never
        # forwarded this field at all (H1/Q16) — an item that failed then
        # succeeded handed the Agent an empty history at exactly the handoff
        # this field exists to make informative.
        item = valid_reimbursement_item("REQ-PUBLISH-HISTORY")
        producer = FakeProducer()
        history = [_error(1), _error(2)]

        await publish_pending(db, producer, item, history, publish_timeout_seconds=5.0)

        published = producer.messages(REIMBURSEMENT_TOPIC)[0]
        assert len(published["errors"]) == 2
        assert published["errors"][0]["message"] == "connection reset by peer"
