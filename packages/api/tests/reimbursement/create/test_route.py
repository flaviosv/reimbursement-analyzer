import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shared.testing import in_memory_tracer, valid_reimbursement_item

from api.dependencies import get_producer
from api.errors import register_handlers
from api.main import app as real_app
from api.reimbursement.create.route import router

VALID_ITEM = valid_reimbursement_item()


def _build_client(fake) -> TestClient:
    app = FastAPI()
    register_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_producer] = lambda: fake
    return TestClient(app)


class DescribeCreateReimbursement:
    def it_returns_201_with_the_accepted_count(self, immediate_fake_producer_class) -> None:
        fake = immediate_fake_producer_class()
        client = _build_client(fake)

        response = client.post("/api/v1/reimbursement", json=[VALID_ITEM])

        assert response.status_code == 201
        assert response.json() == {"msg": "1 request(s) accepted"}

    def it_derives_the_response_message_from_the_batch_size(
        self, immediate_fake_producer_class
    ) -> None:
        fake = immediate_fake_producer_class()
        client = _build_client(fake)
        items = [{**VALID_ITEM, "request_id": f"REQ-{n}"} for n in range(3)]

        response = client.post("/api/v1/reimbursement", json=items)

        assert response.json() == {"msg": "3 request(s) accepted"}

    def it_publishes_exactly_one_message_for_an_accepted_batch(
        self, immediate_fake_producer_class
    ) -> None:
        fake = immediate_fake_producer_class()
        client = _build_client(fake)
        items = [{**VALID_ITEM, "request_id": f"REQ-{n}"} for n in range(3)]

        client.post("/api/v1/reimbursement", json=items)

        assert len(fake.produced) == 1

    def it_keeps_unknown_fields_in_the_published_payload(
        self, immediate_fake_producer_class
    ) -> None:
        fake = immediate_fake_producer_class()
        client = _build_client(fake)
        item = {**VALID_ITEM, "claimed_amount_brl": 93.5, "raw_ocr_text": "..."}

        client.post("/api/v1/reimbursement", json=[item])

        published = json.loads(fake.produced[0])
        assert published["payload"][0]["claimed_amount_brl"] == 93.5
        assert published["payload"][0]["raw_ocr_text"] == "..."

    def it_forwards_an_items_own_retry_and_payload_keys_untouched(
        self, immediate_fake_producer_class
    ) -> None:
        # Named in the spec as a plausible collision bug: these field names
        # also exist one level up, on the envelope itself.
        fake = immediate_fake_producer_class()
        client = _build_client(fake)
        item = {**VALID_ITEM, "retry": 99, "payload": "x"}

        client.post("/api/v1/reimbursement", json=[item])

        published = json.loads(fake.produced[0])
        assert published["payload"][0]["retry"] == 99
        assert published["payload"][0]["payload"] == "x"

    def it_accepts_valid_json_sent_with_a_non_json_content_type(
        self, immediate_fake_producer_class
    ) -> None:
        # The spec explicitly requires this: a Content-Type mismatch is not
        # itself a rejection reason — only the body's actual JSON-ness is.
        fake = immediate_fake_producer_class()
        client = _build_client(fake)

        response = client.post(
            "/api/v1/reimbursement",
            content=json.dumps([VALID_ITEM]).encode(),
            headers={"content-type": "text/plain"},
        )

        assert response.status_code == 201

    @pytest.mark.parametrize(
        "request_kwargs",
        [
            {"json": [{k: v for k, v in VALID_ITEM.items() if k != "submitted_by"}]},
            {"json": []},
            {"content": b"not json", "headers": {"content-type": "application/json"}},
            {"json": VALID_ITEM},
        ],
        ids=["missing_field", "empty_array", "non_json_body", "json_object_instead_of_array"],
    )
    def it_returns_400_and_publishes_nothing(
        self, immediate_fake_producer_class, request_kwargs: dict
    ) -> None:
        fake = immediate_fake_producer_class()
        client = _build_client(fake)

        response = client.post("/api/v1/reimbursement", **request_kwargs)

        assert response.status_code == 400
        assert "msg" in response.json()
        assert fake.produced == []

    def it_returns_413_and_publishes_nothing_for_an_oversized_body(
        self, immediate_fake_producer_class, monkeypatch
    ) -> None:
        monkeypatch.setattr("api.reimbursement.create.payload.MAX_BODY_BYTES", 10)
        fake = immediate_fake_producer_class()
        client = _build_client(fake)

        response = client.post("/api/v1/reimbursement", json=[VALID_ITEM])

        assert response.status_code == 413
        assert fake.produced == []

    def it_returns_500_when_the_broker_fails_to_deliver(self, immediate_fake_producer_class) -> None:
        fake = immediate_fake_producer_class(error=RuntimeError("Local: Message timed out"))
        client = _build_client(fake)

        response = client.post("/api/v1/reimbursement", json=[VALID_ITEM])

        assert response.status_code == 500
        assert response.json() == {"msg": "failed to publish request"}

    def it_stamps_correlation_id_on_the_current_span_when_available(
        self, immediate_fake_producer_class, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tracer, exporter = in_memory_tracer()
        fake = immediate_fake_producer_class()
        client = _build_client(fake)
        monkeypatch.setattr(
            "api.reimbursement.create.route.get_correlation_id", lambda: "req-post-corr-id"
        )

        with tracer.start_as_current_span("test-span"):
            response = client.post("/api/v1/reimbursement", json=[VALID_ITEM])

        assert response.status_code == 201
        span = exporter.get_finished_spans()[0]
        assert span.attributes["correlation_id"] == "req-post-corr-id"

    def it_does_not_stamp_a_span_attribute_when_no_correlation_id_is_available(
        self, immediate_fake_producer_class, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tracer, exporter = in_memory_tracer()
        fake = immediate_fake_producer_class()
        client = _build_client(fake)
        monkeypatch.setattr("api.reimbursement.create.route.get_correlation_id", lambda: None)

        with tracer.start_as_current_span("test-span"):
            response = client.post("/api/v1/reimbursement", json=[VALID_ITEM])

        assert response.status_code == 201
        span = exporter.get_finished_spans()[0]
        assert "correlation_id" not in span.attributes


class DescribeTheRealApp:
    def it_returns_400_through_the_actual_app_object(self, immediate_fake_producer_class) -> None:
        # Every other test in this file builds its own throwaway FastAPI() +
        # register_handlers() + router. This one proves the real api.main.app
        # — lifespan, register_handlers, and router wired together exactly
        # as production runs it — also returns the app-wide error contract.
        fake = immediate_fake_producer_class()
        real_app.dependency_overrides[get_producer] = lambda: fake
        try:
            with TestClient(real_app) as client:
                response = client.post("/api/v1/reimbursement", json=[])
        finally:
            real_app.dependency_overrides.clear()

        assert response.status_code == 400
        assert "msg" in response.json()
        assert fake.produced == []
