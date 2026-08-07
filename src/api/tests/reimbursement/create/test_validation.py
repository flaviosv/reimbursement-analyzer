import json

import pytest
from api.config import MAX_BODY_BYTES
from api.errors import BatchInvalid, PayloadTooLarge
from api.reimbursement.create.validation import read_capped, validate_batch
from starlette.requests import Request

VALID_ITEM = {
    "request_id": "REQ-0001",
    "submitted_by": "person@example.com",
    "submitted_at": "2026-01-01T12:00:00Z",
}


def _request_from_chunks(chunks: list[bytes], content_length: int | None = None) -> Request:
    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    scope = {"type": "http", "method": "POST", "headers": headers}

    messages = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ] or [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive() -> dict:
        return messages.pop(0)

    return Request(scope, receive)


class DescribeReadCapped:
    # anyio's pytest plugin is present transitively (via starlette/httpx) but
    # not set to auto mode, so async tests need this marker or they are
    # silently never awaited.
    pytestmark = pytest.mark.anyio

    async def it_accepts_a_body_at_exactly_the_byte_limit(self) -> None:
        chunk_a = b"x" * (MAX_BODY_BYTES - 10)
        chunk_b = b"y" * 10
        request = _request_from_chunks([chunk_a, chunk_b])

        result = await read_capped(request)

        assert len(result) == MAX_BODY_BYTES

    async def it_rejects_a_body_one_byte_over_the_limit(self) -> None:
        chunk_a = b"x" * MAX_BODY_BYTES
        chunk_b = b"y"
        request = _request_from_chunks([chunk_a, chunk_b])

        with pytest.raises(PayloadTooLarge, match="payload exceeds 25 MiB limit"):
            await read_capped(request)

    async def it_decides_from_counted_bytes_not_a_lying_content_length_header(self) -> None:
        # Header claims a tiny body; the real streamed bytes exceed the cap.
        # If the header were consulted, this would never raise.
        chunk_a = b"x" * MAX_BODY_BYTES
        chunk_b = b"y"
        request = _request_from_chunks([chunk_a, chunk_b], content_length=10)

        with pytest.raises(PayloadTooLarge):
            await read_capped(request)


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
