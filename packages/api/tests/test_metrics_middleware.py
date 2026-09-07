from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from api.metrics import UNMATCHED_PATH_LABEL, api_http_request_duration_seconds, api_http_requests_total
from api.middleware import MetricsMiddleware

pytestmark = pytest.mark.anyio

Scope = dict[str, Any]
Message = dict[str, Any]


class _FakeRoute:
    def __init__(self, path: str) -> None:
        self.path = path


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


class _RecordingSend:
    def __init__(self) -> None:
        self.messages: list[Message] = []

    async def __call__(self, message: Message) -> None:
        self.messages.append(message)


def _http_scope(method: str = "GET") -> Scope:
    return {"type": "http", "method": method}


def _counter_value(method: str, path: str, status_code: str) -> float:
    return api_http_requests_total.labels(method, path, status_code)._value.get()


def _histogram_count(method: str, path: str) -> float:
    # prometheus_client stores each bucket's own (non-cumulative) count
    # internally — cumulative summing happens at collect()/exposition time —
    # so summing every bucket gives the total observation count, since each
    # observation increments exactly one bucket.
    child = api_http_request_duration_seconds.labels(method, path)
    return sum(bucket.get() for bucket in child._buckets)


def _app_setting_route(path: str, status: int = 200) -> Callable[[Scope, Callable, Callable], Awaitable[None]]:
    async def app(scope: Scope, receive: Callable, send: Callable) -> None:
        scope["route"] = _FakeRoute(path)
        await send({"type": "http.response.start", "status": status, "headers": []})

    return app


class DescribeMetricsMiddleware:
    async def it_records_the_route_template_path_not_a_resolved_url(self) -> None:
        before = _counter_value("GET", "/api/v1/reimbursement/{uuid}", "200")

        await MetricsMiddleware(_app_setting_route("/api/v1/reimbursement/{uuid}"))(
            _http_scope(), _receive, _RecordingSend()
        )

        after = _counter_value("GET", "/api/v1/reimbursement/{uuid}", "200")
        assert after == before + 1

    async def it_records_the_status_code_from_the_response_start_message(self) -> None:
        before = _counter_value("GET", "/api/v1/reimbursement", "201")

        await MetricsMiddleware(_app_setting_route("/api/v1/reimbursement", status=201))(
            _http_scope(), _receive, _RecordingSend()
        )

        after = _counter_value("GET", "/api/v1/reimbursement", "201")
        assert after == before + 1

    async def it_records_a_positive_duration(self) -> None:
        before = _histogram_count("GET", "/api/v1/reimbursement")

        await MetricsMiddleware(_app_setting_route("/api/v1/reimbursement"))(
            _http_scope(), _receive, _RecordingSend()
        )

        after = _histogram_count("GET", "/api/v1/reimbursement")
        assert after == before + 1

    async def it_records_a_404_with_the_unmatched_path_label(self) -> None:
        async def not_found_app(scope: Scope, receive: Callable, send: Callable) -> None:
            # No scope["route"] is ever set — Starlette never populates it
            # for a path that matches no registered route.
            await send({"type": "http.response.start", "status": 404, "headers": []})

        before = _counter_value("GET", UNMATCHED_PATH_LABEL, "404")

        await MetricsMiddleware(not_found_app)(_http_scope(), _receive, _RecordingSend())

        after = _counter_value("GET", UNMATCHED_PATH_LABEL, "404")
        assert after == before + 1

    async def it_still_records_the_request_when_the_downstream_app_raises(self) -> None:
        async def failing_app(scope: Scope, receive: Callable, send: Callable) -> None:
            raise RuntimeError("boom")

        before = _counter_value("GET", UNMATCHED_PATH_LABEL, "500")

        with pytest.raises(RuntimeError, match="boom"):
            await MetricsMiddleware(failing_app)(_http_scope(), _receive, _RecordingSend())

        after = _counter_value("GET", UNMATCHED_PATH_LABEL, "500")
        assert after == before + 1

    async def it_increments_both_metrics_exactly_once_per_request(self) -> None:
        counter_before = _counter_value("POST", "/api/v1/reimbursement", "200")
        histogram_before = _histogram_count("POST", "/api/v1/reimbursement")

        await MetricsMiddleware(_app_setting_route("/api/v1/reimbursement"))(
            _http_scope("POST"), _receive, _RecordingSend()
        )

        assert _counter_value("POST", "/api/v1/reimbursement", "200") == counter_before + 1
        assert _histogram_count("POST", "/api/v1/reimbursement") == histogram_before + 1

    async def it_passes_non_http_scopes_through_untouched(self) -> None:
        calls: list[str] = []

        async def app(scope: Scope, receive: Callable, send: Callable) -> None:
            calls.append(scope["type"])

        await MetricsMiddleware(app)({"type": "lifespan"}, _receive, _RecordingSend())

        assert calls == ["lifespan"]
