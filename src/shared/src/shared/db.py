"""Pool lifecycle — domain-agnostic, shared by every service that touches Postgres."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from shared.config import DatabaseConfig


@asynccontextmanager
async def managed_pool(config: DatabaseConfig) -> AsyncIterator[asyncpg.Pool]:
    """Construct a connection pool and guarantee `close()` on exit — the same
    construct/yield/close shape as shared.producer.managed_producer, so the
    two resources compose identically in a service's startup.

    Both sizes are always passed: create_pool defaults to min_size=10,
    max_size=10, which exactly equals the publisher's item concurrency and so
    would leave the pool zero headroom (AD-017).
    """
    pool = await asyncpg.create_pool(
        dsn=config.dsn,
        min_size=config.pool_min_size,
        max_size=config.pool_max_size,
        command_timeout=config.command_timeout,
    )
    try:
        yield pool
    finally:
        await pool.close()
