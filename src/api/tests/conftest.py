from collections.abc import Iterator

import psycopg
import pytest
from helpers import (
    database_name,
    guard_is_test_database,
    maintenance_url,
    target_database_url,
)

from api.migrate import apply_migrations


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[str]:
    url = target_database_url()
    guard_is_test_database(url)
    name = database_name(url)

    with psycopg.connect(maintenance_url(url), autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{name}"')

    apply_migrations(url)
    yield url


@pytest.fixture
def conn(migrated_db: str) -> Iterator[psycopg.Connection]:
    # Every test rolls back, so DML never leaks between tests. The schema is
    # session-scoped and untouched by these transactions.
    connection = psycopg.connect(migrated_db)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()
