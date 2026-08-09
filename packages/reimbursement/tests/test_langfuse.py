from typing import Any

import pytest
from shared.config import FailureLogConfig, load_config

import reimbursement.agent.agent as agent


class DescribeLangfuseHandlers:
    def it_returns_no_handlers_when_langfuse_is_not_installed(self) -> None:
        # AGD-23/24: langfuse is not a project dependency (SPEC_DEVIATION
        # at agent.py), so this is the only currently-reachable branch.
        result = agent._langfuse_handlers()

        assert result == []

    def it_writes_a_failure_log_record_through_the_durable_fallback_when_langfuse_is_not_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[FailureLogConfig, dict[str, Any]]] = []
        monkeypatch.setattr(
            agent.failure_log,
            "write",
            lambda config, record: calls.append((config, record)),
        )

        agent._langfuse_handlers()

        assert len(calls) == 1
        config, record = calls[0]
        # AGD-24: the fallback mirrors the project's existing `failure_log`
        # pattern — same config, same event-keyed record shape every other
        # `failure_log.write` call site in this codebase uses.
        assert config == load_config().failure_log
        assert record["event"] == agent.LANGFUSE_FALLBACK_EVENT
        assert record["reason"] == "langfuse not installed"
