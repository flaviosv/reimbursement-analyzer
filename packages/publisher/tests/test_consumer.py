import asyncio
import signal
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import publisher.consumer as consumer_module
import pytest
from opentelemetry import propagate
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from publisher.config import PublisherConfig, load_publisher_config
from publisher.consumer import _install_signal_handlers, check_startup_config, managed_consumer, run
from fakes import FakePool, FakeProducer
from shared.testing import ThreadSafeAsyncEvent, in_memory_tracer, valid_reimbursement_item
from shared.tracing import inject_headers
from publisher.processing import Dependencies
from shared.config import REIMBURSEMENT_TOPIC, REQUEST_TOPIC, Config, load_config
from shared.models import RequestEnvelope

pytestmark = pytest.mark.anyio


class FakeMessage:
    def __init__(
        self,
        value: bytes = b"",
        error: object | None = None,
        *,
        headers: list[tuple[str, bytes]] | None = None,
        topic: str = REQUEST_TOPIC,
        partition: int = 0,
        offset: int = 0,
    ) -> None:
        self._value = value
        self._error = error
        self._headers = headers
        self._topic = topic
        self._partition = partition
        self._offset = offset

    def value(self) -> bytes:
        return self._value

    def error(self) -> object | None:
        return self._error

    def headers(self) -> list[tuple[str, bytes]] | None:
        return self._headers

    def topic(self) -> str | None:
        return self._topic

    def partition(self) -> int | None:
        return self._partition

    def offset(self) -> int | None:
        return self._offset


class FakeConsumer:
    """Yields one batch per consume() call, then nothing. Carries no poll()
    at all, so a loop reaching for it fails loudly rather than silently
    changing the offset semantics."""

    def __init__(
        self,
        batches: list[list[FakeMessage]],
        *,
        stopping: asyncio.Event | None = None,
        stop_after: int = 1,
        events: list[str] | None = None,
    ) -> None:
        self.batches = list(batches)
        self.stopping = stopping
        self.stop_after = stop_after
        self.events = events if events is not None else []
        self.consume_kwargs: list[dict[str, Any]] = []
        self.commits: list[tuple[FakeMessage, bool]] = []
        self.subscribed: list[list[str]] = []
        self.closed = False

    async def subscribe(self, topics: list[str]) -> None:
        self.subscribed.append(topics)

    async def consume(self, **kwargs: Any) -> list[FakeMessage]:
        self.consume_kwargs.append(kwargs)
        await asyncio.sleep(0)
        if self.stopping is not None and len(self.consume_kwargs) >= self.stop_after:
            self.stopping.set()
        return self.batches.pop(0) if self.batches else []

    async def commit(self, message: FakeMessage, asynchronous: bool) -> None:
        self.events.append("commit")
        self.commits.append((message, asynchronous))

    async def close(self) -> None:
        self.closed = True


class RecordingProducer(FakeProducer):
    """A FakeProducer that also appends to a shared timeline, so the ordering
    of item publishes against the offset commit is observable."""

    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events
        original_produce = self._producer.produce

        def _recording_produce(*, topic: str, value: bytes | None = None, **kwargs: object) -> None:
            original_produce(topic=topic, value=value, **kwargs)
            self.events.append("item")

        self._producer.produce = _recording_produce


class BlockingProducer(FakeProducer):
    """Suspends inside the publish so a message can be interrupted mid-flight.
    `produce()` now runs on the executor's worker thread (per
    `shared.producer.publish`'s sync produce/flush path) — bounded at 5s so a
    cancelled test never leaves an orphaned thread blocked forever."""

    def __init__(self) -> None:
        super().__init__()
        self.started = ThreadSafeAsyncEvent()
        self.release = ThreadSafeAsyncEvent()

        def _blocking_produce(*, topic: str, value: bytes | None = None, on_delivery: Any = None, **kwargs: object) -> None:
            self.started.set()
            self.release.wait_sync(timeout=5)
            if on_delivery is not None:
                on_delivery(None, object())

        self._producer.produce = _blocking_produce


class SignallingProducer(FakeProducer):
    """Delivers a real SIGTERM while the first item is mid-publish, then waits
    for the handler to fire — deterministic proof the message is provably
    still in flight when shutdown is requested. `produce()` runs on the
    executor's worker thread (per `shared.producer.publish`'s sync
    produce/flush path), so `stopping` (an `asyncio.Event`) is awaited via
    `run_coroutine_threadsafe` back onto the loop that owns it, rather than
    busy-polled from this thread."""

    def __init__(self, stopping: asyncio.Event) -> None:
        super().__init__()
        self.stopping = stopping
        self.raised = False
        self._loop = asyncio.get_running_loop()
        original_produce = self._producer.produce

        def _signalling_produce(*, topic: str, value: bytes | None = None, **kwargs: object) -> None:
            if not self.raised:
                self.raised = True
                signal.raise_signal(signal.SIGTERM)
                try:
                    asyncio.run_coroutine_threadsafe(self.stopping.wait(), self._loop).result(timeout=2.0)
                except TimeoutError:
                    pass
            original_produce(topic=topic, value=value, **kwargs)

        self._producer.produce = _signalling_produce


@contextmanager
def _armed_signal_handlers(stopping: asyncio.Event) -> Iterator[None]:
    """Arm the real handlers, parking SIGTERM on a no-op first: with the
    handlers absent the default disposition kills the test runner instead of
    failing the assertion."""
    previous = signal.signal(signal.SIGTERM, lambda *_: None)
    _install_signal_handlers(stopping)
    try:
        yield
    finally:
        loop = asyncio.get_running_loop()
        loop.remove_signal_handler(signal.SIGTERM)
        loop.remove_signal_handler(signal.SIGINT)
        signal.signal(signal.SIGTERM, previous)


def _deps(
    pool: Any,
    producer: Any,
    config: Config | None = None,
    publisher: PublisherConfig | None = None,
) -> Dependencies:
    return Dependencies(
        config=config or load_config(),
        publisher=publisher or load_publisher_config(),
        pool=pool,
        producer=producer,
    )


def _message(request_ids: list[str], *, retry: int = 0) -> FakeMessage:
    envelope = RequestEnvelope(
        retry=retry,
        published_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        payload=[valid_reimbursement_item(request_id) for request_id in request_ids],
    )
    return FakeMessage(envelope.model_dump_json().encode())


def _with_dsn(config: Config, dsn: str | None) -> Config:
    return replace(config, database=replace(config.database, dsn=dsn))


class DescribeTheStartupCheck:
    def it_refuses_to_boot_when_the_pool_cannot_cover_the_item_concurrency(self) -> None:
        config = _with_dsn(load_config(), "postgresql://localhost/x")
        starved = replace(config, database=replace(config.database, pool_max_size=4))
        publisher = replace(load_publisher_config(), item_concurrency=10)

        with pytest.raises(RuntimeError) as caught:
            check_startup_config(starved, publisher)

        assert "4" in str(caught.value)
        assert "10" in str(caught.value)

    def it_refuses_to_boot_when_the_database_url_is_unset(self) -> None:
        with pytest.raises(RuntimeError) as caught:
            check_startup_config(_with_dsn(load_config(), None), load_publisher_config())

        assert "DATABASE_URL" in str(caught.value)

    def it_boots_on_the_shipped_configuration(self) -> None:
        config = load_config()
        publisher = load_publisher_config()

        assert config.database.pool_max_size >= publisher.item_concurrency
        assert check_startup_config(_with_dsn(config, "postgresql://localhost/x"), publisher) is None

    def it_refuses_to_boot_when_env_raises_concurrency_past_the_pool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The guard this class exercises was previously unfailable in any
        # deployment: pool_max_size and item_concurrency were both hardcoded
        # literals, so the RuntimeError branch was dead code. Both are
        # env-driven now — this proves the guard can actually fire.
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        monkeypatch.setenv("DATABASE_POOL_MAX_SIZE", "5")
        monkeypatch.setenv("PUBLISHER_ITEM_CONCURRENCY", "10")
        load_config.cache_clear()
        load_publisher_config.cache_clear()

        with pytest.raises(RuntimeError) as caught:
            check_startup_config(load_config(), load_publisher_config())

        assert "5" in str(caught.value)
        assert "10" in str(caught.value)


class DescribeTheConsumerLifecycle:
    async def it_constructs_the_consumer_inside_the_running_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # AIOConsumer.__init__ calls asyncio.get_event_loop(); built at import
        # time it would bind its callbacks to a loop that never runs.
        bound: list[asyncio.AbstractEventLoop] = []

        class LoopRecordingConsumer(FakeConsumer):
            def __init__(self, config: dict[str, Any]) -> None:
                super().__init__([])
                bound.append(asyncio.get_event_loop())

        monkeypatch.setattr(consumer_module, "AIOConsumer", LoopRecordingConsumer)

        async with managed_consumer(load_config(), load_publisher_config()):
            pass

        assert bound == [asyncio.get_running_loop()]

    async def it_subscribes_to_the_request_topic_and_closes_on_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built = FakeConsumer([])
        monkeypatch.setattr(consumer_module, "AIOConsumer", lambda config: built)

        async with managed_consumer(load_config(), load_publisher_config()) as opened:
            assert opened is built
            assert built.subscribed == [[REQUEST_TOPIC]]
            assert built.closed is False

        assert built.closed is True


class DescribeTheLoop:
    async def it_consumes_one_message_at_a_time(self) -> None:
        stopping = asyncio.Event()
        consumer = FakeConsumer([[_message(["REQ-1"])]], stopping=stopping, stop_after=2)

        await run(_deps(FakePool(), FakeProducer()), consumer, stopping)

        timeout = load_publisher_config().consume_timeout_seconds
        assert consumer.consume_kwargs[0] == {"num_messages": 1, "timeout": timeout}

    async def it_commits_the_offset_exactly_once_for_a_handled_message(self) -> None:
        stopping = asyncio.Event()
        message = _message(["REQ-1", "REQ-2", "REQ-3"])
        consumer = FakeConsumer([[message]], stopping=stopping, stop_after=2)
        pool = FakePool()

        await run(_deps(pool, FakeProducer()), consumer, stopping)

        assert len(pool.inserted) == 3
        assert [committed for committed, _ in consumer.commits] == [message]

    async def it_commits_no_offset_until_every_item_has_settled(self) -> None:
        stopping = asyncio.Event()
        events: list[str] = []
        consumer = FakeConsumer(
            [[_message(["REQ-1", "REQ-2", "REQ-3"])]],
            stopping=stopping,
            stop_after=2,
            events=events,
        )

        await run(_deps(FakePool(), RecordingProducer(events)), consumer, stopping)

        assert events == ["item", "item", "item", "commit"]

    async def it_commits_synchronously_so_the_offset_is_durable_before_the_next_message(
        self,
    ) -> None:
        stopping = asyncio.Event()
        consumer = FakeConsumer([[_message(["REQ-1"])]], stopping=stopping, stop_after=2)

        await run(_deps(FakePool(), FakeProducer()), consumer, stopping)

        assert [asynchronous for _, asynchronous in consumer.commits] == [False]

    async def it_commits_nothing_when_no_message_was_available(self) -> None:
        stopping = asyncio.Event()
        consumer = FakeConsumer([[]], stopping=stopping, stop_after=2)
        pool = FakePool()

        await run(_deps(pool, FakeProducer()), consumer, stopping)

        assert consumer.commits == []
        assert pool.acquisitions == 0

    async def it_skips_a_broker_error_message_without_committing_it(self) -> None:
        stopping = asyncio.Event()
        consumer = FakeConsumer(
            [[FakeMessage(error="broker: partition eof")]], stopping=stopping, stop_after=2
        )
        pool = FakePool()

        await run(_deps(pool, FakeProducer()), consumer, stopping)

        assert consumer.commits == []
        assert pool.acquisitions == 0

    async def it_keeps_consuming_after_a_message_it_could_not_parse(self) -> None:
        stopping = asyncio.Event()
        good = _message(["REQ-AFTER-BAD"])
        consumer = FakeConsumer(
            [[FakeMessage(b"this is not json")], [good]], stopping=stopping, stop_after=3
        )
        pool, producer = FakePool(), FakeProducer()

        await run(_deps(pool, producer), consumer, stopping)

        assert [row[0] for row in pool.inserted] == ["REQ-AFTER-BAD"]
        assert len(producer.messages(REIMBURSEMENT_TOPIC)) == 1
        # Both offsets advance: an unparseable message is handled, not retried
        # forever, or it would block every request behind it.
        assert len(consumer.commits) == 2

    async def it_lets_the_message_in_hand_finish_and_commit_before_it_stops(self) -> None:
        stopping = asyncio.Event()
        message = _message(["REQ-LAST"])
        consumer = FakeConsumer([[message]], stopping=stopping, stop_after=1)
        pool = FakePool()

        await run(_deps(pool, FakeProducer()), consumer, stopping)

        # The signal arrives during the very first consume, yet that message
        # still settles and commits before the loop exits.
        assert stopping.is_set()
        assert [row[0] for row in pool.inserted] == ["REQ-LAST"]
        assert [committed for committed, _ in consumer.commits] == [message]
        assert len(consumer.consume_kwargs) == 1

    async def it_stops_on_a_real_sigterm_only_after_the_message_in_hand_commits(self) -> None:
        stopping = asyncio.Event()
        message = _message(["REQ-SIGTERM"])
        # A second batch and a stop_after well past it: if the signal never
        # armed anything, the loop keeps going and the assertions below say so
        # instead of hanging.
        consumer = FakeConsumer(
            [[message], [_message(["REQ-NEVER"])]], stopping=stopping, stop_after=3
        )
        pool, producer = FakePool(), SignallingProducer(stopping)

        with _armed_signal_handlers(stopping):
            await run(_deps(pool, producer), consumer, stopping)

        assert stopping.is_set()
        assert [row[0] for row in pool.inserted] == ["REQ-SIGTERM"]
        assert [committed for committed, _ in consumer.commits] == [message]
        assert len(consumer.consume_kwargs) == 1

    async def it_leaves_an_interrupted_messages_offset_uncommitted(self) -> None:
        stopping = asyncio.Event()
        consumer = FakeConsumer([[_message(["REQ-INTERRUPTED"])]], stopping=stopping, stop_after=2)
        producer = BlockingProducer()
        task = asyncio.create_task(run(_deps(FakePool(), producer), consumer, stopping))

        await producer.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert consumer.commits == []

    async def it_keeps_consuming_after_a_null_valued_record(self) -> None:
        # A tombstone/null-value record used to crash the loop permanently:
        # handle_message called raw.decode() on None.
        stopping = asyncio.Event()
        good = _message(["REQ-AFTER-NULL"])
        consumer = FakeConsumer(
            [[FakeMessage(value=None)], [good]], stopping=stopping, stop_after=3
        )
        pool = FakePool()

        await run(_deps(pool, FakeProducer()), consumer, stopping)

        assert [row[0] for row in pool.inserted] == ["REQ-AFTER-NULL"]
        assert len(consumer.commits) == 2

    async def it_survives_handle_message_violating_its_never_raises_contract(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Defence in depth: handle_message is documented never to raise
        # (PUB-32), but nothing enforced that before — a violation would have
        # taken the whole consume loop down with it.
        stopping = asyncio.Event()
        consumer = FakeConsumer([[_message(["REQ-1"])], [_message(["REQ-2"])]], stopping=stopping, stop_after=3)

        async def _raises(deps: Any, raw: Any) -> list[Any]:
            raise RuntimeError("handle_message broke its contract")

        monkeypatch.setattr(consumer_module, "handle_message", _raises)

        await run(_deps(FakePool(), FakeProducer()), consumer, stopping)

        assert len(consumer.commits) == 2


class DescribeTracedMessageProcessing:
    async def it_wraps_handling_in_a_process_message_span(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tracer, exporter = in_memory_tracer()
        monkeypatch.setattr(consumer_module, "tracer", tracer)
        stopping = asyncio.Event()
        consumer = FakeConsumer([[_message(["REQ-1"])]], stopping=stopping, stop_after=2)

        await run(_deps(FakePool(), FakeProducer()), consumer, stopping)

        finished = exporter.get_finished_spans()
        assert [span.name for span in finished] == ["process_message"]

    async def it_stamps_topic_partition_and_offset_as_span_attributes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tracer, exporter = in_memory_tracer()
        monkeypatch.setattr(consumer_module, "tracer", tracer)
        stopping = asyncio.Event()
        message = _message(["REQ-1"])
        message._topic = REQUEST_TOPIC
        message._partition = 2
        message._offset = 17
        consumer = FakeConsumer([[message]], stopping=stopping, stop_after=2)

        await run(_deps(FakePool(), FakeProducer()), consumer, stopping)

        span = exporter.get_finished_spans()[0]
        assert span.attributes["messaging.kafka.topic"] == REQUEST_TOPIC
        assert span.attributes["messaging.kafka.partition"] == 2
        assert span.attributes["messaging.kafka.offset"] == 17

    async def it_stamps_both_kafka_attributes_and_reimbursement_uuid_on_the_same_span(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The other tests in this class drive the real run() loop but only
        # ever check one attribute family; this proves both actually land on
        # the one finished span in the real running flow, not two separately
        # hand-constructed spans that happen to each assert their own half.
        tracer, exporter = in_memory_tracer()
        monkeypatch.setattr(consumer_module, "tracer", tracer)
        stopping = asyncio.Event()
        message = _message(["REQ-SAME-SPAN"])
        message._topic = REQUEST_TOPIC
        message._partition = 4
        message._offset = 21
        consumer = FakeConsumer([[message]], stopping=stopping, stop_after=2)
        producer = FakeProducer()

        await run(_deps(FakePool(), producer), consumer, stopping)

        span = exporter.get_finished_spans()[0]
        published_uuid = producer.messages(REIMBURSEMENT_TOPIC)[0]["uuid"]
        assert span.attributes["messaging.kafka.topic"] == REQUEST_TOPIC
        assert span.attributes["messaging.kafka.partition"] == 4
        assert span.attributes["messaging.kafka.offset"] == 21
        assert span.attributes["reimbursement.uuid"] == published_uuid

    async def it_starts_a_child_span_when_the_message_carries_trace_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        propagate.set_global_textmap(TraceContextTextMapPropagator())
        tracer, exporter = in_memory_tracer()
        monkeypatch.setattr(consumer_module, "tracer", tracer)

        with tracer.start_as_current_span("producer-span") as producer_span:
            headers = inject_headers()
            expected_trace_id = producer_span.get_span_context().trace_id

        message = _message(["REQ-1"])
        message._headers = headers
        stopping = asyncio.Event()
        consumer = FakeConsumer([[message]], stopping=stopping, stop_after=2)

        await run(_deps(FakePool(), FakeProducer()), consumer, stopping)

        process_span = next(s for s in exporter.get_finished_spans() if s.name == "process_message")
        assert process_span.context.trace_id == expected_trace_id

    async def it_starts_a_new_root_span_when_the_message_carries_no_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tracer, exporter = in_memory_tracer()
        monkeypatch.setattr(consumer_module, "tracer", tracer)
        stopping = asyncio.Event()
        consumer = FakeConsumer([[_message(["REQ-1"])]], stopping=stopping, stop_after=2)

        await run(_deps(FakePool(), FakeProducer()), consumer, stopping)

        process_span = exporter.get_finished_spans()[0]
        assert process_span.parent is None


class DescribeServe:
    async def it_initializes_and_shuts_down_a_tracer_provider_with_the_publisher_service_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Mocks out every real-I/O dependency `_serve()` owns (DB pool, Kafka
        # producer/consumer, the consume loop itself — each already covered
        # on its own elsewhere in this file) so it can run to completion once,
        # leaving only the tracer lifecycle unmocked and observable.
        monkeypatch.setattr(consumer_module, "check_startup_config", lambda config, publisher: None)

        @asynccontextmanager
        async def _fake_managed_pool(config):
            yield FakePool()

        @asynccontextmanager
        async def _fake_managed_producer(config, **kwargs):
            yield FakeProducer()

        @asynccontextmanager
        async def _fake_managed_consumer(config, publisher):
            yield object()

        async def _fake_run(deps, consumer, stopping):
            return None

        monkeypatch.setattr(consumer_module, "managed_pool", _fake_managed_pool)
        monkeypatch.setattr(consumer_module, "managed_producer", _fake_managed_producer)
        monkeypatch.setattr(consumer_module, "managed_consumer", _fake_managed_consumer)
        monkeypatch.setattr(consumer_module, "run", _fake_run)

        shutdown_calls: list[object] = []

        async def _fake_shutdown(provider: object) -> None:
            shutdown_calls.append(provider)

        monkeypatch.setattr(consumer_module, "shutdown_tracer_async", _fake_shutdown)

        await consumer_module._serve()

        assert len(shutdown_calls) == 1
        assert shutdown_calls[0].resource.attributes["service.name"] == "reimbursement-analyzer-publisher"
