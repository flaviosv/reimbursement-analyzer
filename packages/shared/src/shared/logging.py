"""One JSON log-event envelope for this feature's new call sites.

Always import as `from shared.logging import log_event` — never
`from shared import logging`, which reads like the stdlib module at the
call site (no functional collision either way: Python 3's absolute imports
resolve a plain `import logging` inside this module to the stdlib, not
itself)."""

import contextvars
import json
import logging
from typing import Any

_FALLBACK_LOGGER_NAME = "reimbursementanalyzer.logging.fallback"

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)


def get_correlation_id() -> str | None:
    """The current context's correlation id, or `None` if none is set (e.g.
    outside any HTTP request/Kafka message scope)."""
    return _correlation_id.get()


def set_correlation_id(value: str | None) -> contextvars.Token:
    """Set the current context's correlation id. `None` is a valid, common
    value (e.g. a consumer processing a pre-feature message with no id to
    carry). Returns a token for `reset_correlation_id`."""
    return _correlation_id.set(value)


def reset_correlation_id(token: contextvars.Token) -> None:
    """Restore the previous correlation id. Always called in a `finally` by
    every setter (middleware, both consumers) so one request/message's id
    never bleeds into the next."""
    _correlation_id.reset(token)


class CorrelationIdFilter(logging.Filter):
    """Injects the current context's correlation id into every `LogRecord`
    that passes through — the mechanism that makes the field appear on
    every log line with no per-call-site `extra={}` needed.

    Omits the attribute entirely (rather than setting it to `None`) when no
    correlation id is set: `ecs_logging.StdlibFormatter` only emits
    attributes actually present on the record, so omitting the attribute is
    what makes the JSON key itself absent instead of present-as-`null`."""

    def filter(self, record: logging.LogRecord) -> bool:
        correlation_id = get_correlation_id()
        if correlation_id is not None:
            record.correlation_id = correlation_id
        return True


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Emit `{"event": event, **fields}` as one JSON object at `level`.

    Mirrors `failure_log.write`'s `default=str` (so a `UUID`/`Decimal` field
    serializes without extra caller effort) and defensive try/except shape —
    never raises, so a logging call can never break the caller's business
    flow. Falls back to a differently-named logger on failure, the same
    "don't recurse into the same failure" reasoning `failure_log.write`
    uses.

    No field allowlist or redaction: callers are responsible for passing
    only pre-vetted primitive fields (`str`/`int`/`float`/`bool`/`None`/
    `UUID`) — passing a raw exception, ORM row, or pydantic model would
    silently serialize whatever `default=str` produces for it.
    """
    try:
        payload = json.dumps({"event": event, **fields}, default=str)
        logger.log(level, "%s", payload)
    except Exception:
        logging.getLogger(_FALLBACK_LOGGER_NAME).exception(
            "log_event could not emit a record for event=%s", event
        )
