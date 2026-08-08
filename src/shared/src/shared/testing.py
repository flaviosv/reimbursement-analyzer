"""Test doubles for the contracts `shared` itself defines, importable by any
service's own test suite via a normal package import — not bare-name
pythonpath resolution, so there is nothing to collide with.

Production code never imports this module.
"""

import asyncio
import json
from typing import Any


class FakeProducer:
    """Stands in for `confluent_kafka.aio.AIOProducer`'s `produce()`
    contract, the one `shared.producer.publish` and every caller depend on.
    Records every produced message. `errors` maps a topic to the exception
    its delivery future carries, so a delivery failure can be injected
    independently per topic."""

    def __init__(self, *, errors: dict[str, Exception] | None = None) -> None:
        self.errors = errors or {}
        self.produced: list[tuple[str, bytes]] = []

    async def produce(self, topic: str, value: bytes, **kwargs: object) -> asyncio.Future:
        await asyncio.sleep(0)
        self.produced.append((topic, value))
        future = asyncio.get_running_loop().create_future()
        error = self.errors.get(topic)
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(object())
        return future

    def messages(self, topic: str) -> list[dict[str, Any]]:
        return [json.loads(value) for produced, value in self.produced if produced == topic]
