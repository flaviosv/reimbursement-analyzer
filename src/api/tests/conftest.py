from collections.abc import Iterator

import psycopg
import pytest
from shared.config import load_config


@pytest.fixture(autouse=True)
def _clear_config_cache() -> None:
    # load_config() is @lru_cache'd for production (read env once, reuse
    # forever) — without this, whichever test calls it first would poison
    # every later test's view of the environment for the rest of the run.
    load_config.cache_clear()


@pytest.fixture
def conn(migrated_db: str) -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(migrated_db)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()
