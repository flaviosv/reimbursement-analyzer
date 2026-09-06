"""Establishes the request's `correlation_id` for the full span of the
request — before the route handler runs, and before any error handler that
might run instead of it.

A raw ASGI middleware class, not `BaseHTTPMiddleware`/`@app.middleware
("http")`: Starlette's `BaseHTTPMiddleware` has a documented history of
subtle `ContextVar` propagation edge cases around its internal task-group/
streaming-response handling, which this feature's CORR-06 (never leak one
request's correlation_id onto another's logs) cannot risk. This class sets
the ContextVar directly in the same coroutine that awaits the downstream
app, with no intermediate task-group hop."""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from shared.logging import reset_correlation_id, set_correlation_id

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

_HEADER_NAME = b"x-request-id"


def _extract_or_generate_correlation_id(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name.lower() == _HEADER_NAME:
            decoded = value.decode("latin-1")
            if decoded:
                return decoded
            break
    return str(uuid.uuid7())


class CorrelationIdMiddleware:
    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        correlation_id = _extract_or_generate_correlation_id(scope)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((_HEADER_NAME, correlation_id.encode("latin-1")))
            await send(message)

        token = set_correlation_id(correlation_id)
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            reset_correlation_id(token)
