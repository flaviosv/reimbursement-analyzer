"""The bottom of every fallback chain — one structured JSON record per
failure nothing else could handle."""

import json
import logging
from typing import Any

from shared.config import FailureLogConfig
from shared.models import AttemptError

_MAX_DEPTH = 10
_FALLBACK_LOGGER_NAME = "reimbursementanalyzer.failures.fallback"


def build_record(
    event: str,
    index: int,
    item: Any,
    errors: list[AttemptError],
    **extra: Any,
) -> dict[str, Any]:
    """The shape every last-resort record shares.

    Moved here from the publisher (A6, 2026-08-08): AD-025's justification
    for putting `failure_log` in `shared` is that both the publisher and
    the future Agent must write to it — leaving the record *shape* defined
    in the publisher would have made the Agent reinvent this schema
    independently the first time it needed to escalate something.
    """
    return {
        "event": event,
        "item_index": index,
        "request_id": item.get("request_id") if isinstance(item, dict) else None,
        "item": item,
        "errors": [error.model_dump(mode="json") for error in errors],
        **extra,
    }


def _truncated(value: Any, limit: int, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        return "<max depth exceeded>"
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, dict):
        # Keys truncated too (S8): `extra="allow"` on ReimbursementRequest
        # means an item's field *names* are as unbounded as its values —
        # an oversized key would otherwise pass through this cap untouched.
        return {
            (key[:limit] if isinstance(key, str) else key): _truncated(item, limit, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_truncated(item, limit, depth + 1) for item in value]
    return value


def write(config: FailureLogConfig, record: dict[str, Any]) -> None:
    """Emit `record` as one JSON object at critical level to the dedicated
    named logger.

    Opens no path and writes no file: a container-local file is destroyed by
    the very restart it exists to survive, so durability is delegated to
    whatever handler ops attach to `logger_name` — no code change needed.

    Never raises. This is the last resort, so by definition there is nowhere
    left to report a failure inside it; propagating one would take down the
    consumer loop it exists to protect. It does still try to leave a trace:
    on failure it logs to a *different* logger name than `config.logger_name`
    — if that name's own handler is what just broke, logging to it again
    here would risk recursing into the same failure instead of reporting it.
    """
    try:
        logging.getLogger(config.logger_name).critical(
            json.dumps(_truncated(record, config.max_message_chars), default=str)
        )
    except Exception:
        logging.getLogger(_FALLBACK_LOGGER_NAME).exception(
            "failure_log.write could not emit a record for event=%s", record.get("event")
        )
