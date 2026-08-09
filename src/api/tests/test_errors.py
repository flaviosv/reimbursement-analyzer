from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, EmailStr
from shared.errors import (
    BatchInvalid,
    PayloadTooLarge,
    PublishFailed,
    ReimbursementFilterInvalid,
    ReimbursementNotEligible,
    ReimbursementNotFound,
    ReimbursementUuidMismatch,
    ReviewInvalid,
)

from errors import register_handlers


class _Payload(BaseModel):
    field: EmailStr


def _build_app() -> FastAPI:
    app = FastAPI()
    register_handlers(app)

    @app.get("/raise/payload-too-large")
    def _raise_payload_too_large() -> None:
        raise PayloadTooLarge("payload exceeds 25 MiB limit")

    @app.get("/raise/batch-invalid")
    def _raise_batch_invalid() -> None:
        raise BatchInvalid("item 1: submitted_by — value is not a valid email address")

    @app.get("/raise/publish-failed")
    def _raise_publish_failed() -> None:
        raise PublishFailed("Local: Message timed out")

    @app.get("/raise/reimbursement-filter-invalid")
    def _raise_reimbursement_filter_invalid() -> None:
        raise ReimbursementFilterInvalid("status: must be one of pending, approved, rejected")

    @app.get("/raise/review-invalid")
    def _raise_review_invalid() -> None:
        raise ReviewInvalid("status: must be one of approved, rejected")

    @app.get("/raise/reimbursement-not-found")
    def _raise_reimbursement_not_found() -> None:
        raise ReimbursementNotFound("no reimbursement matches the given uuid")

    @app.get("/raise/reimbursement-not-eligible")
    def _raise_reimbursement_not_eligible() -> None:
        raise ReimbursementNotEligible("reimbursement is not in a reviewable state")

    @app.get("/raise/reimbursement-uuid-mismatch")
    def _raise_reimbursement_uuid_mismatch() -> None:
        raise ReimbursementUuidMismatch("payload uuid disagrees with the path uuid")

    @app.get("/raise/http-exception")
    def _raise_http_exception() -> None:
        raise HTTPException(status_code=404, detail="not found")

    @app.get("/raise/unhandled")
    def _raise_unhandled() -> None:
        raise RuntimeError("boom")

    @app.post("/validate")
    def _validate(payload: _Payload) -> _Payload:
        return payload

    return app


class DescribeRegisterHandlers:
    def it_returns_413_with_msg_for_payload_too_large(self) -> None:
        client = TestClient(_build_app())

        response = client.get("/raise/payload-too-large")

        assert response.status_code == 413
        assert response.json() == {"msg": "payload exceeds 25 MiB limit"}

    def it_returns_400_with_msg_for_batch_invalid(self) -> None:
        client = TestClient(_build_app())

        response = client.get("/raise/batch-invalid")

        assert response.status_code == 400
        assert response.json() == {
            "msg": "item 1: submitted_by — value is not a valid email address"
        }

    def it_returns_500_for_publish_failed_without_leaking_the_broker_detail(self) -> None:
        client = TestClient(_build_app())

        response = client.get("/raise/publish-failed")

        assert response.status_code == 500
        assert response.json() == {"msg": "failed to publish request"}

    def it_returns_400_with_msg_for_reimbursement_filter_invalid(self) -> None:
        client = TestClient(_build_app())

        response = client.get("/raise/reimbursement-filter-invalid")

        assert response.status_code == 400
        assert response.json() == {"msg": "status: must be one of pending, approved, rejected"}

    def it_returns_422_with_msg_for_review_invalid(self) -> None:
        client = TestClient(_build_app())

        response = client.get("/raise/review-invalid")

        assert response.status_code == 422
        assert response.json() == {"msg": "status: must be one of approved, rejected"}

    def it_returns_404_with_msg_for_reimbursement_not_found(self) -> None:
        client = TestClient(_build_app())

        response = client.get("/raise/reimbursement-not-found")

        assert response.status_code == 404
        assert response.json() == {"msg": "no reimbursement matches the given uuid"}

    def it_returns_400_with_msg_for_reimbursement_not_eligible(self) -> None:
        client = TestClient(_build_app())

        response = client.get("/raise/reimbursement-not-eligible")

        assert response.status_code == 400
        assert response.json() == {"msg": "reimbursement is not in a reviewable state"}

    def it_returns_400_with_msg_for_reimbursement_uuid_mismatch(self) -> None:
        client = TestClient(_build_app())

        response = client.get("/raise/reimbursement-uuid-mismatch")

        assert response.status_code == 400
        assert response.json() == {"msg": "payload uuid disagrees with the path uuid"}

    def it_reshapes_a_starlette_http_exception_into_the_msg_contract(self) -> None:
        client = TestClient(_build_app())

        response = client.get("/raise/http-exception")

        assert response.status_code == 404
        assert response.json() == {"msg": "not found"}

    def it_returns_500_with_a_generic_message_for_unhandled_exceptions(self) -> None:
        # raise_server_exceptions=False: otherwise TestClient re-raises the
        # unhandled exception instead of returning the handler's response.
        client = TestClient(_build_app(), raise_server_exceptions=False)

        response = client.get("/raise/unhandled")

        assert response.status_code == 500
        assert response.json() == {"msg": "internal error"}

    def it_returns_400_not_422_for_a_request_validation_error(self) -> None:
        client = TestClient(_build_app())

        response = client.post("/validate", json={"field": "not-an-email"})

        assert response.status_code == 400
        assert "msg" in response.json()
        assert "detail" not in response.json()

    def it_never_echoes_the_offending_value_in_a_validation_error(self) -> None:
        client = TestClient(_build_app())
        secret_value = "SECRET-VALUE-9f3a"

        response = client.post("/validate", json={"field": secret_value})

        assert secret_value not in response.text
