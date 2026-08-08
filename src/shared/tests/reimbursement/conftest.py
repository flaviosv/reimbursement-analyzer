from collections.abc import AsyncIterator

import asyncpg
import pytest


@pytest.fixture
async def db(migrated_db: str) -> AsyncIterator[asyncpg.Connection]:
    """One connection per test inside a transaction that is always rolled
    back. The migrated schema is session-scoped and shared, so a test that
    leaves a row behind would collide with the next one on the unique index."""
    connection = await asyncpg.connect(migrated_db)
    transaction = connection.transaction()
    await transaction.start()
    try:
        yield connection
    finally:
        await transaction.rollback()
        await connection.close()
