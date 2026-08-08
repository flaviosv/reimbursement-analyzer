"""Preserve a request for a human, with an explanation of how it got there."""

from typing import Any
from uuid import UUID

import asyncpg

from shared.models import AttemptError
from shared.reimbursement.repository import insert_human_review

_NO_HISTORY = "Retry ceiling reached, but the message carried no error detail."


def _one_line(value: str) -> str:
    """Collapse embedded newlines/carriage returns to spaces.

    `error_type`/`message` originate from `str(exc)` — a Postgres or driver
    error can echo back a fragment of the offending input (e.g. a value that
    failed a `::timestamptz`/`::jsonb` cast). Rendered as-is, an embedded
    newline could forge an extra "attempt N ..." line into a record a human
    reviewer reads as the system's own narrative."""
    return " ".join(value.split())


def render_history(errors: list[AttemptError], max_message_chars: int) -> str:
    """Render every attempt, one line each — never a count and never a summary.

    A reviewer's first question is whether the same error occurred on all four
    attempts (permanently bad data — fix it) or four different ones did (flaky
    infrastructure — just replay it), and only the full list answers it.
    """
    if not errors:
        return _NO_HISTORY

    lines = [
        f"attempt {error.attempt} at {error.occurred_at.isoformat()} "
        f"[{_one_line(error.stage)}] {_one_line(error.error_type)}: "
        f"{_one_line(error.message)[:max_message_chars]}"
        for error in errors
    ]
    return "\n".join([f"Retry ceiling reached after {len(errors)} failed attempts:", *lines])


async def send_human_review(
    conn: asyncpg.Connection,
    item: dict[str, Any],
    errors: list[AttemptError],
    max_message_chars: int,
) -> UUID:
    return await insert_human_review(conn, item, render_history(errors, max_message_chars))
