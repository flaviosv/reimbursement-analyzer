import json

import pytest
from shared.testing import valid_reimbursement_item
from shared.errors import BatchInvalid

from api.reimbursement.create.validation import MAX_BATCH_ITEMS, validate_batch

VALID_ITEM = valid_reimbursement_item()


class DescribeValidateBatch:
    def it_accepts_a_well_formed_batch(self) -> None:
        result = validate_batch(json.dumps([VALID_ITEM]).encode())

        assert len(result) == 1
        assert result[0].request_id == "REQ-0001"

    def it_keeps_unknown_fields_via_extra_allow(self) -> None:
        item = {**VALID_ITEM, "claimed_amount_brl": 150.0, "raw_ocr_text": "..."}

        result = validate_batch(json.dumps([item]).encode())

        assert result[0].model_extra == {"claimed_amount_brl": 150.0, "raw_ocr_text": "..."}

    @pytest.mark.parametrize("missing_field", ["request_id", "submitted_by", "submitted_at"])
    def it_rejects_a_batch_missing_a_required_field(self, missing_field: str) -> None:
        item = {k: v for k, v in VALID_ITEM.items() if k != missing_field}

        with pytest.raises(BatchInvalid) as exc_info:
            validate_batch(json.dumps([item]).encode())

        assert str(exc_info.value) == f"item 0: {missing_field} — Field required"

    def it_rejects_an_invalid_email(self) -> None:
        item = {**VALID_ITEM, "submitted_by": "not-an-email"}

        with pytest.raises(BatchInvalid) as exc_info:
            validate_batch(json.dumps([item]).encode())

        assert str(exc_info.value).startswith(
            "item 0: submitted_by — value is not a valid email address"
        )

    def it_rejects_a_naive_submitted_at(self) -> None:
        item = {**VALID_ITEM, "submitted_at": "2026-01-01T12:00:00"}

        with pytest.raises(BatchInvalid) as exc_info:
            validate_batch(json.dumps([item]).encode())

        assert str(exc_info.value) == "item 0: submitted_at — Input should have timezone info"

    def it_rejects_a_completely_unparseable_submitted_at(self) -> None:
        # Distinct from the naive-datetime case above: this string isn't a
        # datetime at all, not merely one missing a timezone offset.
        item = {**VALID_ITEM, "submitted_at": "not-a-date"}

        with pytest.raises(BatchInvalid) as exc_info:
            validate_batch(json.dumps([item]).encode())

        assert str(exc_info.value).startswith("item 0: submitted_at — Input should be a valid datetime")

    def it_rejects_a_non_json_body(self) -> None:
        with pytest.raises(BatchInvalid) as exc_info:
            validate_batch(b"not json")

        assert str(exc_info.value).startswith("Invalid JSON:")

    def it_rejects_a_json_object_instead_of_an_array(self) -> None:
        with pytest.raises(BatchInvalid) as exc_info:
            validate_batch(json.dumps(VALID_ITEM).encode())

        assert str(exc_info.value) == "Input should be a valid array"

    def it_rejects_an_empty_array(self) -> None:
        with pytest.raises(BatchInvalid) as exc_info:
            validate_batch(b"[]")

        assert str(exc_info.value) == "batch must contain at least one request"

    def it_rejects_a_batch_over_the_max_item_cap(self) -> None:
        batch = [{**VALID_ITEM, "request_id": f"REQ-{n}"} for n in range(MAX_BATCH_ITEMS + 1)]

        with pytest.raises(BatchInvalid) as exc_info:
            validate_batch(json.dumps(batch).encode())

        assert f"at most {MAX_BATCH_ITEMS} items" in str(exc_info.value)

    def it_accepts_a_batch_at_exactly_the_max_item_cap(self) -> None:
        batch = [{**VALID_ITEM, "request_id": f"REQ-{n}"} for n in range(MAX_BATCH_ITEMS)]

        result = validate_batch(json.dumps(batch).encode())

        assert len(result) == MAX_BATCH_ITEMS

    def it_names_the_second_items_index_not_the_first(self) -> None:
        bad_item = {k: v for k, v in VALID_ITEM.items() if k != "request_id"}

        with pytest.raises(BatchInvalid) as exc_info:
            validate_batch(json.dumps([VALID_ITEM, bad_item]).encode())

        assert str(exc_info.value) == "item 1: request_id — Field required"

    def it_never_includes_the_offending_value_in_the_message(self) -> None:
        item = {**VALID_ITEM, "submitted_by": "SECRET-VALUE-9f3a"}

        with pytest.raises(BatchInvalid) as exc_info:
            validate_batch(json.dumps([item]).encode())

        assert "SECRET-VALUE-9f3a" not in str(exc_info.value)

    def it_still_names_the_index_when_the_item_itself_is_not_an_object(self) -> None:
        # No field name is possible here — the item never became one — but
        # the index alone must still be named.
        with pytest.raises(BatchInvalid) as exc_info:
            validate_batch(json.dumps(["not-a-dict"]).encode())

        assert str(exc_info.value) == "item 0: Input should be an object"

    @pytest.mark.parametrize("bad_request_id", ["", "   ", 123])
    def it_rejects_an_empty_whitespace_or_non_string_request_id(self, bad_request_id) -> None:
        item = {**VALID_ITEM, "request_id": bad_request_id}

        with pytest.raises(BatchInvalid) as exc_info:
            validate_batch(json.dumps([item]).encode())

        assert str(exc_info.value).startswith("item 0: request_id —")

    def it_rejects_a_request_id_over_the_length_cap(self) -> None:
        item = {**VALID_ITEM, "request_id": "x" * 129}

        with pytest.raises(BatchInvalid) as exc_info:
            validate_batch(json.dumps([item]).encode())

        assert str(exc_info.value) == "item 0: request_id — String should have at most 128 characters"

    def it_rejects_a_request_id_with_disallowed_characters(self) -> None:
        item = {**VALID_ITEM, "request_id": "REQ 0001!"}

        with pytest.raises(BatchInvalid) as exc_info:
            validate_batch(json.dumps([item]).encode())

        assert "request_id" in str(exc_info.value)

    def it_rejects_a_bare_epoch_number_for_submitted_at(self) -> None:
        # AwareDatetime alone accepts int/float as Unix timestamps; this is
        # not a parseable ISO-8601 string at all.
        item = {**VALID_ITEM, "submitted_at": 1_700_000_000}

        with pytest.raises(BatchInvalid) as exc_info:
            validate_batch(json.dumps([item]).encode())

        assert str(exc_info.value) == (
            "item 0: submitted_at — Value error, input should be an ISO-8601 "
            "string, not a bare number"
        )
