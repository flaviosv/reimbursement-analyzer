import asyncio
import logging
from uuid import uuid4

import pytest
import shared.logging as shared_logging
from shared.logging import (
    CorrelationIdFilter,
    configure_logging,
    get_correlation_id,
    log_event,
    reset_correlation_id,
    set_correlation_id,
)

pytestmark = pytest.mark.anyio


def _record() -> logging.LogRecord:
    return logging.LogRecord("test", logging.INFO, "path", 1, "message", None, None)


@pytest.fixture(autouse=True)
def _isolate_root_logger(monkeypatch: pytest.MonkeyPatch):
    # configure_logging() mutates process-wide state (the root logger's
    # handlers/level and the module-level `_handler` sentinel) — without
    # this, one test's call would leak a StreamHandler onto every other
    # test's root logger for the rest of the run.
    monkeypatch.setattr(shared_logging, "_handler", None)
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield
    for handler in list(root.handlers):
        if handler not in saved_handlers:
            root.removeHandler(handler)
    root.setLevel(saved_level)


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


class DescribeConfigureLogging:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("debug", logging.DEBUG),
            ("DEBUG", logging.DEBUG),
            ("info", logging.INFO),
            ("INFO", logging.INFO),
            ("warning", logging.WARNING),
            ("error", logging.ERROR),
            ("critical", logging.CRITICAL),
        ],
    )
    def it_sets_the_root_logger_to_each_valid_level_case_insensitively(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: int
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", raw)

        configure_logging()

        assert logging.getLogger().level == expected

    def it_defaults_to_info_when_log_level_is_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LOG_LEVEL", raising=False)

        configure_logging()

        assert logging.getLogger().level == logging.INFO

    def it_falls_back_to_info_and_warns_once_on_an_invalid_level(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "bogus")

        with caplog.at_level(logging.WARNING):
            configure_logging()
            assert logging.getLogger().level == logging.INFO

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "bogus" in warnings[0].getMessage()

    def it_rejects_empty_log_level_string(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "")

        with caplog.at_level(logging.WARNING):
            configure_logging()
            assert logging.getLogger().level == logging.INFO

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "" in warnings[0].getMessage()

    def it_rejects_undocumented_stdlib_aliases_like_notset(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "notset")

        with caplog.at_level(logging.WARNING):
            configure_logging()
            assert logging.getLogger().level == logging.INFO

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "notset" in warnings[0].getMessage()

    def it_attaches_the_handler_only_once_across_repeated_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "info")
        root = logging.getLogger()
        before = len(root.handlers)

        configure_logging()
        configure_logging()
        configure_logging()

        assert len(root.handlers) == before + 1

    def it_re_resolves_the_level_on_every_call_so_it_can_change_mid_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from shared.config import load_config

        monkeypatch.setenv("LOG_LEVEL", "error")
        configure_logging()
        assert logging.getLogger().level == logging.ERROR

        monkeypatch.setenv("LOG_LEVEL", "info")
        load_config.cache_clear()
        configure_logging()
        assert logging.getLogger().level == logging.INFO

    def it_attaches_a_handler_carrying_the_ecs_formatter_and_the_correlation_filter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ecs_logging

        monkeypatch.setenv("LOG_LEVEL", "info")

        configure_logging()

        handler = next(
            h for h in logging.getLogger().handlers if isinstance(h.formatter, ecs_logging.StdlibFormatter)
        )
        assert any(isinstance(f, CorrelationIdFilter) for f in handler.filters)


class DescribeLogEvent:
    def it_uses_the_event_as_the_human_readable_message(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        logger = logging.getLogger("test.log_event")

        with caplog.at_level(logging.INFO, logger="test.log_event"):
            log_event(logger, logging.INFO, "some.event")

        assert caplog.records[-1].getMessage() == "some.event"

    def it_also_carries_event_as_its_own_extra_field(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        logger = logging.getLogger("test.log_event")

        with caplog.at_level(logging.INFO, logger="test.log_event"):
            log_event(logger, logging.INFO, "some.event")

        assert caplog.records[-1].event == "some.event"

    def it_passes_non_uuid_fields_through_unchanged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        logger = logging.getLogger("test.log_event")

        with caplog.at_level(logging.INFO, logger="test.log_event"):
            log_event(logger, logging.INFO, "some.event", request_id="REQ-1", count=3)

        record = caplog.records[-1]
        assert record.request_id == "REQ-1"
        assert record.count == 3

    def it_coerces_a_uuid_field_to_a_plain_string_not_its_repr(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        logger = logging.getLogger("test.log_event")
        value = uuid4()

        with caplog.at_level(logging.INFO, logger="test.log_event"):
            log_event(logger, logging.INFO, "some.event", uuid=value)

        record = caplog.records[-1]
        assert record.uuid == str(value)
        assert "UUID(" not in record.uuid

    def it_falls_back_without_raising_when_a_field_collides_with_a_reserved_attribute(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        logger = logging.getLogger("test.log_event")

        with (
            caplog.at_level(logging.INFO, logger="test.log_event"),
            caplog.at_level(logging.INFO, logger="reimbursementanalyzer.logging.fallback"),
        ):
            log_event(logger, logging.INFO, "some.event", message="collides")

        assert any(
            r.name == "reimbursementanalyzer.logging.fallback" and r.levelno == logging.ERROR
            for r in caplog.records
        )
