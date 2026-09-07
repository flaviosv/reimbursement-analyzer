import logging

import pytest
from api.metrics import (
    ALL_STATUSES,
    UNMATCHED_PATH_LABEL,
    api_http_request_duration_seconds,
    api_http_requests_total,
    reimbursement_review_wait_seconds,
    reimbursement_status_count,
    refresh_status_gauge,
)

pytestmark = pytest.mark.anyio


class _FakeAcquisition:
    def __init__(self, conn: object) -> None:
        self._conn = conn

    async def __aenter__(self) -> object:
        return self._conn

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakePool:
    def __init__(self, conn: object) -> None:
        self._conn = conn

    def acquire(self, *, timeout: float | None = None) -> _FakeAcquisition:
        return _FakeAcquisition(self._conn)


class DescribeApiHttpRequestsTotal:
    def it_is_constructed_with_method_path_status_code_labelnames(self) -> None:
        assert api_http_requests_total._labelnames == ("method", "path", "status_code")


class DescribeApiHttpRequestDurationSeconds:
    def it_is_constructed_with_method_path_labelnames(self) -> None:
        assert api_http_request_duration_seconds._labelnames == ("method", "path")

    def it_has_http_latency_shaped_buckets(self) -> None:
        assert api_http_request_duration_seconds._upper_bounds == [
            0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, float("inf"),
        ]


class DescribeReimbursementStatusCount:
    def it_is_constructed_with_a_status_labelname(self) -> None:
        assert reimbursement_status_count._labelnames == ("status",)


class DescribeReimbursementReviewWaitSeconds:
    def it_has_no_labelnames(self) -> None:
        assert reimbursement_review_wait_seconds._labelnames == ()

    def it_has_minutes_to_days_scale_buckets(self) -> None:
        assert reimbursement_review_wait_seconds._upper_bounds == [
            60, 300, 900, 3600, 14400, 43200, 86400, 259200, 604800, float("inf"),
        ]

    def it_shares_no_bucket_boundaries_with_the_http_duration_histogram(self) -> None:
        review_wait_buckets = set(reimbursement_review_wait_seconds._upper_bounds)
        http_duration_buckets = set(api_http_request_duration_seconds._upper_bounds)

        assert not (review_wait_buckets & http_duration_buckets - {float("inf")})


class DescribeUnmatchedPathLabel:
    def it_is_the_fixed_literal_unmatched(self) -> None:
        assert UNMATCHED_PATH_LABEL == "unmatched"


class DescribeAllStatuses:
    def it_lists_all_six_check_constraint_statuses(self) -> None:
        assert set(ALL_STATUSES) == {
            "pending",
            "human-review",
            "auto-approved",
            "auto-rejected",
            "human-approved",
            "human-rejected",
        }


class DescribeRefreshStatusGauge:
    @pytest.fixture(autouse=True)
    def _reset_refresh_throttle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("api.metrics._last_refreshed_at", 0.0)

    async def it_sets_the_gauge_from_the_queried_counts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_count_by_status(conn: object) -> list[dict]:
            return [{"status": "pending", "count": 3}, {"status": "auto-approved", "count": 5}]

        monkeypatch.setattr("api.metrics.repository.count_by_status", fake_count_by_status)

        await refresh_status_gauge(_FakePool(conn=object()))

        assert reimbursement_status_count.labels("pending")._value.get() == 3
        assert reimbursement_status_count.labels("auto-approved")._value.get() == 5

    async def it_zero_fills_statuses_absent_from_the_query_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_count_by_status(conn: object) -> list[dict]:
            return [{"status": "pending", "count": 1}]

        monkeypatch.setattr("api.metrics.repository.count_by_status", fake_count_by_status)

        await refresh_status_gauge(_FakePool(conn=object()))

        for status in ALL_STATUSES:
            if status != "pending":
                assert reimbursement_status_count.labels(status)._value.get() == 0

    async def it_logs_a_warning_and_returns_without_raising_on_db_failure(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def failing_count_by_status(conn: object) -> list[dict]:
            raise RuntimeError("db unreachable")

        monkeypatch.setattr("api.metrics.repository.count_by_status", failing_count_by_status)

        with caplog.at_level(logging.WARNING, logger="api.metrics"):
            await refresh_status_gauge(_FakePool(conn=object()))

        assert any(record.levelno == logging.WARNING for record in caplog.records)

    async def it_skips_the_db_query_when_called_again_within_the_min_interval(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = 0

        async def counting_count_by_status(conn: object) -> list[dict]:
            nonlocal calls
            calls += 1
            return [{"status": "pending", "count": 1}]

        monkeypatch.setattr("api.metrics.repository.count_by_status", counting_count_by_status)

        await refresh_status_gauge(_FakePool(conn=object()))
        await refresh_status_gauge(_FakePool(conn=object()))

        assert calls == 1
