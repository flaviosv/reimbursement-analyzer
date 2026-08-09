import json
from typing import Any, Self
from uuid import UUID

import asyncpg
from pydantic import BaseModel


class Reimbursement(BaseModel):
    """The graph's own minimal view of a resolved row — just `uuid` and
    `original_payload`, not the full table."""

    uuid: UUID
    original_payload: dict[str, Any]

    @classmethod
    def from_record(cls, record: asyncpg.Record) -> Self:
        # original_payload always arrives as raw text: no asyncpg JSON codec
        # is configured anywhere in this codebase.
        return cls(
            uuid=record["uuid"],
            original_payload=json.loads(record["original_payload"]),
        )
