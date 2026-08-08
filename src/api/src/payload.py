from fastapi import Request
from shared.config import MAX_BODY_BYTES
from shared.errors import PayloadTooLarge


def _enforce_max_body_bytes(total: int) -> None:
    """The size half of body reading — split out so the ceiling check has
    its own name and can be tested/reused independently of streaming."""
    if total > MAX_BODY_BYTES:
        raise PayloadTooLarge(f"payload exceeds {MAX_BODY_BYTES // (1024 * 1024)} MiB limit")


async def read_capped(request: Request) -> bytes:
    """Stream the body while counting bytes, aborting the moment the running
    total exceeds MAX_BODY_BYTES — before an oversized body is ever fully
    buffered. Content-Length is never consulted: it is absent under chunked
    encoding and is client-controlled either way."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        _enforce_max_body_bytes(total)
        chunks.append(chunk)
    return b"".join(chunks)
