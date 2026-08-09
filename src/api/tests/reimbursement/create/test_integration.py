import json
import time

import pytest
from confluent_kafka import Consumer
from fastapi.testclient import TestClient
from helpers import valid_reimbursement_item
from shared.config import MAX_BODY_BYTES, REQUEST_TOPIC

from main import app

pytestmark = pytest.mark.integration


def _padded_batch(request_id: str, target_bytes: int) -> bytes:
    item = valid_reimbursement_item(request_id, padding="")
    base_length = len(json.dumps([item]))
    item["padding"] = "x" * (target_bytes - base_length)
    result = json.dumps([item]).encode()
    assert len(result) == target_bytes
    return result


def _post(app_client: TestClient, raw: bytes) -> None:
    response = app_client.post(
        "/api/v1/reimbursement",
        content=raw,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 201


def _consume_matching(bootstrap_server: str, marker: str, timeout: float) -> bytes:
    """The topic is shared across every test in this session, and only ever
    auto-created by an actual produce (a bare metadata lookup does not
    trigger it) -- so this drains from the beginning with a fresh group each
    time and picks out the one message carrying this test's own marker,
    rather than trying to time a watermark against topic creation."""
    marker_bytes = marker.encode()
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap_server,
            "group.id": f"integration-test-{time.monotonic_ns()}",
            "auto.offset.reset": "earliest",
        }
    )
    consumer.subscribe([REQUEST_TOPIC])
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                raise AssertionError(f"consumer error: {msg.error()}")
            value = msg.value()
            if marker_bytes in value:
                return value
        raise AssertionError(f"no message carrying {marker!r} on {REQUEST_TOPIC} within {timeout}s")
    finally:
        consumer.close()


class DescribeRealBrokerRoundTrip:
    def it_accepts_a_normal_sized_batch_through_the_real_broker(
        self, kafka_bootstrap_server: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", kafka_bootstrap_server)
        request_id = "REQ-INTEGRATION-NORMAL"
        raw = json.dumps([valid_reimbursement_item(request_id)]).encode()

        with TestClient(app) as client:
            _post(client, raw)

        delivered = _consume_matching(kafka_bootstrap_server, request_id, timeout=30.0)
        published = json.loads(delivered)
        assert published["payload"] == json.loads(raw)
        assert published["errors"] == []

    def it_publishes_a_ceiling_sized_batch_and_consumes_it_back_byte_identical(
        self, kafka_bootstrap_server: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", kafka_bootstrap_server)
        request_id = "REQ-INTEGRATION-CEILING"
        raw = _padded_batch(request_id, MAX_BODY_BYTES)

        with TestClient(app) as client:
            _post(client, raw)

        delivered = _consume_matching(kafka_bootstrap_server, request_id, timeout=60.0)
        payload_start = delivered.index(b'"payload":') + len(b'"payload":')
        assert delivered[payload_start:-1] == raw
