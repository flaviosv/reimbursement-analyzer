"""Preserve a request for a human, with an explanation of how it got there."""

from typing import Any
from uuid import UUID

import asyncpg

from shared.config import load_config
from shared.models import AttemptError
from shared.reimbursement.repository import insert_human_review

_NO_HISTORY = "Retry ceiling reached, but the message carried no error detail."


def render_history(errors: list[AttemptError]) -> str:
    """Render every attempt, one line each — never a count and never a summary.

    A reviewer's first question is whether the same error occurred on all four
    attempts (permanently bad data — fix it) or four different ones did (flaky
    infrastructure — just replay it), and only the full list answers it.
    """
    if not errors:
        return _NO_HISTORY

    limit = load_config().failure_log.max_message_chars
    lines = [
        f"attempt {error.attempt} at {error.occurred_at.isoformat()} "
        f"[{error.stage}] {error.error_type}: {error.message[:limit]}"
        for error in errors
    ]
    return "\n".join([f"Retry ceiling reached after {len(errors)} failed attempts:", *lines])


async def send_human_review(
    conn: asyncpg.Connection, item: dict[str, Any], errors: list[AttemptError]
) -> UUID:
    return await insert_human_review(conn, item, render_history(errors))
