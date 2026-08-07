import os
from collections.abc import Iterator

import psycopg
import pytest
from helpers import (
    MAINTENANCE_DATABASE,
    POSTGRES_IMAGE,
    guard_is_test_database,
    maintenance_url,
    target_database,
    with_database,
)
from testcontainers.community.postgres import PostgresContainer

from api.migrate import apply_migrations


@pytest.fixture(scope="session")
def server_url() -> Iterator[str]:
    """A PostgreSQL server for the suite to run against.

    Defaults to a throwaway container, so the suite needs no running stack and
    cannot reach a real database at all. TEST_DATABASE_URL overrides it for
    environments that supply their own server (a CI service container, say);
    that path is guarded because it can point anywhere.
    """
    override = os.environ.get("TEST_DATABASE_URL")
    if override:
        guard_is_test_database(override)
        yield override
        return

    with PostgresContainer(POSTGRES_IMAGE, dbname=MAINTENANCE_DATABASE) as container:
        yield container.get_connection_url(driver=None)


@pytest.fixture(scope="session")
def migrated_db(server_url: str) -> Iterator[str]:
    url = with_database(server_url, target_database(server_url))
    name = target_database(server_url)

    with psycopg.connect(maintenance_url(server_url), autocommit=True) as admin:
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
