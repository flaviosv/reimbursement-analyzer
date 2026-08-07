from api.reimbursement.create.route import router
from api.reimbursement.create.validation import BATCH_ADAPTER
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _openapi_schema() -> dict:
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200
    return response.json()


class DescribeOpenApiDocumentation:
    def it_documents_the_route_path_and_method(self) -> None:
        schema = _openapi_schema()

        assert "/api/v1/reimbursement" in schema["paths"]
        assert "post" in schema["paths"]["/api/v1/reimbursement"]

    def it_documents_all_four_status_codes(self) -> None:
        schema = _openapi_schema()

        responses = schema["paths"]["/api/v1/reimbursement"]["post"]["responses"]
        assert set(responses.keys()) == {"201", "400", "413", "500"}

    def it_derives_the_request_body_schema_from_the_batch_adapter(self) -> None:
        schema = _openapi_schema()

        body_schema = schema["paths"]["/api/v1/reimbursement"]["post"]["requestBody"][
            "content"
        ]["application/json"]["schema"]
        assert body_schema == BATCH_ADAPTER.json_schema()
        assert body_schema["type"] == "array"
        item_ref = body_schema["items"]["$ref"].removeprefix("#/$defs/")
        assert body_schema["$defs"][item_ref]["required"] == [
            "request_id",
            "submitted_by",
            "submitted_at",
        ]
