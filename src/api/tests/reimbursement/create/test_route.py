import asyncio
import json

from api.errors import register_handlers
from api.kafka import get_producer
from api.reimbursement.create.route import router
from fastapi import FastAPI
from fastapi.testclient import TestClient

VALID_ITEM = {
    "request_id": "REQ-0001",
    "submitted_by": "person@example.com",
    "submitted_at": "2026-01-01T12:00:00Z",
}


class _FakeProducer:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.produced: list[bytes] = []

    async def produce(self, topic: str, value: bytes | None = None, **kwargs: object):
        self.produced.append(value)
        future = asyncio.get_running_loop().create_future()
        if self.error is not None:
            future.set_exception(self.error)
        else:
            future.set_result(object())
        return future


def _build_client(fake: _FakeProducer) -> TestClient:
    app = FastAPI()
    register_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_producer] = lambda: fake
    return TestClient(app)


class DescribeCreateReimbursement:
    def it_returns_201_with_the_accepted_count(self) -> None:
        fake = _FakeProducer()
        client = _build_client(fake)

        response = client.post("/api/v1/reimbursement", json=[VALID_ITEM])

        assert response.status_code == 201
        assert response.json() == {"msg": "1 request(s) accepted"}

    def it_derives_the_response_message_from_the_batch_size(self) -> None:
        fake = _FakeProducer()
        client = _build_client(fake)
        items = [{**VALID_ITEM, "request_id": f"REQ-{n}"} for n in range(3)]

        response = client.post("/api/v1/reimbursement", json=items)

        assert response.json() == {"msg": "3 request(s) accepted"}

    def it_publishes_exactly_one_message_for_an_accepted_batch(self) -> None:
        fake = _FakeProducer()
        client = _build_client(fake)
        items = [{**VALID_ITEM, "request_id": f"REQ-{n}"} for n in range(3)]

        client.post("/api/v1/reimbursement", json=items)

        assert len(fake.produced) == 1

    def it_keeps_unknown_fields_in_the_published_payload(self) -> None:
        fake = _FakeProducer()
        client = _build_client(fake)
        item = {**VALID_ITEM, "claimed_amount_brl": 93.5, "raw_ocr_text": "..."}

        client.post("/api/v1/reimbursement", json=[item])

        published = json.loads(fake.produced[0])
        assert published["payload"][0]["claimed_amount_brl"] == 93.5
        assert published["payload"][0]["raw_ocr_text"] == "..."

    def it_returns_400_and_publishes_nothing_for_a_missing_field(self) -> None:
        fake = _FakeProducer()
        client = _build_client(fake)
        item = {k: v for k, v in VALID_ITEM.items() if k != "submitted_by"}

        response = client.post("/api/v1/reimbursement", json=[item])

        assert response.status_code == 400
        assert fake.produced == []

    def it_returns_400_and_publishes_nothing_for_an_empty_array(self) -> None:
        fake = _FakeProducer()
        client = _build_client(fake)

        response = client.post("/api/v1/reimbursement", json=[])

        assert response.status_code == 400
        assert fake.produced == []

    def it_returns_400_and_publishes_nothing_for_a_non_json_body(self) -> None:
        fake = _FakeProducer()
        client = _build_client(fake)

        response = client.post(
            "/api/v1/reimbursement",
            content=b"not json",
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 400
        assert fake.produced == []

    def it_returns_400_and_publishes_nothing_for_a_json_object_body(self) -> None:
        fake = _FakeProducer()
        client = _build_client(fake)

        response = client.post("/api/v1/reimbursement", json=VALID_ITEM)

        assert response.status_code == 400
        assert fake.produced == []

    def it_returns_413_and_publishes_nothing_for_an_oversized_body(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr("api.reimbursement.create.validation.MAX_BODY_BYTES", 10)
        fake = _FakeProducer()
        client = _build_client(fake)

        response = client.post("/api/v1/reimbursement", json=[VALID_ITEM])

        assert response.status_code == 413
        assert fake.produced == []

    def it_returns_500_when_the_broker_fails_to_deliver(self) -> None:
        fake = _FakeProducer(error=RuntimeError("Local: Message timed out"))
        client = _build_client(fake)

        response = client.post("/api/v1/reimbursement", json=[VALID_ITEM])

        assert response.status_code == 500
        assert response.json() == {"msg": "failed to publish request"}
