import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from shared.errors import PublishFailed
from shared.models import RequestEnvelope

from reimbursement.create.producer import (
    PUBLISH_TIMEOUT_SECONDS,
    build_envelope,
    publish,
)

pytestmark = pytest.mark.anyio

SAMPLE_JSON_PATH = Path(__file__).resolve().parents[5] / "docs" / "original" / "sample.json"


class _NeverResolvesFakeProducer:
    async def produce(self, topic: str, value: bytes | None = None, **kwargs: object):
        return asyncio.get_running_loop().create_future()


class _OutOfOrderFakeProducer:
    """Two produce() calls, each returning its own future — call 0's future
    is only resolved once call 1's has already been observed resolved.
    Deterministic ordering via an Event, not wall-clock sleeps: no margin to
    compress or invert under CI scheduler jitter."""

    def __init__(self, resolutions: list[Exception | None]) -> None:
        self._resolutions = resolutions
        self.produced: list[bytes] = []
        self._call_1_done = asyncio.Event()

    async def produce(self, topic: str, value: bytes | None = None, **kwargs: object):
        index = len(self.produced)
        self.produced.append(value)
        error = self._resolutions[index]
        future = asyncio.get_running_loop().create_future()

        async def _resolve() -> None:
            if index == 0:
                await self._call_1_done.wait()
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(object())
            if index == 1:
                self._call_1_done.set()

        asyncio.ensure_future(_resolve())
        return future


class DescribeBuildEnvelope:
    def it_reconstructs_the_exact_wire_format_bytes(self) -> None:
        raw = b'[{"request_id":"REQ-1"}]'
        published_at = datetime(2026, 4, 10, 9, 15, 0, tzinfo=UTC)

        envelope = build_envelope(raw, published_at)

        stamp = published_at.isoformat().encode()
        expected = b'{"retry":0,"published_at":"' + stamp + b'","payload":' + raw + b"}"
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

    def it_parses_cleanly_and_the_payload_matches_the_input_structurally(self) -> None:
        raw = SAMPLE_JSON_PATH.read_bytes()

        envelope = build_envelope(raw, datetime.now(UTC))
        model = RequestEnvelope.model_validate_json(envelope)

        assert model.payload == json.loads(raw)


class DescribeTimeoutOrdering:
    def it_keeps_the_broker_timeout_below_the_asyncio_timeout(self) -> None:
        # shared.config.MESSAGE_TIMEOUT_MS < PUBLISH_TIMEOUT_SECONDS*1000:
        # librdkafka must always fail first, so a 500 means "definitely not
        # delivered", never "not delivered yet" (the phantom-failure risk).
        from shared.config import MESSAGE_TIMEOUT_MS

        assert MESSAGE_TIMEOUT_MS / 1000 < PUBLISH_TIMEOUT_SECONDS


class DescribePublish:
    async def it_resolves_when_the_broker_acknowledges_delivery(
        self, immediate_fake_producer_class
    ) -> None:
        fake = immediate_fake_producer_class()

        result = await publish(fake, b"[]", ["REQ-1"])

        assert result is None
        assert len(fake.produced) == 1

    async def it_raises_publish_failed_on_a_broker_delivery_error(
        self, immediate_fake_producer_class
    ) -> None:
        fake = immediate_fake_producer_class(error=RuntimeError("Local: Message timed out"))

        with pytest.raises(PublishFailed, match="RuntimeError: Local: Message timed out"):
            await publish(fake, b"[]", ["REQ-1"])

    async def it_raises_publish_failed_on_a_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "reimbursement.create.producer.PUBLISH_TIMEOUT_SECONDS", 0.05
        )
        fake = _NeverResolvesFakeProducer()

        with pytest.raises(PublishFailed, match="TimeoutError"):
            await publish(fake, b"[]", ["REQ-1"])

    async def it_gives_each_concurrent_publish_its_own_verdict_despite_out_of_order_resolution(
        self,
    ) -> None:
        # Call 0 (REQ-A) is produced first but resolves LAST, as a failure.
        # Call 1 (REQ-B) is produced second but resolves FIRST, as a success.
        # A shared-queue bug (the flush()-based design this replaced) would
        # let one request's outcome leak into the other's.
        fake = _OutOfOrderFakeProducer(resolutions=[RuntimeError("late failure"), None])

        results = await asyncio.gather(
            publish(fake, b"[]", ["REQ-A"]),
            publish(fake, b"[]", ["REQ-B"]),
            return_exceptions=True,
        )

        assert isinstance(results[0], PublishFailed)
        assert results[1] is None

    async def it_logs_request_ids_and_the_broker_error_but_never_the_payload_body(
        self, caplog: pytest.LogCaptureFixture, immediate_fake_producer_class
    ) -> None:
        caplog.set_level(logging.ERROR, logger="reimbursement.create.producer")
        fake = immediate_fake_producer_class(error=RuntimeError("simulated broker failure"))
        payload_marker = b'{"secret_marker": "PAYLOAD-SECRET-XYZ"}'

        with pytest.raises(PublishFailed):
            await publish(fake, payload_marker, ["REQ-SECRET-ID"])

        assert "REQ-SECRET-ID" in caplog.text
        assert "simulated broker failure" in caplog.text
        assert "PAYLOAD-SECRET-XYZ" not in caplog.text
