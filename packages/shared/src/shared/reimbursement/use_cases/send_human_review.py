"""Preserve a request for a human, with an explanation of how it got there."""

from typing import Any
from uuid import UUID

import asyncpg

from shared.models import AttemptError
from shared.reimbursement.repository import insert_human_review
from shared.reimbursement.use_cases.apply_decision import apply_decision

_NO_HISTORY = "Retry ceiling reached, but the message carried no error detail."


def _one_line(value: str) -> str:
    """Collapse embedded newlines/carriage returns to spaces.

    `error_type`/`message` originate from `str(exc)` — a Postgres or driver
    error can echo back a fragment of the offending input (e.g. a value that
    failed a `::timestamptz`/`::jsonb` cast). Rendered as-is, an embedded
    newline could forge an extra "attempt N ..." line into a record a human
    reviewer reads as the system's own narrative."""
    return " ".join(value.split())


def render_history(
    errors: list[AttemptError], max_message_chars: int, *, header: str = "Retry ceiling reached"
) -> str:
    """Render every attempt, one line each — never a count and never a summary.

    A reviewer's first question is whether the same error occurred on all four
    attempts (permanently bad data — fix it) or four different ones did (flaky
    infrastructure — just replay it), and only the full list answers it.

    `header` defaults to the retry-ceiling escalation's own wording; a caller
    whose escalation was never about a retry ceiling (e.g. an immediate
    decision-stage failure) supplies its own accurate text instead — the
    default would otherwise claim a retry ceiling was reached when none was.
    """
    if not errors:
        return _NO_HISTORY

    lines = [
        f"attempt {error.attempt} at {error.occurred_at.isoformat()} "
        f"[{_one_line(error.stage)}] {_one_line(error.error_type)}: "
        f"{_one_line(error.message)[:max_message_chars]}"
        for error in errors
    ]
    return "\n".join([f"{header} after {len(errors)} failed attempts:", *lines])


async def send_human_review(
    conn: asyncpg.Connection,
    item: dict[str, Any],
    errors: list[AttemptError],
    max_message_chars: int,
    *,
    header: str = "Retry ceiling reached",
) -> UUID:
    return await insert_human_review(conn, item, render_history(errors, max_message_chars, header=header))


async def escalate_existing(
    conn: asyncpg.Connection,
    uuid: UUID,
    errors: list[AttemptError],
    max_message_chars: int,
    *,
    header: str = "Retry ceiling reached",
) -> UUID | None:
    """The Agent's own retry>3 fallback: the row already exists (the
    publisher inserted it), so escalation is an UPDATE, not a fresh INSERT
    like send_human_review's. Returns None when the uuid is a ghost (R-001)
    — nothing to escalate, the caller routes to the failure log instead."""
    return await apply_decision(
        conn, uuid, "human-review", render_history(errors, max_message_chars, header=header)
    )
