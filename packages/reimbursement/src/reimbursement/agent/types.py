"""Types shared by more than one node module — kept out of `schema.py`,
which otherwise holds only `TypedDict` state shapes (AGD's graph State)."""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID

import asyncpg
from langchain_core.runnables import RunnableConfig

from reimbursement.schema import State

ApplyDecision = Callable[[asyncpg.Connection, UUID, str, str], Awaitable[UUID | None]]


class Node(Protocol):
    async def __call__(self, state: State, config: RunnableConfig) -> dict[str, Any]: ...
