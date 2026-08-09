from dataclasses import replace

import pytest
from shared.config import load_config
from shared.db import managed_pool

pytestmark = pytest.mark.anyio


class DescribeManagedPool:
    async def it_sizes_the_pool_from_config_rather_than_asyncpg_defaults(self, migrated_db: str) -> None:
        config = replace(load_config().database, dsn=migrated_db)

        async with managed_pool(config) as pool:
            assert pool.get_min_size() == config.pool_min_size
            assert pool.get_max_size() == config.pool_max_size
            assert (pool.get_min_size(), pool.get_max_size()) != (10, 10)
            assert await pool.fetchval("SELECT 1") == 1

    async def it_closes_the_pool_on_exit(self, migrated_db: str) -> None:
        config = replace(load_config().database, dsn=migrated_db)

        async with managed_pool(config) as pool:
            pass

        assert pool.is_closing()
