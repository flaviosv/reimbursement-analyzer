"""Types shared by more than one node module — kept out of `schema.py`,
which otherwise holds only `TypedDict` state shapes (AGD's graph State)."""

from datetime import date
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

import asyncpg
from langchain_core.runnables import RunnableConfig
from shared.models import DecisionStatus

from reimbursement.schema import State


class ApplyDecision(Protocol):
    async def __call__(
        self,
        conn: asyncpg.Connection,
        uuid: UUID,
        status: DecisionStatus,
        decision_reason: str,
        *,
        receipts_value: Decimal | None = None,
        receipts_date: date | None = None,
        currency: str | None = None,
    ) -> UUID | None: ...


class Node(Protocol):
    async def __call__(self, state: State, config: RunnableConfig) -> dict[str, Any]: ...
