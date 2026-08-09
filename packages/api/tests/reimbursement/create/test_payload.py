import pytest
from shared.config import MAX_BODY_BYTES
from shared.errors import PayloadTooLarge
from starlette.requests import Request

from api.reimbursement.create.payload import _enforce_max_body_bytes, read_capped


class DescribeEnforceMaxBodyBytes:
    def it_accepts_a_total_at_exactly_the_real_ceiling(self) -> None:
        _enforce_max_body_bytes(MAX_BODY_BYTES)

    def it_rejects_a_total_one_byte_over_the_real_ceiling(self) -> None:
        with pytest.raises(PayloadTooLarge, match="payload exceeds 1 MiB limit"):
            _enforce_max_body_bytes(MAX_BODY_BYTES + 1)


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

    @pytest.fixture(autouse=True)
    def small_cap(self, monkeypatch: pytest.MonkeyPatch) -> int:
        # This class only exercises read_capped's counting/abort logic, which
        # is entirely size-agnostic — a small monkeypatched cap keeps these
        # tests cheap and independent of MAX_BODY_BYTES's real value,
        # matching the pattern test_route.py already uses.
        cap = 100
        monkeypatch.setattr("api.reimbursement.create.payload.MAX_BODY_BYTES", cap)
        return cap

    async def it_accepts_a_body_at_exactly_the_byte_limit(self, small_cap: int) -> None:
        chunk_a = b"x" * (small_cap - 10)
        chunk_b = b"y" * 10
        request = _request_from_chunks([chunk_a, chunk_b])

        result = await read_capped(request)

        assert len(result) == small_cap

    async def it_rejects_a_body_one_byte_over_the_limit(self, small_cap: int) -> None:
        chunk_a = b"x" * small_cap
        chunk_b = b"y"
        request = _request_from_chunks([chunk_a, chunk_b])

        with pytest.raises(PayloadTooLarge, match="payload exceeds 0 MiB limit"):
            await read_capped(request)

    async def it_decides_from_counted_bytes_not_a_lying_content_length_header(
        self, small_cap: int
    ) -> None:
        # Header claims a tiny body; the real streamed bytes exceed the cap.
        # If the header were consulted, this would never raise.
        chunk_a = b"x" * small_cap
        chunk_b = b"y"
        request = _request_from_chunks([chunk_a, chunk_b], content_length=10)

        with pytest.raises(PayloadTooLarge):
            await read_capped(request)
