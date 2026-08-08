import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import consumer as consumer_module
import pytest
from consumer import check_startup_config, managed_consumer, run
from fakes import FakePool, FakeProducer
from helpers import valid_reimbursement_item
from processing import Dependencies
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


def _deps(pool: Any, producer: Any, config: Config | None = None) -> Dependencies:
    return Dependencies(config=config or load_config(), pool=pool, producer=producer)


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
        config = load_config()
        starved = replace(
            _with_dsn(config, "postgresql://localhost/x"),
            database=replace(config.database, dsn="postgresql://localhost/x", pool_max_size=4),
            publisher=replace(config.publisher, item_concurrency=10),
        )

        with pytest.raises(RuntimeError) as caught:
            check_startup_config(starved)

        assert "4" in str(caught.value)
        assert "10" in str(caught.value)

    def it_refuses_to_boot_when_the_database_url_is_unset(self) -> None:
        with pytest.raises(RuntimeError) as caught:
            check_startup_config(_with_dsn(load_config(), None))

        assert "DATABASE_URL" in str(caught.value)

    def it_boots_on_the_shipped_configuration(self) -> None:
        config = load_config()

        assert config.database.pool_max_size >= config.publisher.item_concurrency
        assert check_startup_config(_with_dsn(config, "postgresql://localhost/x")) is None


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

        async with managed_consumer(load_config()):
            pass

        assert bound == [asyncio.get_running_loop()]

    async def it_subscribes_to_the_request_topic_and_closes_on_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built = FakeConsumer([])
        monkeypatch.setattr(consumer_module, "AIOConsumer", lambda config: built)

        async with managed_consumer(load_config()) as opened:
            assert opened is built
            assert built.subscribed == [[REQUEST_TOPIC]]
            assert built.closed is False

        assert built.closed is True


class DescribeTheLoop:
    async def it_consumes_one_message_at_a_time(self) -> None:
        stopping = asyncio.Event()
        consumer = FakeConsumer([[_message(["REQ-1"])]], stopping=stopping, stop_after=2)

        await run(_deps(FakePool(), FakeProducer()), consumer, stopping)

        timeout = load_config().publisher.consume_timeout_seconds
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
