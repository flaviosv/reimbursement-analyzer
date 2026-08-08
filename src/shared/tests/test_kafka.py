import asyncio

import pytest
from shared.errors import PublishFailed
from shared.kafka import managed_producer, publish

pytestmark = pytest.mark.anyio


class _ImmediateFakeProducer:
    """Every produce() call returns an already-resolved (or already-failed)
    future, so awaiting it never actually suspends."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.produced: list[tuple[str, bytes]] = []

    async def produce(self, topic: str, value: bytes | None = None, **kwargs: object):
        self.produced.append((topic, value))
        future = asyncio.get_running_loop().create_future()
        if self.error is not None:
            future.set_exception(self.error)
        else:
            future.set_result(object())
        return future


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


class DescribePublish:
    async def it_resolves_when_the_broker_acknowledges_delivery(self) -> None:
        fake = _ImmediateFakeProducer()

        result = await publish(fake, "some-topic", b"payload", timeout_seconds=5)

        assert result is None
        assert fake.produced == [("some-topic", b"payload")]

    async def it_raises_publish_failed_on_a_broker_delivery_error(self) -> None:
        fake = _ImmediateFakeProducer(error=RuntimeError("Local: Message timed out"))

        with pytest.raises(PublishFailed, match="RuntimeError: Local: Message timed out"):
            await publish(fake, "some-topic", b"payload", timeout_seconds=5)

    async def it_raises_publish_failed_on_a_timeout(self) -> None:
        fake = _NeverResolvesFakeProducer()

        with pytest.raises(PublishFailed, match="TimeoutError"):
            await publish(fake, "some-topic", b"payload", timeout_seconds=0.05)

    async def it_gives_each_concurrent_publish_its_own_verdict_despite_out_of_order_resolution(
        self,
    ) -> None:
        # Call 0 is produced first but resolves LAST, as a failure. Call 1
        # is produced second but resolves FIRST, as a success. A
        # shared-queue bug (the flush()-based design this replaced) would
        # let one call's outcome leak into the other's.
        fake = _OutOfOrderFakeProducer(resolutions=[RuntimeError("late failure"), None])

        results = await asyncio.gather(
            publish(fake, "topic-a", b"[]", timeout_seconds=5),
            publish(fake, "topic-b", b"[]", timeout_seconds=5),
            return_exceptions=True,
        )

        assert isinstance(results[0], PublishFailed)
        assert results[1] is None


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
