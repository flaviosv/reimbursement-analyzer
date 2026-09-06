import asyncio

import pytest
from shared.logging import get_correlation_id, reset_correlation_id, set_correlation_id

pytestmark = pytest.mark.anyio


class DescribeContextVar:
    def it_returns_none_when_nothing_has_been_set(self) -> None:
        assert get_correlation_id() is None

    def it_returns_the_value_just_set(self) -> None:
        token = set_correlation_id("corr-1")
        try:
            assert get_correlation_id() == "corr-1"
        finally:
            reset_correlation_id(token)

    def it_restores_the_previous_value_on_reset(self) -> None:
        outer_token = set_correlation_id("outer")
        inner_token = set_correlation_id("inner")

        reset_correlation_id(inner_token)

        assert get_correlation_id() == "outer"
        reset_correlation_id(outer_token)

    def it_restores_none_when_reset_after_the_first_set(self) -> None:
        token = set_correlation_id("only-one")

        reset_correlation_id(token)

        assert get_correlation_id() is None

    def it_accepts_none_as_a_valid_value(self) -> None:
        # A consumer processing a pre-feature message with no correlation_id
        # to carry — None is a legitimate, common value, not an error state.
        token = set_correlation_id("something")
        try:
            set_correlation_id(None)
            assert get_correlation_id() is None
        finally:
            reset_correlation_id(token)

    async def it_never_leaks_one_asyncio_tasks_value_into_another(self) -> None:
        seen: dict[str, str | None] = {}

        async def _run(name: str, value: str) -> None:
            token = set_correlation_id(value)
            try:
                await asyncio.sleep(0)
                seen[name] = get_correlation_id()
            finally:
                reset_correlation_id(token)

        await asyncio.gather(_run("a", "req-a"), _run("b", "req-b"))

        assert seen == {"a": "req-a", "b": "req-b"}
