"""The bottom of every fallback chain — one structured JSON record per
failure nothing else could handle."""

import json
import logging
from typing import TYPE_CHECKING, Any

from shared.config import FailureLogConfig

if TYPE_CHECKING:
    from shared.models import AttemptError


def render_errors(errors: "list[AttemptError]") -> list[dict[str, Any]]:
    """Each `AttemptError` as a JSON-safe dict, in order — the one
    sub-expression every service's own failure-record shape shares."""
    return [error.model_dump(mode="json") for error in errors]


def _truncated(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, dict):
        return {key: _truncated(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_truncated(item, limit) for item in value]
    return value


def write(config: FailureLogConfig, record: dict[str, Any]) -> None:
    """Emit `record` as one JSON object at critical level to the dedicated
    named logger.

    Opens no path and writes no file: a container-local file is destroyed by
    the very restart it exists to survive, so durability is delegated to
    whatever handler ops attach to `logger_name` — no code change needed.

    Never raises. This is the last resort, so by definition there is nowhere
    left to report a failure inside it; propagating one would take down the
    consumer loop it exists to protect.
    """
    try:
        logging.getLogger(config.logger_name).critical(
            json.dumps(_truncated(record, config.max_message_chars), default=str)
        )
    except Exception:
        pass
