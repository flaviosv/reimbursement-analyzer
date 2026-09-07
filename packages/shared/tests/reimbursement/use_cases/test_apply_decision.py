from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest
from shared.metrics import reimbursement_status_transitions_total
from shared.reimbursement.repository import insert_pending
from shared.reimbursement.use_cases.apply_decision import apply_decision
from shared.testing import valid_reimbursement_item

pytestmark = pytest.mark.anyio


class DescribeApplyDecision:
    @pytest.mark.parametrize(
        ("status", "reason"),
        [
            pytest.param("auto-approved", "value 150 <= 200 threshold", id="auto_approved"),
            pytest.param("auto-rejected", "receipt 91 days old", id="auto_rejected"),
            pytest.param("human-review", "value 1000 in the ambiguous zone", id="human_review"),
        ],
    )
    async def it_updates_the_row_and_returns_its_uuid(
        self, db: asyncpg.Connection, status: str, reason: str
    ) -> None:
        uuid = await insert_pending(db, valid_reimbursement_item(f"REQ-APPLY-{status.upper()}"))

        result = await apply_decision(db, uuid, status, reason)

        assert result == uuid
        assert isinstance(result, UUID)
        row = await db.fetchrow("SELECT * FROM reimbursement WHERE uuid = $1", uuid)
        assert row["status"] == status
        assert row["decision_reason"] == reason

    async def it_returns_none_for_a_uuid_matching_no_row(self, db: asyncpg.Connection) -> None:
        result = await apply_decision(db, uuid4(), "human-review", "unreachable reason")

        assert result is None

    async def it_backfills_receipts_value_date_and_currency_when_provided(
        self, db: asyncpg.Connection
    ) -> None:
        uuid = await insert_pending(db, valid_reimbursement_item("REQ-APPLY-RECEIPTS"))

        await apply_decision(
            db,
            uuid,
            "auto-approved",
            "value 150 <= 200 threshold",
            receipts_value=Decimal("150.00"),
            receipts_date=date(2026, 4, 9),
            currency="BRL",
        )

        row = await db.fetchrow("SELECT * FROM reimbursement WHERE uuid = $1", uuid)
        assert row["receipts_value"] == Decimal("150.00")
        assert row["receipts_date"] == date(2026, 4, 9)
        assert row["currency"] == "BRL"

    async def it_leaves_receipts_columns_untouched_when_not_provided(
        self, db: asyncpg.Connection
    ) -> None:
        # escalate_existing's own call site: no resolved extraction to
        # backfill from — a prior partial decision's receipts_* values
        # (if any) must survive an escalation, not get nulled out.
        uuid = await insert_pending(db, valid_reimbursement_item("REQ-APPLY-NO-RECEIPTS"))
        await apply_decision(
            db,
            uuid,
            "human-review",
            "first pass",
            receipts_value=Decimal("64.80"),
            receipts_date=date(2026, 4, 11),
            currency="BRL",
        )

        await apply_decision(db, uuid, "human-review", "retry ceiling reached")

        row = await db.fetchrow("SELECT * FROM reimbursement WHERE uuid = $1", uuid)
        assert row["decision_reason"] == "retry ceiling reached"
        assert row["receipts_value"] == Decimal("64.80")
        assert row["receipts_date"] == date(2026, 4, 11)
        assert row["currency"] == "BRL"


class DescribeApplyDecisionStatusTransitionsMetric:
    async def it_increments_status_transitions_total_with_pending_as_the_from_label(
        self, db: asyncpg.Connection
    ) -> None:
        uuid = await insert_pending(db, valid_reimbursement_item("REQ-APPLY-METRIC-INC"))
        before = reimbursement_status_transitions_total.labels("pending", "auto-approved")._value.get()

        result = await apply_decision(db, uuid, "auto-approved", "value 150 <= 200 threshold")

        assert result == uuid
        after = reimbursement_status_transitions_total.labels("pending", "auto-approved")._value.get()
        assert after == before + 1

    async def it_does_not_increment_status_transitions_total_for_a_ghost_uuid(
        self, db: asyncpg.Connection
    ) -> None:
        before = reimbursement_status_transitions_total.labels("pending", "human-review")._value.get()

        result = await apply_decision(db, uuid4(), "human-review", "unreachable reason")

        assert result is None
        after = reimbursement_status_transitions_total.labels("pending", "human-review")._value.get()
        assert after == before
