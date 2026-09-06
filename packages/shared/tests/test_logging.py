import asyncio
import logging

import pytest
from shared.logging import (
    CorrelationIdFilter,
    get_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)

pytestmark = pytest.mark.anyio


def _record() -> logging.LogRecord:
    return logging.LogRecord("test", logging.INFO, "path", 1, "message", None, None)


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


class DescribeCorrelationIdFilter:
    def it_sets_the_attribute_when_a_correlation_id_is_set(self) -> None:
        token = set_correlation_id("corr-filter-1")
        try:
            record = _record()
            CorrelationIdFilter().filter(record)
            assert record.correlation_id == "corr-filter-1"
        finally:
            reset_correlation_id(token)

    def it_omits_the_attribute_entirely_when_no_correlation_id_is_set(self) -> None:
        record = _record()

        CorrelationIdFilter().filter(record)

        # Omitted, not set to None: ecs_logging only emits attributes
        # actually present on the record.
        assert not hasattr(record, "correlation_id")

    def it_always_returns_true_so_the_record_is_never_filtered_out(self) -> None:
        assert CorrelationIdFilter().filter(_record()) is True

        token = set_correlation_id("corr-filter-2")
        try:
            assert CorrelationIdFilter().filter(_record()) is True
        finally:
            reset_correlation_id(token)

    async def it_never_leaks_one_asyncio_tasks_correlation_id_onto_anothers_record(
        self,
    ) -> None:
        results: dict[str, logging.LogRecord] = {}

        async def _run(name: str, value: str) -> None:
            token = set_correlation_id(value)
            try:
                await asyncio.sleep(0)
                record = _record()
                CorrelationIdFilter().filter(record)
                results[name] = record
            finally:
                reset_correlation_id(token)

        await asyncio.gather(_run("a", "req-a"), _run("b", "req-b"))

        assert results["a"].correlation_id == "req-a"
        assert results["b"].correlation_id == "req-b"
