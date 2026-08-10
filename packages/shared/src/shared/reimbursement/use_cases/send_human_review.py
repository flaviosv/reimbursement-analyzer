"""Preserve a request for a human, with an explanation of how it got there."""

from typing import Any
from uuid import UUID

import asyncpg

from shared.models import AttemptError
from shared.reimbursement.repository import insert_human_review
from shared.reimbursement.use_cases.apply_decision import apply_decision

DEFAULT_HEADER = "Retry ceiling reached"


def _one_line(value: str) -> str:
    """Collapse embedded newlines/carriage returns to spaces.

    `error_type`/`message` originate from `str(exc)` — a Postgres or driver
    error can echo back a fragment of the offending input (e.g. a value that
    failed a `::timestamptz`/`::jsonb` cast). Rendered as-is, an embedded
    newline could forge an extra "attempt N ..." line into a record a human
    reviewer reads as the system's own narrative."""
    return " ".join(value.split())


def render_history(
    errors: list[AttemptError], max_message_chars: int, *, header: str = DEFAULT_HEADER
) -> str:
    """Render every attempt, one line each — never a count and never a summary.

    A reviewer's first question is whether the same error occurred on all four
    attempts (permanently bad data — fix it) or four different ones did (flaky
    infrastructure — just replay it), and only the full list answers it.

    `header` defaults to the retry-ceiling escalation's own wording; a caller
    whose escalation was never about a retry ceiling (e.g. an immediate
    decision-stage failure) supplies its own accurate text instead — the
    default would otherwise claim a retry ceiling was reached when none was.
    The no-history sentence below is built from the same `header`, so a
    custom-header caller never sees contradictory "Retry ceiling" wording
    even in the empty-errors case.
    """
    if not errors:
        return f"{header}, but the message carried no error detail."

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
    header: str = DEFAULT_HEADER,
) -> UUID:
    return await insert_human_review(conn, item, render_history(errors, max_message_chars, header=header))


async def escalate_existing(
    conn: asyncpg.Connection,
    uuid: UUID,
    errors: list[AttemptError],
    max_message_chars: int,
    *,
    header: str = DEFAULT_HEADER,
) -> UUID | None:
    """Escalate a row that already exists (the publisher inserted it), so
    this is an UPDATE, not a fresh INSERT like send_human_review's. Two
    callers today: the Agent's own retry>3 fallback (`header` defaulted),
    and a decision-stage failure escalated immediately regardless of retry
    count (`header="Decision-stage failure"`) — retry-count-agnostic by
    design. Returns None when the uuid is a ghost (R-001) — nothing to
    escalate, the caller routes to the failure log instead."""
    return await apply_decision(
        conn, uuid, "human-review", render_history(errors, max_message_chars, header=header)
    )
