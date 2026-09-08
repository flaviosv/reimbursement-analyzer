"""Raw ASGI middleware classes used by the app's main factory, avoiding
`BaseHTTPMiddleware`/`@app.middleware("http")` due to Starlette's documented
edge cases with ContextVar propagation and streaming responses.

- `CorrelationIdMiddleware`: Establishes the request's `correlation_id` for
  the full span, never leaking one request's correlation_id onto another's logs.
- `MetricsMiddleware`: Records HTTP RED metrics (`requests_total`,
  `request_duration_seconds`) for every request."""

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from shared.logging import reset_correlation_id, set_correlation_id

from api.metrics import UNMATCHED_PATH_LABEL, api_http_request_duration_seconds, api_http_requests_total

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

_HEADER_NAME = b"x-request-id"
_KNOWN_METHODS = frozenset(("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"))


def _extract_or_generate_correlation_id(scope: Scope) -> str:
    _MAX_CORRELATION_ID_LENGTH = 128

    for name, value in scope.get("headers", []):
        if name.lower() == _HEADER_NAME:
            decoded = value.decode("latin-1")
            if decoded:
                decoded = decoded[:_MAX_CORRELATION_ID_LENGTH]
                if "\r" not in decoded and "\n" not in decoded:
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
                headers[:] = [(n, v) for n, v in headers if n.lower() != _HEADER_NAME]
                headers.append((_HEADER_NAME, correlation_id.encode("latin-1")))
            await send(message)

        token = set_correlation_id(correlation_id)
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            reset_correlation_id(token)


class MetricsMiddleware:
    """Records `api_http_requests_total`/`api_http_request_duration_seconds`
    for every HTTP request, success or error, with a route-template `path`
    label — never the resolved URL containing a real path-parameter value.

    A raw ASGI middleware class, matching `CorrelationIdMiddleware`'s own
    shape above (constructor + `send_wrapper` intercepting
    `http.response.start`), for the same reason: avoids
    `BaseHTTPMiddleware`'s ContextVar/streaming edge cases."""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"] if scope["method"] in _KNOWN_METHODS else UNMATCHED_PATH_LABEL
        # Defensive fallback: `errors.py`'s `add_exception_handler(Exception,
        # ...)` catch-all means nearly every response, including unhandled
        # exceptions, already produces a clean `http.response.start` before
        # reaching this middleware — this default only fires on a path this
        # feature did not introduce and did not change.
        status_holder = {"status_code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status_code"] = message["status"]
            await send(message)

        start = time.monotonic()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.monotonic() - start
            # scope["route"] is populated by Starlette's router *inside* the
            # await self.app(...) call above, on this same mutable scope
            # dict — the standard mechanism starlette-exporter/
            # prometheus-fastapi-instrumentator both rely on for
            # path-template extraction.
            route = scope.get("route")
            path = route.path if route is not None else UNMATCHED_PATH_LABEL
            status_code = str(status_holder["status_code"])
            api_http_requests_total.labels(method, path, status_code).inc()
            api_http_request_duration_seconds.labels(method, path).observe(duration)
