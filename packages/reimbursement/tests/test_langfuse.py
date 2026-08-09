import sys
from typing import Any

import pytest
import reimbursement.agent.agent as agent
from langfuse.langchain import CallbackHandler
from shared.config import FailureLogConfig, load_config


@pytest.fixture(autouse=True)
def _cleared_cache() -> None:
    agent._langfuse_handlers.cache_clear()


class DescribeLangfuseHandlers:
    def it_returns_a_callback_handler_when_langfuse_is_installed_and_reachable(self) -> None:
        result = agent._langfuse_handlers()

        assert len(result) == 1
        assert isinstance(result[0], CallbackHandler)

    def it_builds_the_handler_only_once_across_repeated_calls(self) -> None:
        first = agent._langfuse_handlers()
        second = agent._langfuse_handlers()

        assert first is second

    def it_returns_no_handlers_when_langfuse_is_not_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # sys.modules[name] = None is the standard way to make Python raise
        # ImportError on `from <name> import ...`, simulating the package
        # genuinely being absent even though it's a real dependency here.
        monkeypatch.setitem(sys.modules, "langfuse.langchain", None)

        result = agent._langfuse_handlers()

        assert result == []

    def it_writes_a_failure_log_record_when_langfuse_is_not_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "langfuse.langchain", None)
        calls: list[tuple[FailureLogConfig, dict[str, Any]]] = []
        monkeypatch.setattr(
            agent.failure_log, "write", lambda config, record: calls.append((config, record))
        )

        agent._langfuse_handlers()

        assert len(calls) == 1
        config, record = calls[0]
        assert config == load_config().failure_log
        assert record["event"] == agent.LANGFUSE_FALLBACK_EVENT
        assert record["reason"] == "langfuse not installed"

    def it_returns_no_handlers_when_the_langfuse_handler_is_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raising_constructor(*args: object, **kwargs: object) -> None:
            raise RuntimeError("connection refused")

        monkeypatch.setattr("langfuse.langchain.CallbackHandler", _raising_constructor)

        result = agent._langfuse_handlers()

        assert result == []

    def it_writes_a_failure_log_record_when_the_langfuse_handler_is_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raising_constructor(*args: object, **kwargs: object) -> None:
            raise RuntimeError("connection refused")

        monkeypatch.setattr("langfuse.langchain.CallbackHandler", _raising_constructor)
        calls: list[tuple[FailureLogConfig, dict[str, Any]]] = []
        monkeypatch.setattr(
            agent.failure_log, "write", lambda config, record: calls.append((config, record))
        )

        agent._langfuse_handlers()

        assert len(calls) == 1
        config, record = calls[0]
        assert config == load_config().failure_log
        assert record["event"] == agent.LANGFUSE_FALLBACK_EVENT
        assert record["reason"] == "langfuse handler unavailable"
        assert record["error"] == "connection refused"
