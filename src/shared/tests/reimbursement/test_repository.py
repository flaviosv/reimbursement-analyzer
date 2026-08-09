import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest
from shared.testing import valid_reimbursement_item
from shared.reimbursement.repository import (
    approve,
    fetch_reimbursement_page,
    find_reimbursement_state,
    get_by_uuid,
    insert_human_review,
    insert_pending,
    is_duplicate,
    record_human_review_decision,
    reject,
    update_decision,
)
from shared.testing import (
    seed_human_review,
    seed_reimbursement,
    seed_reimbursement_with_receipts,
)

pytestmark = pytest.mark.anyio

_RAW_INSERT = """
    INSERT INTO reimbursement (uuid, request_id, original_payload)
    VALUES ($1, $2, '{}'::jsonb)
"""

_ELIGIBLE_STATUSES = ["human-review", "auto-rejected", "human-rejected"]


async def _rows_for(db: asyncpg.Connection, request_id: str) -> list[asyncpg.Record]:
    return await db.fetch("SELECT * FROM reimbursement WHERE request_id = $1", request_id)


class DescribeInsertPending:
    async def it_returns_the_uuid_of_the_row_it_created(self, db: asyncpg.Connection) -> None:
        uuid = await insert_pending(db, valid_reimbursement_item("REQ-RETURN"))

        assert isinstance(uuid, UUID)
        assert [row["uuid"] for row in await _rows_for(db, "REQ-RETURN")] == [uuid]

    async def it_populates_the_identity_columns_and_the_original_payload(
        self, db: asyncpg.Connection
    ) -> None:
        item = valid_reimbursement_item("REQ-FIELDS", amount=93.5, currency="BRL")

        uuid = await insert_pending(db, item)

        row = await db.fetchrow("SELECT * FROM reimbursement WHERE uuid = $1", uuid)
        assert row["request_id"] == "REQ-FIELDS"
        assert row["submitted_by"] == "person@example.com"
        assert row["submitted_at"] == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        assert json.loads(row["original_payload"]) == item

    async def it_leaves_the_status_at_its_pending_default(self, db: asyncpg.Connection) -> None:
        uuid = await insert_pending(db, valid_reimbursement_item("REQ-PENDING"))

        status = await db.fetchval("SELECT status FROM reimbursement WHERE uuid = $1", uuid)
        assert status == "pending"

    async def it_stores_no_second_row_for_a_repeated_request_and_submitter(
        self, db: asyncpg.Connection
    ) -> None:
        item = valid_reimbursement_item("REQ-DUP", amount=10.0)
        first_uuid = await insert_pending(db, item)
        resubmitted = valid_reimbursement_item("REQ-DUP", amount=999.0)

        with pytest.raises(asyncpg.UniqueViolationError):
            async with db.transaction():
                await insert_pending(db, resubmitted)

        rows = await _rows_for(db, "REQ-DUP")
        assert [row["uuid"] for row in rows] == [first_uuid]
        # The rejected insert leaves the row it collided with untouched — it is
        # still the first submission, not a silent overwrite by the second.
        assert json.loads(rows[0]["original_payload"]) == item
        assert rows[0]["status"] == "pending"
        assert rows[0]["decision_reason"] is None

    async def it_stores_the_validated_request_id_not_the_raw_whitespace_padded_one(
        self, db: asyncpg.Connection
    ) -> None:
        # request_id has strip_whitespace=True on the pydantic model; the raw
        # dict does not carry that normalisation. Storing the raw form would
        # let " REQ-PAD " and "REQ-PAD" coexist as two rows the dedup index
        # was supposed to treat as the same request.
        item = valid_reimbursement_item(" REQ-PAD ")

        uuid = await insert_pending(db, item)

        row = await db.fetchrow("SELECT request_id FROM reimbursement WHERE uuid = $1", uuid)
        assert row["request_id"] == "REQ-PAD"

    async def it_stores_no_second_row_when_only_the_submitter_case_differs(
        self, db: asyncpg.Connection
    ) -> None:
        # The index is on lower(submitted_by): a byte-exact key would let one
        # person file the same request under ana@, Ana@ and ANA@.
        first_uuid = await insert_pending(
            db, valid_reimbursement_item("REQ-CASE", submitted_by="ana@company.com")
        )

        with pytest.raises(asyncpg.UniqueViolationError):
            async with db.transaction():
                await insert_pending(
                    db, valid_reimbursement_item("REQ-CASE", submitted_by="ANA@Company.com")
                )

        rows = await _rows_for(db, "REQ-CASE")
        assert [row["uuid"] for row in rows] == [first_uuid]
        assert rows[0]["submitted_by"] == "ana@company.com"


class DescribeInsertHumanReview:
    async def it_stores_the_row_at_human_review_with_its_reason(self, db: asyncpg.Connection) -> None:
        item = valid_reimbursement_item("REQ-REVIEW")
        reason = "attempt 1 [db-insert] RuntimeError: connection reset"

        uuid = await insert_human_review(db, item, reason)

        row = await db.fetchrow("SELECT * FROM reimbursement WHERE uuid = $1", uuid)
        assert row["status"] == "human-review"
        assert row["decision_reason"] == reason

    async def it_populates_the_identity_columns_and_the_original_payload(
        self, db: asyncpg.Connection
    ) -> None:
        item = valid_reimbursement_item("REQ-REVIEW-FIELDS", amount=12.0)

        uuid = await insert_human_review(db, item, "ceiling reached")

        row = await db.fetchrow("SELECT * FROM reimbursement WHERE uuid = $1", uuid)
        assert row["request_id"] == "REQ-REVIEW-FIELDS"
        assert row["submitted_by"] == "person@example.com"
        assert row["submitted_at"] == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        assert json.loads(row["original_payload"]) == item


class DescribeIsDuplicate:
    async def it_classifies_a_request_and_submitter_collision_as_a_duplicate(
        self, db: asyncpg.Connection
    ) -> None:
        item = valid_reimbursement_item("REQ-IS-DUP")
        await insert_pending(db, item)

        with pytest.raises(asyncpg.UniqueViolationError) as caught:
            async with db.transaction():
                await insert_pending(db, item)

        assert caught.value.constraint_name == "reimbursement_request_submitter_key"
        assert is_duplicate(caught.value) is True

    async def it_does_not_classify_a_primary_key_collision_as_a_duplicate(
        self, db: asyncpg.Connection
    ) -> None:
        # A PK collision is a different failure entirely and must not take the
        # silent-drop path — which a bare 23505 sqlstate check would let it.
        uuid = uuid4()
        await db.execute(_RAW_INSERT, uuid, "REQ-PK-FIRST")

        with pytest.raises(asyncpg.UniqueViolationError) as caught:
            async with db.transaction():
                await db.execute(_RAW_INSERT, uuid, "REQ-PK-SECOND")

        assert caught.value.constraint_name == "reimbursement_pkey"
        assert is_duplicate(caught.value) is False

    async def it_does_not_classify_an_unrelated_error_as_a_duplicate(self) -> None:
        assert is_duplicate(RuntimeError("connection reset by peer")) is False


class DescribeFetchReimbursementPage:
    async def it_returns_the_requested_page_slice_via_limit_and_offset(self, db: asyncpg.Connection) -> None:
        # Far-future base: unlike the human-review-scoped tests below, exact
        # LIMIT/OFFSET order can't be made robust with a membership check —
        # it has to out-rank whatever `now()` an unrelated real commit (e.g.
        # the agent's own decision-graph integration test, which persists a
        # genuine auto-approved row) lands at in this same shared migrated_db.
        base = datetime(2099, 5, 1, tzinfo=UTC)
        # Scoped by status: other tests in the full suite (e.g. create's real
        # broker roundtrip) commit real, uncontrolled `pending` rows into this
        # same shared migrated_db outside this test's rollback — an
        # exclusive-ownership assumption over the whole table would be flaky.
        uuids = [
            await seed_reimbursement(
                db, f"REQ-PAGE-{n}", status="auto-approved", created_at=base + timedelta(minutes=n)
            )
            for n in range(5)
        ]
        # created_at DESC → newest first: [4, 3, 2, 1, 0]; offset=1, limit=2 → [3, 2]
        expected = [uuids[3], uuids[2]]

        page = await fetch_reimbursement_page(db, statuses=["auto-approved"], limit=2, offset=1)

        assert [row["uuid"] for row in page] == expected

    async def it_filters_to_a_single_status(self, db: asyncpg.Connection) -> None:
        await seed_reimbursement(db, "REQ-SINGLE-A", status="human-review")
        await seed_reimbursement(db, "REQ-SINGLE-B", status="auto-rejected")

        page = await fetch_reimbursement_page(db, statuses=["human-review"], limit=100, offset=0)

        # Membership, not exact-set: other pre-existing integration tests
        # (e.g. agent's, REVIEW-09) commit real, undeletable human-review
        # rows to this same session-scoped migrated_db.
        request_ids = {row["request_id"] for row in page}
        assert "REQ-SINGLE-A" in request_ids
        assert "REQ-SINGLE-B" not in request_ids

    async def it_filters_to_multiple_statuses_via_any(self, db: asyncpg.Connection) -> None:
        await seed_reimbursement(db, "REQ-MULTI-A", status="human-review")
        await seed_reimbursement(db, "REQ-MULTI-B", status="auto-rejected")
        await seed_reimbursement(db, "REQ-MULTI-C", status="auto-approved")

        page = await fetch_reimbursement_page(
            db, statuses=["human-review", "auto-rejected"], limit=100, offset=0
        )

        # Subset, not exact-set: see it_filters_to_a_single_status above.
        request_ids = {row["request_id"] for row in page}
        assert {"REQ-MULTI-A", "REQ-MULTI-B"} <= request_ids
        assert "REQ-MULTI-C" not in request_ids

    async def it_returns_every_status_including_pending_when_no_filter_is_given(
        self, db: asyncpg.Connection
    ) -> None:
        await seed_reimbursement(db, "REQ-ALL-PENDING", status="pending")
        await seed_reimbursement(db, "REQ-ALL-APPROVED", status="human-approved")

        # No filter means the whole table, so — unlike the status-scoped tests
        # above — this can't isolate itself from other tests' committed rows
        # via WHERE; it looks its own two rows up by request_id instead of
        # asserting the returned set is exactly these two.
        page = await fetch_reimbursement_page(db, statuses=None, limit=200, offset=0)
        by_request_id = {row["request_id"]: row for row in page}

        assert by_request_id["REQ-ALL-PENDING"]["status"] == "pending"
        assert by_request_id["REQ-ALL-APPROVED"]["status"] == "human-approved"

    async def it_returns_an_empty_list_when_nothing_matches(self, db: asyncpg.Connection) -> None:
        await seed_reimbursement(db, "REQ-EMPTY", status="pending")

        page = await fetch_reimbursement_page(db, statuses=["auto-rejected"], limit=100, offset=0)

        assert page == []

    async def it_returns_an_empty_list_when_offset_is_beyond_the_total_row_count(
        self, db: asyncpg.Connection
    ) -> None:
        await seed_reimbursement(db, "REQ-OFFSET-BEYOND", status="auto-rejected")

        page = await fetch_reimbursement_page(db, statuses=["auto-rejected"], limit=100, offset=1000)

        assert page == []

    async def it_does_not_duplicate_rows_for_a_repeated_status_in_the_filter(
        self, db: asyncpg.Connection
    ) -> None:
        await seed_reimbursement(db, "REQ-DUP-STATUS", status="auto-rejected")

        page = await fetch_reimbursement_page(
            db, statuses=["auto-rejected", "auto-rejected"], limit=100, offset=0
        )

        assert [row["request_id"] for row in page] == ["REQ-DUP-STATUS"]

    async def it_populates_last_human_review_when_present_and_null_when_absent(
        self, db: asyncpg.Connection
    ) -> None:
        with_review = await seed_reimbursement(db, "REQ-HR-PRESENT", status="human-approved")
        await seed_human_review(
            db, with_review, reason="first look", created_at=datetime(2026, 5, 1, tzinfo=UTC)
        )
        await seed_human_review(
            db, with_review, reason="second look", created_at=datetime(2026, 5, 2, tzinfo=UTC)
        )
        without_review = await seed_reimbursement(db, "REQ-HR-ABSENT", status="human-review")

        page = await fetch_reimbursement_page(db, statuses=None, limit=100, offset=0)
        by_uuid = {row["uuid"]: row for row in page}

        # Most recent by created_at, never a history/count.
        assert by_uuid[with_review]["hr_reason"] == "second look"
        assert by_uuid[with_review]["hr_status"] == "approved"
        assert by_uuid[without_review]["hr_status"] is None
        assert by_uuid[without_review]["hr_reason"] is None

    async def it_orders_results_by_created_at_descending(self, db: asyncpg.Connection) -> None:
        # Far-future base — see it_returns_the_requested_page_slice_via_limit_and_offset above.
        base = datetime(2099, 5, 1, tzinfo=UTC)
        oldest = await seed_reimbursement(db, "REQ-ORDER-OLD", status="auto-approved", created_at=base)
        middle = await seed_reimbursement(
            db, "REQ-ORDER-MID", status="auto-approved", created_at=base + timedelta(hours=12)
        )
        newest = await seed_reimbursement(
            db, "REQ-ORDER-NEW", status="auto-approved", created_at=base + timedelta(days=1)
        )

        page = await fetch_reimbursement_page(db, statuses=["auto-approved"], limit=100, offset=0)

        # Membership, not exact-set (see it_filters_to_a_single_status above)
        # — this test's own limit=100/offset=0 has no window to hide an
        # unrelated real auto-approved commit behind, so relative order is
        # checked only among these three known rows, by uuid.
        known = {oldest, middle, newest}
        ordered_known = [row["uuid"] for row in page if row["uuid"] in known]
        assert ordered_known == [newest, middle, oldest]

    async def it_orders_results_by_created_at_descending_on_the_no_filter_path(
        self, db: asyncpg.Connection
    ) -> None:
        # A single-status filter can be served by the
        # reimbursement_status_created_idx (status, created_at DESC) index
        # scan, which incidentally preserves DESC order even without the
        # query's own explicit ORDER BY — see validation.md's surviving
        # mutant. Mixed statuses force the $1::text[] IS NULL no-filter
        # branch, which that index cannot serve; a seq scan without ORDER BY
        # returns rows in roughly insertion (ascending) order, so this is the
        # one path where dropping ORDER BY actually flips the result.
        base = datetime(2026, 6, 1, tzinfo=UTC)
        oldest = await seed_reimbursement(db, "REQ-ORDER-NOFILTER-OLD", status="pending", created_at=base)
        middle = await seed_reimbursement(
            db, "REQ-ORDER-NOFILTER-MID", status="human-review", created_at=base + timedelta(hours=12)
        )
        newest = await seed_reimbursement(
            db, "REQ-ORDER-NOFILTER-NEW", status="auto-rejected", created_at=base + timedelta(days=1)
        )

        page = await fetch_reimbursement_page(db, statuses=None, limit=1000, offset=0)
        own_rows = [row["uuid"] for row in page if row["uuid"] in {oldest, middle, newest}]

        assert own_rows == [newest, middle, oldest]


class DescribeApprove:
    async def it_succeeds_on_an_eligible_row_and_returns_the_updated_row(
        self, db: asyncpg.Connection
    ) -> None:
        uuid = await seed_reimbursement(db, "REQ-APPROVE-ELIGIBLE", status="human-review")

        row = await approve(
            db,
            uuid,
            eligible_statuses=_ELIGIBLE_STATUSES,
            receipts_value=Decimal("50.00"),
            receipts_date=date(2026, 1, 5),
            receipts_currency="BRL",
            reason="looks good",
        )

        assert row["status"] == "human-approved"
        assert row["receipts_value"] == Decimal("50.00")
        assert row["receipts_date"] == date(2026, 1, 5)
        assert row["currency"] == "BRL"
        assert row["decision_reason"] == "looks good"

    async def it_returns_none_on_an_ineligible_status(self, db: asyncpg.Connection) -> None:
        uuid = await seed_reimbursement(db, "REQ-APPROVE-INELIGIBLE", status="human-approved")

        row = await approve(
            db,
            uuid,
            eligible_statuses=_ELIGIBLE_STATUSES,
            receipts_value=Decimal("50.00"),
            receipts_date=date(2026, 1, 5),
            receipts_currency="BRL",
            reason="looks good",
        )

        assert row is None
        status = await db.fetchval("SELECT status FROM reimbursement WHERE uuid = $1", uuid)
        assert status == "human-approved"


class DescribeReject:
    async def it_succeeds_when_all_three_receipt_fields_are_already_set(
        self, db: asyncpg.Connection
    ) -> None:
        uuid = await seed_reimbursement_with_receipts(db, "REQ-REJECT-COMPLETE", status="human-review")

        row = await reject(db, uuid, eligible_statuses=_ELIGIBLE_STATUSES, reason="bad receipt")

        assert row["status"] == "human-rejected"
        assert row["decision_reason"] == "bad receipt"

    async def it_returns_none_when_any_receipt_field_is_null_even_on_an_eligible_status(
        self, db: asyncpg.Connection
    ) -> None:
        uuid = await seed_reimbursement(db, "REQ-REJECT-INCOMPLETE", status="human-review")

        row = await reject(db, uuid, eligible_statuses=_ELIGIBLE_STATUSES, reason="bad receipt")

        assert row is None
        status = await db.fetchval("SELECT status FROM reimbursement WHERE uuid = $1", uuid)
        assert status == "human-review"

    @pytest.mark.parametrize(
        ("receipts_value", "receipts_date", "currency", "request_id"),
        [
            pytest.param(Decimal("100.00"), None, None, "REQ-REJECT-ONLY-VALUE", id="only_value_set"),
            pytest.param(None, date(2026, 1, 1), None, "REQ-REJECT-ONLY-DATE", id="only_date_set"),
            pytest.param(None, None, "BRL", "REQ-REJECT-ONLY-CURRENCY", id="only_currency_set"),
            pytest.param(
                Decimal("100.00"), date(2026, 1, 1), None, "REQ-REJECT-TWO-OF-THREE", id="two_of_three_set"
            ),
        ],
    )
    async def it_returns_none_when_exactly_one_or_two_receipt_fields_are_set(
        self,
        db: asyncpg.Connection,
        receipts_value: Decimal | None,
        receipts_date: date | None,
        currency: str | None,
        request_id: str,
    ) -> None:
        # The _REJECT guard is three independent AND'd IS NOT NULL checks, not
        # one all-or-nothing test — a partial row (e.g. only receipts_value
        # set) must still fail, or a future AND→OR regression would slip
        # through undetected by the all-null / all-set cases alone.
        uuid = await seed_reimbursement_with_receipts(
            db,
            request_id,
            status="human-review",
            receipts_value=receipts_value,
            receipts_date=receipts_date,
            currency=currency,
        )

        row = await reject(db, uuid, eligible_statuses=_ELIGIBLE_STATUSES, reason="bad receipt")

        assert row is None
        status = await db.fetchval("SELECT status FROM reimbursement WHERE uuid = $1", uuid)
        assert status == "human-review"


class DescribeFindReimbursementState:
    async def it_returns_none_for_an_unknown_uuid(self, db: asyncpg.Connection) -> None:
        state = await find_reimbursement_state(db, uuid4())

        assert state is None

    async def it_returns_the_row_for_a_known_uuid(self, db: asyncpg.Connection) -> None:
        uuid = await seed_reimbursement(db, "REQ-FIND-STATE", status="auto-rejected")

        state = await find_reimbursement_state(db, uuid)

        assert state["uuid"] == uuid
        assert state["status"] == "auto-rejected"


class DescribeRecordHumanReviewDecision:
    async def it_inserts_exactly_one_human_review_row_with_the_given_fields(
        self, db: asyncpg.Connection
    ) -> None:
        uuid = await seed_reimbursement(db, "REQ-RECORD-DECISION", status="human-review")

        await record_human_review_decision(db, uuid, "approved", "reviewer@example.com", "all good")

        rows = await db.fetch("SELECT * FROM human_review WHERE reimbursement_uuid = $1", uuid)
        assert len(rows) == 1
        assert rows[0]["status"] == "approved"
        assert rows[0]["reviewed_by"] == "reviewer@example.com"
        assert rows[0]["reason"] == "all good"


class DescribeGetByUuid:
    async def it_returns_the_full_row_for_a_real_uuid(self, db: asyncpg.Connection) -> None:
        item = valid_reimbursement_item("REQ-GET", amount=42.0)
        uuid = await insert_pending(db, item)

        row = await get_by_uuid(db, uuid)

        assert row is not None
        assert row["uuid"] == uuid
        assert row["request_id"] == "REQ-GET"
        assert row["status"] == "pending"
        assert row["updated_at"] is not None
        assert json.loads(row["original_payload"]) == item

    async def it_returns_none_for_a_uuid_matching_no_row(self, db: asyncpg.Connection) -> None:
        # The ghost case (R-001): a uuid with no row must resolve to None, not
        # an exception — the Agent's own tolerance for this depends on it.
        row = await get_by_uuid(db, uuid4())

        assert row is None


class DescribeUpdateDecision:
    async def it_returns_true_and_updates_the_existing_row(self, db: asyncpg.Connection) -> None:
        uuid = await insert_pending(db, valid_reimbursement_item("REQ-ESCALATE"))

        result = await update_decision(
            db, uuid, "human-review", "attempt 4 [resolve] RuntimeError: db down"
        )

        assert result is True
        row = await db.fetchrow("SELECT * FROM reimbursement WHERE uuid = $1", uuid)
        assert row["status"] == "human-review"
        assert row["decision_reason"] == "attempt 4 [resolve] RuntimeError: db down"
        assert row["updated_at"] is not None

    async def it_returns_false_for_a_uuid_matching_no_row(self, db: asyncpg.Connection) -> None:
        # The compound ghost + retry>3 case (AGT-18): nothing to update, and
        # the caller must be able to tell "0 rows" from "1 row" to route to
        # the failure log instead of treating this as success.
        result = await update_decision(db, uuid4(), "human-review", "unreachable reason")

        assert result is False
