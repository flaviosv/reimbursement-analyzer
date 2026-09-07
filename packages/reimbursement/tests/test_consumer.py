import asyncio
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import reimbursement.consumer as consumer_module
import pytest
from reimbursement.config import load_agent_config
from reimbursement.consumer import check_startup_config, managed_consumer, run
from reimbursement.metrics import reimbursement_messages_consumed_total
from reimbursement.validation import Dependencies
from agent_fakes import FakePool, FakeProducer
from confluent_kafka.aio import AIOConsumer
from shared.config import REIMBURSEMENT_TOPIC, Config, load_config
from shared.models import ReimbursementEnvelope
from shared.signals import install_shutdown_handlers

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
    ) -> None:
        self.batches = list(batches)
        self.stopping = stopping
        self.stop_after = stop_after
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
        self.commits.append((message, asynchronous))

    async def close(self) -> None:
        self.closed = True


class SignallingProducer(FakeProducer):
    """Delivers a real SIGTERM while a requeue publish is mid-flight, then
    waits for the handler to fire — deterministic proof the message is
    provably still in flight when shutdown is requested."""

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


class BlockingProducer(FakeProducer):
    """Suspends inside the publish so a message can be interrupted
    mid-flight."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def produce(self, topic: str, value: bytes, **kwargs: object) -> asyncio.Future:
        self.started.set()
        await self.release.wait()
        return await super().produce(topic, value, **kwargs)


@contextmanager
def _armed_signal_handlers(stopping: asyncio.Event) -> Iterator[None]:
    """Arm the real handlers, parking SIGTERM on a no-op first: with the
    handlers absent the default disposition kills the test runner instead of
    failing the assertion. Installation happens inside the try so a failure
    during `install_shutdown_handlers` (e.g. one signal's `add_signal_handler`
    raising) still restores SIGTERM's original disposition, rather than
    leaking a corrupted one to every later test in the process."""
    previous = signal.signal(signal.SIGTERM, lambda *_: None)
    try:
        install_shutdown_handlers(stopping)
        yield
    finally:
        loop = asyncio.get_running_loop()
        loop.remove_signal_handler(signal.SIGTERM)
        loop.remove_signal_handler(signal.SIGINT)
        signal.signal(signal.SIGTERM, previous)


def _deps(pool: Any, producer: Any, config: Config | None = None) -> Dependencies:
    return Dependencies(
        config=config or load_config(), agent=load_agent_config(), pool=pool, producer=producer
    )


def _message(*, retry: int = 0) -> FakeMessage:
    envelope = ReimbursementEnvelope(
        uuid=uuid4(), retry=retry, published_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    )
    return FakeMessage(envelope.model_dump_json().encode())


def _with_dsn(config: Config, dsn: str | None) -> Config:
    return replace(config, database=replace(config.database, dsn=dsn))


class DescribeTheStartupCheck:
    def it_refuses_to_boot_when_the_database_url_is_unset(self) -> None:
        with pytest.raises(RuntimeError) as caught:
            check_startup_config(_with_dsn(load_config(), None))

        assert "DATABASE_URL" in str(caught.value)

    def it_boots_on_the_shipped_configuration(self) -> None:
        config = load_config()

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

        async with managed_consumer(load_config(), load_agent_config()):
            pass

        assert bound == [asyncio.get_running_loop()]

    async def it_subscribes_to_the_reimbursement_topic_and_closes_on_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built = FakeConsumer([])
        monkeypatch.setattr(consumer_module, "AIOConsumer", lambda config: built)

        async with managed_consumer(load_config(), load_agent_config()) as opened:
            assert opened is built
            assert built.subscribed == [[REIMBURSEMENT_TOPIC]]
            assert built.closed is False

        assert built.closed is True

    def it_is_the_real_confluent_aio_consumer_class(self) -> None:
        # Guards against the monkeypatches above masking a broken default.
        assert consumer_module.AIOConsumer is AIOConsumer


class DescribeTheLoop:
    async def it_consumes_one_message_at_a_time(self) -> None:
        stopping = asyncio.Event()
        consumer = FakeConsumer([[_message()]], stopping=stopping, stop_after=2)

        await run(_deps(FakePool(), FakeProducer()), consumer, stopping)

        timeout = load_agent_config().consume_timeout_seconds
        assert consumer.consume_kwargs[0] == {"num_messages": 1, "timeout": timeout}

    async def it_commits_the_offset_after_handling_a_message(self) -> None:
        stopping = asyncio.Event()
        message = _message()
        consumer = FakeConsumer([[message]], stopping=stopping, stop_after=2)

        await run(_deps(FakePool(), FakeProducer()), consumer, stopping)

        assert [committed for committed, _ in consumer.commits] == [message]

    async def it_commits_synchronously_so_the_offset_is_durable_before_the_next_message(
        self,
    ) -> None:
        stopping = asyncio.Event()
        consumer = FakeConsumer([[_message()]], stopping=stopping, stop_after=2)

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
        good = _message()
        consumer = FakeConsumer(
            [[FakeMessage(b"this is not json")], [good]], stopping=stopping, stop_after=3
        )

        await run(_deps(FakePool(), FakeProducer()), consumer, stopping)

        # Both offsets advance: an unparseable message is handled, not
        # retried forever, or it would block every message behind it.
        assert len(consumer.commits) == 2

    async def it_lets_the_message_in_hand_finish_and_commit_before_it_stops(self) -> None:
        stopping = asyncio.Event()
        message = _message()
        consumer = FakeConsumer([[message]], stopping=stopping, stop_after=1)

        await run(_deps(FakePool(), FakeProducer()), consumer, stopping)

        # The signal arrives during the very first consume, yet that
        # message still settles and commits before the loop exits.
        assert stopping.is_set()
        assert [committed for committed, _ in consumer.commits] == [message]
        assert len(consumer.consume_kwargs) == 1

    async def it_stops_on_a_real_sigterm_only_after_the_message_in_hand_commits(self) -> None:
        stopping = asyncio.Event()
        message = _message(retry=1)  # a get_error below forces the requeue path
        # The second batch and stop_after=3 are a hang-prevention safety net,
        # not part of the primary path: the real SIGTERM below should always
        # stop the loop after the first message settles (consume_kwargs
        # asserted at 1 below), before a second consume() is ever reached.
        # They only matter if signal delivery itself fails in some
        # environment — without them, that failure mode would hang forever
        # instead of eventually stopping via stop_after.
        consumer = FakeConsumer([[message], [_message()]], stopping=stopping, stop_after=3)
        producer = SignallingProducer(stopping)
        uuid = ReimbursementEnvelope.model_validate_json(message.value()).uuid
        pool = FakePool(get_errors={uuid: RuntimeError("db down")})

        with _armed_signal_handlers(stopping):
            await run(_deps(pool, producer), consumer, stopping)

        assert stopping.is_set()
        assert [committed for committed, _ in consumer.commits] == [message]
        assert len(consumer.consume_kwargs) == 1

    async def it_leaves_an_interrupted_messages_offset_uncommitted(self) -> None:
        # AGT-22: a termination signal mid-transaction rolls back and the
        # offset is never committed for the message being processed.
        #
        # task.cancel() here stands in for an abrupt interruption (a SIGKILL,
        # an OOM-kill) that no graceful handler can intercept -- distinct
        # from the real SIGTERM proven in the sibling test above, which
        # shows graceful signals let the in-flight message finish and commit
        # normally. This test proves the other half: if execution is halted
        # before that point, by any means, nothing partial gets committed.
        stopping = asyncio.Event()
        message = _message(retry=1)
        consumer = FakeConsumer([[message]], stopping=stopping, stop_after=2)
        producer = BlockingProducer()
        uuid = ReimbursementEnvelope.model_validate_json(message.value()).uuid
        pool = FakePool(get_errors={uuid: RuntimeError("db down")})
        task = asyncio.create_task(run(_deps(pool, producer), consumer, stopping))

        await producer.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert consumer.commits == []


class DescribeMessagesConsumedMetric:
    async def it_increments_once_per_genuinely_delivered_message(self) -> None:
        stopping = asyncio.Event()
        consumer = FakeConsumer([[_message()]], stopping=stopping, stop_after=2)
        before = reimbursement_messages_consumed_total.labels(REIMBURSEMENT_TOPIC)._value.get()

        await run(_deps(FakePool(), FakeProducer()), consumer, stopping)

        after = reimbursement_messages_consumed_total.labels(REIMBURSEMENT_TOPIC)._value.get()
        assert after == before + 1

    async def it_does_not_increment_on_a_protocol_level_error_message(self) -> None:
        stopping = asyncio.Event()
        consumer = FakeConsumer(
            [[FakeMessage(error="broker: partition eof")]], stopping=stopping, stop_after=2
        )
        before = reimbursement_messages_consumed_total.labels(REIMBURSEMENT_TOPIC)._value.get()

        await run(_deps(FakePool(), FakeProducer()), consumer, stopping)

        assert reimbursement_messages_consumed_total.labels(REIMBURSEMENT_TOPIC)._value.get() == before

    async def it_does_not_increment_when_no_message_was_available(self) -> None:
        stopping = asyncio.Event()
        consumer = FakeConsumer([[]], stopping=stopping, stop_after=2)
        before = reimbursement_messages_consumed_total.labels(REIMBURSEMENT_TOPIC)._value.get()

        await run(_deps(FakePool(), FakeProducer()), consumer, stopping)

        assert reimbursement_messages_consumed_total.labels(REIMBURSEMENT_TOPIC)._value.get() == before


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
