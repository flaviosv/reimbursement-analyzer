import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import confluent_kafka
import pytest
from opentelemetry import propagate
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from shared.errors import PublishFailed
from shared.producer import managed_producer, publish

pytestmark = pytest.mark.anyio


class _FakeAIOProducer:
    """Minimal double satisfying `publish()`'s two attribute reads:
    `.executor` (a real `ThreadPoolExecutor` — `publish()` genuinely
    schedules blocking work onto it) and `._producer` (the fake sync
    producer below, modelling `confluent_kafka.Producer`'s synchronous
    `produce()`/`flush()` pair)."""

    def __init__(self, sync_producer: Any, max_workers: int = 4) -> None:
        self._producer = sync_producer
        self.executor = ThreadPoolExecutor(max_workers=max_workers)


class _ImmediateFakeSyncProducer:
    """produce() then flush() resolves the message immediately — either
    successfully, or with the given delivery error."""

    def __init__(self, *, error: confluent_kafka.KafkaError | None = None) -> None:
        self.error = error
        self.produced: list[tuple[str, bytes | None, list[tuple[str, bytes]] | None]] = []
        self._on_delivery: Any = None

    def produce(
        self,
        *,
        topic: str,
        value: bytes | None = None,
        headers: list[tuple[str, bytes]] | None = None,
        on_delivery: Any = None,
        **kwargs: Any,
    ) -> None:
        self.produced.append((topic, value, headers))
        self._on_delivery = on_delivery

    def flush(self, timeout: float) -> int:
        if self._on_delivery is not None:
            self._on_delivery(self.error, object())
        return 0


class _NeverResolvesFakeSyncProducer:
    """The delivery callback never fires; flush() reports the message still
    queued after the timeout elapses — models a publish that never gets a
    delivery report."""

    def produce(self, *, topic: str, value: bytes | None = None, **kwargs: Any) -> None:
        pass

    def flush(self, timeout: float) -> int:
        return 1


class _OutOfOrderFakeSyncProducer:
    """Two produce()+flush() call pairs, each dispatched to its own thread
    by the real `ThreadPoolExecutor` (matching how concurrent `publish()`
    calls are actually scheduled). Call 0 is produced first but its
    delivery is held back until call 1's has already resolved — a
    shared-state bug leaking one call's outcome into the other's would
    surface as a wrong result on one of them. `threading.local()` mirrors
    `publish()`'s own closure-per-call isolation: produce() and flush() for
    one logical call always run on the same thread."""

    def __init__(self, resolutions: list[confluent_kafka.KafkaError | None]) -> None:
        self._resolutions = resolutions
        self.produced: list[bytes | None] = []
        self._lock = threading.Lock()
        self._next_index = 0
        self._call_1_done = threading.Event()
        self._local = threading.local()

    def produce(self, *, topic: str, value: bytes | None = None, on_delivery: Any = None, **kwargs: Any) -> None:
        with self._lock:
            index = self._next_index
            self._next_index += 1
        self.produced.append(value)
        self._local.index = index
        self._local.on_delivery = on_delivery

    def flush(self, timeout: float) -> int:
        index = self._local.index
        on_delivery = self._local.on_delivery
        error = self._resolutions[index]
        if index == 0:
            self._call_1_done.wait(timeout=5)
        if on_delivery is not None:
            on_delivery(error, object())
        if index == 1:
            self._call_1_done.set()
        return 0


def _broker_error(reason: str) -> confluent_kafka.KafkaError:
    return confluent_kafka.KafkaError(confluent_kafka.KafkaError._TRANSPORT, reason=reason)


class DescribePublish:
    async def it_resolves_when_the_broker_acknowledges_delivery(self) -> None:
        sync_producer = _ImmediateFakeSyncProducer()
        fake = _FakeAIOProducer(sync_producer)

        result = await publish(fake, "some-topic", b"payload", timeout_seconds=5)

        assert result is None
        assert sync_producer.produced[0][0] == "some-topic"
        assert sync_producer.produced[0][1] == b"payload"

    async def it_raises_publish_failed_on_a_broker_delivery_error(self) -> None:
        sync_producer = _ImmediateFakeSyncProducer(error=_broker_error("Broker: Not leader for partition"))
        fake = _FakeAIOProducer(sync_producer)

        with pytest.raises(PublishFailed, match="Broker: Not leader for partition"):
            await publish(fake, "some-topic", b"payload", timeout_seconds=5)

    async def it_raises_publish_failed_on_a_timeout(self) -> None:
        fake = _FakeAIOProducer(_NeverResolvesFakeSyncProducer())

        with pytest.raises(PublishFailed, match="TimeoutError"):
            await publish(fake, "some-topic", b"payload", timeout_seconds=0.05)

    async def it_gives_each_concurrent_publish_its_own_verdict_despite_out_of_order_resolution(
        self,
    ) -> None:
        # Call 0 is produced first but resolves LAST, as a failure. Call 1
        # is produced second but resolves FIRST, as a success. A
        # shared-state bug would let one call's outcome leak into the
        # other's.
        sync_producer = _OutOfOrderFakeSyncProducer(
            resolutions=[_broker_error("late failure"), None]
        )
        fake = _FakeAIOProducer(sync_producer, max_workers=2)

        results = await asyncio.gather(
            publish(fake, "topic-a", b"[]", timeout_seconds=5),
            publish(fake, "topic-b", b"[]", timeout_seconds=5),
            return_exceptions=True,
        )

        assert isinstance(results[0], PublishFailed)
        assert results[1] is None

    async def it_injects_the_current_trace_context_as_kafka_headers(self) -> None:
        propagate.set_global_textmap(TraceContextTextMapPropagator())
        tracer = TracerProvider().get_tracer(__name__)
        sync_producer = _ImmediateFakeSyncProducer()
        fake = _FakeAIOProducer(sync_producer)

        with tracer.start_as_current_span("test-span"):
            await publish(fake, "some-topic", b"payload", timeout_seconds=5)

        _, _, headers = sync_producer.produced[0]
        assert headers is not None
        keys = {key for key, _ in headers}
        assert "traceparent" in keys


class DescribeManagedProducer:
    async def it_closes_the_producer_on_clean_exit(self) -> None:
        async with managed_producer({"bootstrap.servers": "localhost:9092"}) as producer:
            pass

        assert producer._is_closed is True

    async def it_closes_the_producer_even_when_the_body_raises(self) -> None:
        captured = None
        with pytest.raises(RuntimeError):
            async with managed_producer({"bootstrap.servers": "localhost:9092"}) as producer:
                captured = producer
                raise RuntimeError("boom")

        assert captured is not None
        assert captured._is_closed is True

    async def it_constructs_a_producer_whose_publish_path_remains_compatible_with_a_real_upgrade(
        self,
    ) -> None:
        # Guard against a future confluent-kafka upgrade silently renaming
        # or restructuring the private attributes `shared.producer.publish`
        # depends on to carry headers (design.md's Risks table). If this
        # ever fails, the coupling `publish()` relies on has broken and
        # needs a new approach, not a silent production failure.
        #
        # SPEC_DEVIATION: this constructs a real, unmocked confluent_kafka
        # Producer targeting the default localhost:9092 bootstrap server,
        # with no Docker-gated container — safe in a Docker-less run only
        # because construction and .close() never actually publish anything
        # (produce() is never called), so a refused background connection
        # (visible in stderr as librdkafka "Connect...failed") is expected
        # noise, not a test failure.
        async with managed_producer({"bootstrap.servers": "localhost:9092"}) as producer:
            assert hasattr(producer, "executor")
            assert isinstance(producer._producer, confluent_kafka.Producer)
