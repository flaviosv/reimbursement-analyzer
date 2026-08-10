"""One JSON log-event envelope for this feature's new call sites.

Always import as `from shared.logging import log_event` — never
`from shared import logging`, which reads like the stdlib module at the
call site (no functional collision either way: Python 3's absolute imports
resolve a plain `import logging` inside this module to the stdlib, not
itself)."""

import json
import logging
from typing import Any

_FALLBACK_LOGGER_NAME = "reimbursementanalyzer.logging.fallback"


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
