import builtins
import json
import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from shared import failure_log
from shared.config import FailureLogConfig
from shared.models import AttemptError

CONFIG = FailureLogConfig(logger_name="test.failures", max_message_chars=40)


def _record() -> dict[str, Any]:
    return {
        "event": "item_failed",
        "request_id": "REQ-1",
        "stage": "publish",
        "outcome": "LOGGED",
        "errors": [
            {"attempt": 1, "stage": "db-insert", "message": "connection reset"},
            {"attempt": 2, "stage": "publish", "message": "broker unreachable"},
        ],
    }


def _emitted(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        json.loads(entry.message)
        for entry in caplog.records
        if entry.name == CONFIG.logger_name and entry.levelno == logging.CRITICAL
    ]


class DescribeWrite:
    def it_emits_one_json_record_at_critical_level(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.CRITICAL, logger=CONFIG.logger_name):
            failure_log.write(CONFIG, _record())

        assert len(_emitted(caplog)) == 1

    def it_carries_the_request_id_stage_outcome_and_full_error_history(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.CRITICAL, logger=CONFIG.logger_name):
            failure_log.write(CONFIG, _record())

        emitted = _emitted(caplog)[0]
        assert emitted["request_id"] == "REQ-1"
        assert emitted["stage"] == "publish"
        assert emitted["outcome"] == "LOGGED"
        assert [entry["message"] for entry in emitted["errors"]] == [
            "connection reset",
            "broker unreachable",
        ]

    def it_truncates_a_long_value_to_the_configured_cap(self, caplog: pytest.LogCaptureFixture) -> None:
        record = _record() | {"detail": "x" * 500}

        with caplog.at_level(logging.CRITICAL, logger=CONFIG.logger_name):
            failure_log.write(CONFIG, record)

        assert _emitted(caplog)[0]["detail"] == "x" * CONFIG.max_message_chars

    def it_truncates_an_oversized_dict_key_not_only_its_values(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # extra="allow" on ReimbursementRequest means an item's field *names*
        # are as unbounded as its values — only truncating values left an
        # oversized key untouched (S8).
        record = _record() | {"y" * 500: "value"}

        with caplog.at_level(logging.CRITICAL, logger=CONFIG.logger_name):
            failure_log.write(CONFIG, record)

        keys = _emitted(caplog)[0].keys()
        assert all(len(key) <= CONFIG.max_message_chars for key in keys)
        assert "y" * CONFIG.max_message_chars in keys

    def it_truncates_nested_messages_inside_the_error_history(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        record = _record()
        record["errors"][0]["message"] = "y" * 500

        with caplog.at_level(logging.CRITICAL, logger=CONFIG.logger_name):
            failure_log.write(CONFIG, record)

        assert _emitted(caplog)[0]["errors"][0]["message"] == "y" * CONFIG.max_message_chars

    def it_returns_normally_when_the_underlying_logger_throws(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _explode(*args: object, **kwargs: object) -> None:
            raise RuntimeError("logging subsystem is down")

        monkeypatch.setattr(logging.getLogger(CONFIG.logger_name), "critical", _explode)

        assert failure_log.write(CONFIG, _record()) is None

    def it_still_leaves_a_trace_when_the_underlying_logger_throws(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # "Never raises" doesn't require "never reports" (A9/Q13) — this is
        # the bottom of every fallback chain, so silently discarding the
        # one record meant to survive everything else failing is its own
        # kind of loss. Logged to a *different* name than CONFIG.logger_name
        # deliberately: if that name's own handler is what just broke,
        # logging to it again here would recurse into the same failure.
        def _explode(*args: object, **kwargs: object) -> None:
            raise RuntimeError("logging subsystem is down")

        monkeypatch.setattr(logging.getLogger(CONFIG.logger_name), "critical", _explode)

        with caplog.at_level(logging.ERROR, logger="reimbursementanalyzer.failures.fallback"):
            failure_log.write(CONFIG, _record())

        fallback_records = [r for r in caplog.records if r.name == "reimbursementanalyzer.failures.fallback"]
        assert len(fallback_records) == 1
        assert fallback_records[0].levelno == logging.ERROR

    def it_opens_no_file_of_its_own(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Durability belongs to whatever handler ops attach to logger_name; a
        # container-local file would be destroyed by the restart it exists to
        # survive.
        def _refuse(*args: object, **kwargs: object) -> None:
            raise AssertionError("failure_log must not open a path of its own")

        monkeypatch.setattr(builtins, "open", _refuse)

        with caplog.at_level(logging.CRITICAL, logger=CONFIG.logger_name):
            failure_log.write(CONFIG, _record())

        assert len(_emitted(caplog)) == 1

    def it_does_not_recurse_past_the_configured_depth(self) -> None:
        nested: dict[str, Any] = {"value": "leaf"}
        for _ in range(50):
            nested = {"nested": nested}

        # Must not raise RecursionError — the whole point of a depth bound.
        failure_log.write(CONFIG, {"event": "deep", "payload": nested})


class DescribeBuildRecord:
    def it_carries_the_event_index_request_id_item_and_errors(self) -> None:
        error = AttemptError(
            attempt=1,
            occurred_at=datetime(2026, 4, 10, 9, 0, 0, tzinfo=UTC),
            stage="db-insert",
            error_type="RuntimeError",
            message="boom",
        )
        item = {"request_id": "REQ-1", "submitted_by": "person@example.com"}

        record = failure_log.build_record("some.event", 2, item, [error], outcome="logged")

        assert record["event"] == "some.event"
        assert record["item_index"] == 2
        assert record["request_id"] == "REQ-1"
        assert record["item"] == item
        assert record["errors"] == [
            {
                "attempt": 1,
                "occurred_at": "2026-04-10T09:00:00Z",
                "stage": "db-insert",
                "error_type": "RuntimeError",
                "message": "boom",
            }
        ]
        assert record["outcome"] == "logged"

    def it_returns_none_for_request_id_when_the_item_is_not_a_dict(self) -> None:
        record = failure_log.build_record("some.event", 0, "not-a-dict", [])

        assert record["request_id"] is None
