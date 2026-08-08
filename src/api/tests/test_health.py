from fastapi.testclient import TestClient

from main import app


class DescribeHealth:
    def it_reports_ok(self) -> None:
        with TestClient(app) as client:
            response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
