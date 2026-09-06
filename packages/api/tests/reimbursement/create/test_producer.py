import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from shared.config import KafkaConfig
from shared.errors import PublishFailed
from shared.logging import reset_correlation_id, set_correlation_id
from shared.models import RequestEnvelope

from api.reimbursement.create.producer import build_envelope, publish

pytestmark = pytest.mark.anyio

SAMPLE_JSON_PATH = Path(__file__).resolve().parents[5] / "docs" / "original" / "sample.json"


class _NeverResolvesFakeProducer:
    async def produce(self, topic: str, value: bytes | None = None, **kwargs: object):
        return asyncio.get_running_loop().create_future()


class DescribeBuildEnvelope:
    def it_reconstructs_the_exact_wire_format_bytes(self) -> None:
        raw = b'[{"request_id":"REQ-1"}]'
        published_at = datetime(2026, 4, 10, 9, 15, 0, tzinfo=UTC)

        envelope = build_envelope(raw, published_at, None)

        stamp = published_at.isoformat().encode()
        expected = (
            b'{"retry":0,"published_at":"'
            + stamp
            + b'","correlation_id":null,"errors":[],"payload":'
            + raw
            + b"}"
        )
        assert envelope == expected

    def it_publishes_a_payload_byte_for_byte_identical_to_sample_json(self) -> None:
        raw = SAMPLE_JSON_PATH.read_bytes()

        envelope = build_envelope(raw, datetime.now(UTC), None)

        # Byte-identical means literally: the payload slice, sitting between
        # the known prefix and the closing brace, equals the input exactly —
        # not "parses to the same structure after re-serialisation".
        assert envelope.endswith(raw + b"}")
        payload_start = envelope.index(b'"payload":') + len(b'"payload":')
        assert envelope[payload_start:-1] == raw

    def it_sets_retry_to_zero(self) -> None:
        envelope = build_envelope(b"[]", datetime.now(UTC), None)

        model = RequestEnvelope.model_validate_json(envelope)

        assert model.retry == 0

    def it_stamps_an_aware_utc_published_at(self) -> None:
        published_at = datetime(2026, 4, 10, 9, 15, 0, tzinfo=UTC)

        envelope = build_envelope(b"[]", published_at, None)
        model = RequestEnvelope.model_validate_json(envelope)

        assert model.published_at == published_at
        assert model.published_at.tzinfo is not None

    def it_starts_the_error_history_empty(self) -> None:
        envelope = build_envelope(b"[]", datetime.now(UTC), None)

        model = RequestEnvelope.model_validate_json(envelope)

        assert model.errors == []

    def it_parses_cleanly_and_the_payload_matches_the_input_structurally(self) -> None:
        raw = SAMPLE_JSON_PATH.read_bytes()

        envelope = build_envelope(raw, datetime.now(UTC), None)
        model = RequestEnvelope.model_validate_json(envelope)

        assert model.payload == json.loads(raw)

    def it_serializes_a_none_correlation_id_as_the_bare_json_null(self) -> None:
        envelope = build_envelope(b"[]", datetime.now(UTC), None)

        model = RequestEnvelope.model_validate_json(envelope)

        assert model.correlation_id is None
        assert b'"correlation_id":null' in envelope

    def it_serializes_a_string_correlation_id_as_a_quoted_json_string(self) -> None:
        envelope = build_envelope(b"[]", datetime.now(UTC), "corr-abc-123")

        model = RequestEnvelope.model_validate_json(envelope)

        assert model.correlation_id == "corr-abc-123"
        assert b'"correlation_id":"corr-abc-123"' in envelope


class DescribePublishCorrelationId:
    async def it_forwards_the_current_correlation_id_into_the_published_envelope(
        self, immediate_fake_producer_class, kafka_config: KafkaConfig
    ) -> None:
        fake = immediate_fake_producer_class()
        token = set_correlation_id("corr-forwarded")
        try:
            await publish(fake, b"[]", ["REQ-1"], kafka_config)
        finally:
            reset_correlation_id(token)

        published = json.loads(fake.produced[0])
        assert published["correlation_id"] == "corr-forwarded"

    async def it_omits_the_correlation_id_when_none_is_set(
        self, immediate_fake_producer_class, kafka_config: KafkaConfig
    ) -> None:
        fake = immediate_fake_producer_class()

        await publish(fake, b"[]", ["REQ-1"], kafka_config)

        published = json.loads(fake.produced[0])
        assert published["correlation_id"] is None

    async def it_never_mixes_up_two_concurrent_publishes_correlation_ids(
        self, immediate_fake_producer_class, kafka_config: KafkaConfig
    ) -> None:
        fake_a, fake_b = immediate_fake_producer_class(), immediate_fake_producer_class()

        async def _publish_with(fake, value: str) -> None:
            token = set_correlation_id(value)
            try:
                await asyncio.sleep(0)
                await publish(fake, b"[]", ["REQ-X"], kafka_config)
            finally:
                reset_correlation_id(token)

        await asyncio.gather(_publish_with(fake_a, "corr-a"), _publish_with(fake_b, "corr-b"))

        assert json.loads(fake_a.produced[0])["correlation_id"] == "corr-a"
        assert json.loads(fake_b.produced[0])["correlation_id"] == "corr-b"


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

        with pytest.raises(PublishFailed, match="RuntimeError: Local: Message timed out"):
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
