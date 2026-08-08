from fastapi import FastAPI
from fastapi.testclient import TestClient
from reimbursement.list.params import LimitQuery, OffsetQuery, StatusQuery, parse_status_filter


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/params")
    def _echo(limit: LimitQuery = 100, offset: OffsetQuery = 0, status: StatusQuery = None) -> dict:
        return {"limit": limit, "offset": offset, "status": status}

    return app


class DescribeListQueryDefaults:
    def it_defaults_limit_and_offset_when_both_are_omitted(self) -> None:
        client = TestClient(_build_app())

        response = client.get("/params")

        assert response.json() == {"limit": 100, "offset": 0, "status": None}


class DescribeParseStatusFilter:
    def it_splits_a_comma_separated_value_into_a_list(self) -> None:
        assert parse_status_filter("human-review,auto-rejected") == ["human-review", "auto-rejected"]

    def it_returns_none_for_an_empty_string(self) -> None:
        assert parse_status_filter("") is None

    def it_returns_none_when_absent(self) -> None:
        assert parse_status_filter(None) is None
