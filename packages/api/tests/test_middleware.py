import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import pytest
from shared.logging import get_correlation_id

from api.middleware import CorrelationIdMiddleware

pytestmark = pytest.mark.anyio

Scope = dict[str, Any]
Message = dict[str, Any]
App = Callable[[Scope, Callable[[], Awaitable[Message]], Callable[[Message], Awaitable[None]]], Awaitable[None]]


@pytest.fixture(autouse=True)
def _assert_no_leaked_correlation_id():
    yield
    assert get_correlation_id() is None


def _http_scope(headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    return {"type": "http", "headers": headers or []}


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


class _RecordingSend:
    def __init__(self) -> None:
        self.messages: list[Message] = []

    async def __call__(self, message: Message) -> None:
        self.messages.append(message)


async def _ok_app(scope: Scope, receive: Callable, send: Callable) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})


class DescribeCorrelationIdMiddleware:
    async def it_generates_a_fresh_uuid7_when_the_header_is_absent(self) -> None:
        seen: dict[str, str | None] = {}

        async def app(scope: Scope, receive: Callable, send: Callable) -> None:
            seen["cid"] = get_correlation_id()
            await _ok_app(scope, receive, send)

        await CorrelationIdMiddleware(app)(_http_scope(), _receive, _RecordingSend())

        assert seen["cid"] is not None
        assert UUID(seen["cid"]).version == 7

    async def it_echoes_the_inbound_x_request_id_header(self) -> None:
        seen: dict[str, str | None] = {}

        async def app(scope: Scope, receive: Callable, send: Callable) -> None:
            seen["cid"] = get_correlation_id()
            await _ok_app(scope, receive, send)

        await CorrelationIdMiddleware(app)(
            _http_scope([(b"x-request-id", b"caller-supplied-id")]), _receive, _RecordingSend()
        )

        assert seen["cid"] == "caller-supplied-id"

    async def it_treats_an_empty_header_value_as_absent_and_generates_a_fresh_one(self) -> None:
        seen: dict[str, str | None] = {}

        async def app(scope: Scope, receive: Callable, send: Callable) -> None:
            seen["cid"] = get_correlation_id()
            await _ok_app(scope, receive, send)

        await CorrelationIdMiddleware(app)(
            _http_scope([(b"x-request-id", b"")]), _receive, _RecordingSend()
        )

        assert seen["cid"] not in (None, "")
        UUID(seen["cid"])

    async def it_injects_the_correlation_id_as_the_x_request_id_response_header(self) -> None:
        send = _RecordingSend()

        await CorrelationIdMiddleware(_ok_app)(
            _http_scope([(b"x-request-id", b"my-id")]), _receive, send
        )

        start = next(m for m in send.messages if m["type"] == "http.response.start")
        assert (b"x-request-id", b"my-id") in start["headers"]

    async def it_resets_the_contextvar_after_the_request_completes(self) -> None:
        await CorrelationIdMiddleware(_ok_app)(
            _http_scope([(b"x-request-id", b"my-id")]), _receive, _RecordingSend()
        )

        assert get_correlation_id() is None

    async def it_resets_the_contextvar_even_when_the_downstream_app_raises(self) -> None:
        async def failing_app(scope: Scope, receive: Callable, send: Callable) -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await CorrelationIdMiddleware(failing_app)(
                _http_scope([(b"x-request-id", b"my-id")]), _receive, _RecordingSend()
            )

        assert get_correlation_id() is None

    async def it_never_leaks_one_concurrent_requests_id_into_anothers_scope(self) -> None:
        seen: dict[str, str | None] = {}

        def _app_for(name: str) -> App:
            async def app(scope: Scope, receive: Callable, send: Callable) -> None:
                await asyncio.sleep(0)
                seen[name] = get_correlation_id()
                await _ok_app(scope, receive, send)

            return app

        await asyncio.gather(
            CorrelationIdMiddleware(_app_for("a"))(
                _http_scope([(b"x-request-id", b"req-a")]), _receive, _RecordingSend()
            ),
            CorrelationIdMiddleware(_app_for("b"))(
                _http_scope([(b"x-request-id", b"req-b")]), _receive, _RecordingSend()
            ),
        )

        assert seen == {"a": "req-a", "b": "req-b"}

    async def it_passes_non_http_scopes_through_untouched(self) -> None:
        calls: list[str] = []

        async def app(scope: Scope, receive: Callable, send: Callable) -> None:
            calls.append(scope["type"])

        await CorrelationIdMiddleware(app)({"type": "lifespan"}, _receive, _RecordingSend())

        assert calls == ["lifespan"]
