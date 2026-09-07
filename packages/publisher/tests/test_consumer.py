import asyncio
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import publisher.consumer as consumer_module
import pytest
from publisher.config import PublisherConfig, load_publisher_config
from publisher.consumer import _install_signal_handlers, check_startup_config, managed_consumer, run
from fakes import FakePool, FakeProducer
from shared.testing import valid_reimbursement_item
from publisher.metrics import publisher_messages_consumed_total
from publisher.processing import Dependencies
from shared.config import REIMBURSEMENT_TOPIC, REQUEST_TOPIC, Config, load_config
from shared.models import RequestEnvelope

pytestmark = pytest.mark.anyio


class FakeMessage:
    def __init__(self, value: bytes = b"", error: object | None = None) -> None:
        self._value = value
        self._error = error

    def value(self) -> bytes:
        return self._value

    def error(self) -> object | None:
        return self._error


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

    async def produce(self, topic: str, value: bytes, **kwargs: object) -> asyncio.Future:
        future = await super().produce(topic, value, **kwargs)
        self.events.append("item")
        return future


class BlockingProducer(FakeProducer):
    """Suspends inside the publish so a message can be interrupted mid-flight."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def produce(self, topic: str, value: bytes, **kwargs: object) -> asyncio.Future:
        self.started.set()
        await self.release.wait()
        return await super().produce(topic, value, **kwargs)


class SignallingProducer(FakeProducer):
    """Delivers a real SIGTERM while the first item is mid-publish, then waits
    for the handler to fire. Waiting on the event rather than sleeping is what
    keeps this deterministic: the message is provably still in flight when the
    shutdown is requested."""

    def __init__(self, stopping: asyncio.Event) -> None:
        super().__init__()
        self.stopping = stopping
        self.raised = False

    async def produce(self, topic: str, value: bytes, **kwargs: object) -> asyncio.Future:
        if not self.raised:
            self.raised = True
            signal.raise_signal(signal.SIGTERM)
            await asyncio.wait_for(self.stopping.wait(), timeout=2.0)
        return await super().produce(topic, value, **kwargs)


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


class DescribeMessagesConsumedMetric:
    async def it_increments_once_per_genuinely_delivered_message(self) -> None:
        stopping = asyncio.Event()
        consumer = FakeConsumer([[_message(["REQ-1"])]], stopping=stopping, stop_after=2)
        before = publisher_messages_consumed_total.labels(REQUEST_TOPIC)._value.get()

        await run(_deps(FakePool(), FakeProducer()), consumer, stopping)

        after = publisher_messages_consumed_total.labels(REQUEST_TOPIC)._value.get()
        assert after == before + 1

    async def it_does_not_increment_on_a_protocol_level_error_message(self) -> None:
        stopping = asyncio.Event()
        consumer = FakeConsumer(
            [[FakeMessage(error="broker: partition eof")]], stopping=stopping, stop_after=2
        )
        before = publisher_messages_consumed_total.labels(REQUEST_TOPIC)._value.get()

        await run(_deps(FakePool(), FakeProducer()), consumer, stopping)

        assert publisher_messages_consumed_total.labels(REQUEST_TOPIC)._value.get() == before

    async def it_does_not_increment_when_no_message_was_available(self) -> None:
        stopping = asyncio.Event()
        consumer = FakeConsumer([[]], stopping=stopping, stop_after=2)
        before = publisher_messages_consumed_total.labels(REQUEST_TOPIC)._value.get()

        await run(_deps(FakePool(), FakeProducer()), consumer, stopping)

        assert publisher_messages_consumed_total.labels(REQUEST_TOPIC)._value.get() == before


class DescribeMain:
    def it_starts_the_metrics_server_with_the_configured_port_before_serving(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        monkeypatch.setattr(consumer_module, "start_metrics_server", lambda port: calls.append("start_metrics_server"))
        monkeypatch.setattr(consumer_module, "load_dotenv", lambda: None)
        monkeypatch.setattr(consumer_module, "configure_logging", lambda: None)

        def _fake_run(coro: Any) -> None:
            coro.close()
            calls.append("asyncio.run")

        monkeypatch.setattr(consumer_module.asyncio, "run", _fake_run)

        consumer_module.main()

        assert calls == ["start_metrics_server", "asyncio.run"]
