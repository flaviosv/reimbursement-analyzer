import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from shared.config import KafkaConfig
from shared.errors import PublishFailed
from shared.models import RequestEnvelope

from api.reimbursement.create.producer import build_envelope, publish

pytestmark = pytest.mark.anyio

SAMPLE_JSON_PATH = Path(__file__).resolve().parents[5] / "docs" / "original" / "sample.json"


class _NeverResolvesFakeSyncProducer:
    """The delivery callback never fires; flush() reports the message still
    queued after the timeout elapses."""

    def produce(self, *, topic: str, value: bytes | None = None, **kwargs: object) -> None:
        pass

    def flush(self, timeout: float) -> int:
        return 1


class _NeverResolvesFakeProducer:
    def __init__(self) -> None:
        self._producer = _NeverResolvesFakeSyncProducer()
        self.executor = ThreadPoolExecutor(max_workers=4)


class DescribeBuildEnvelope:
    def it_reconstructs_the_exact_wire_format_bytes(self) -> None:
        raw = b'[{"request_id":"REQ-1"}]'
        published_at = datetime(2026, 4, 10, 9, 15, 0, tzinfo=UTC)

        envelope = build_envelope(raw, published_at)

        stamp = published_at.isoformat().encode()
        expected = b'{"retry":0,"published_at":"' + stamp + b'","errors":[],"payload":' + raw + b"}"
        assert envelope == expected

    def it_publishes_a_payload_byte_for_byte_identical_to_sample_json(self) -> None:
        raw = SAMPLE_JSON_PATH.read_bytes()

        envelope = build_envelope(raw, datetime.now(UTC))

        # Byte-identical means literally: the payload slice, sitting between
        # the known prefix and the closing brace, equals the input exactly —
        # not "parses to the same structure after re-serialisation".
        assert envelope.endswith(raw + b"}")
        payload_start = envelope.index(b'"payload":') + len(b'"payload":')
        assert envelope[payload_start:-1] == raw

    def it_sets_retry_to_zero(self) -> None:
        envelope = build_envelope(b"[]", datetime.now(UTC))

        model = RequestEnvelope.model_validate_json(envelope)

        assert model.retry == 0

    def it_stamps_an_aware_utc_published_at(self) -> None:
        published_at = datetime(2026, 4, 10, 9, 15, 0, tzinfo=UTC)

        envelope = build_envelope(b"[]", published_at)
        model = RequestEnvelope.model_validate_json(envelope)

        assert model.published_at == published_at
        assert model.published_at.tzinfo is not None

    def it_starts_the_error_history_empty(self) -> None:
        envelope = build_envelope(b"[]", datetime.now(UTC))

        model = RequestEnvelope.model_validate_json(envelope)

        assert model.errors == []

    def it_parses_cleanly_and_the_payload_matches_the_input_structurally(self) -> None:
        raw = SAMPLE_JSON_PATH.read_bytes()

        envelope = build_envelope(raw, datetime.now(UTC))
        model = RequestEnvelope.model_validate_json(envelope)

        assert model.payload == json.loads(raw)


class DescribePublish:
    async def it_resolves_when_the_broker_acknowledges_delivery(
        self, immediate_fake_producer_class, kafka_config: KafkaConfig
    ) -> None:
        fake = immediate_fake_producer_class()

        result = await publish(fake, b"[]", ["REQ-1"], kafka_config)

        assert result is None
        assert len(fake.produced) == 1

    async def it_raises_publish_failed_on_a_broker_delivery_error(
        self, immediate_fake_producer_class, kafka_config: KafkaConfig
    ) -> None:
        fake = immediate_fake_producer_class(error=RuntimeError("Local: Message timed out"))

        with pytest.raises(PublishFailed, match="Local: Message timed out"):
            await publish(fake, b"[]", ["REQ-1"], kafka_config)

    async def it_forwards_the_configured_publish_timeout(self, kafka_config: KafkaConfig) -> None:
        # The mechanism itself (asyncio.wait_for racing the delivery future)
        # is covered at shared.producer.publish's own test level — this
        # proves only that the wrapper forwards config.publish_timeout_seconds
        # rather than hardcoding its own.
        fake = _NeverResolvesFakeProducer()
        fast_timeout_config = replace(kafka_config, publish_timeout_seconds=0.05)

        with pytest.raises(PublishFailed, match="TimeoutError"):
            await publish(fake, b"[]", ["REQ-1"], fast_timeout_config)

    async def it_logs_request_ids_and_the_broker_error_but_never_the_payload_body(
        self,
        caplog: pytest.LogCaptureFixture,
        immediate_fake_producer_class,
        kafka_config: KafkaConfig,
    ) -> None:
        caplog.set_level(logging.ERROR, logger="reimbursement.create.producer")
        fake = immediate_fake_producer_class(error=RuntimeError("simulated broker failure"))
        payload_marker = b'{"secret_marker": "PAYLOAD-SECRET-XYZ"}'

        with pytest.raises(PublishFailed):
            await publish(fake, payload_marker, ["REQ-SECRET-ID"], kafka_config)

        assert "REQ-SECRET-ID" in caplog.text
        assert "simulated broker failure" in caplog.text
        assert "PAYLOAD-SECRET-XYZ" not in caplog.text
