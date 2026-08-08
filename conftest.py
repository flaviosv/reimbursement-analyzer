"""Workspace-level test fixtures.

The PostgreSQL server lives here, not under one package's tests, so every
package shares a single session-scoped container. Defined in `src/api/tests/`
it would start a second one the moment `src/shared/tests/` needed a database.

`helpers` and `migrate` resolve through the root pyproject's `pythonpath`
entries, which the python_path plugin inserts before initial conftests load.
"""

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

import asyncpg
import psycopg
import pytest
from helpers import (
    MAINTENANCE_DATABASE,
    POSTGRES_IMAGE,
    disposable_database_name,
    guard_is_test_database,
    maintenance_url,
    with_database,
)
from migrate import apply_migrations
from testcontainers.community.postgres import PostgresContainer


@contextmanager
def disposable_database(server_url: str) -> Iterator[str]:
    name = disposable_database_name()
    url = with_database(server_url, name)
    guard_is_test_database(url)
    admin_url = maintenance_url(server_url)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield url
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def server_url() -> Iterator[str]:
    """A PostgreSQL server for the suite to run against.

    Defaults to a throwaway container, so the suite needs no running stack and
    cannot reach a real database. TEST_DATABASE_URL points it at a server you
    supply; either way it only ever creates and drops databases it named itself.
    """
    override = os.environ.get("TEST_DATABASE_URL")
    if override:
        yield override
        return

    try:
        container = PostgresContainer(POSTGRES_IMAGE, dbname=MAINTENANCE_DATABASE)
        container.start()
    except Exception as exc:
        pytest.fail(
            f"could not start the {POSTGRES_IMAGE} test container: {exc}. "
            "Docker must be running. To run only the tests that need no "
            'database, use: pytest -m "not integration"',
            pytrace=False,
        )
    try:
        yield container.get_connection_url(driver=None)
    finally:
        container.stop()


@pytest.fixture(scope="session")
def migrated_db(server_url: str) -> Iterator[str]:
    with disposable_database(server_url) as url:
        apply_migrations(url)
        yield url


@pytest.fixture
async def db(migrated_db: str) -> AsyncIterator[asyncpg.Connection]:
    """One connection per test inside a transaction that is always rolled
    back. The migrated schema is session-scoped and shared, so a test that
    leaves a row behind would collide with the next one on the unique index.

    Lives here rather than under one package's tests for the same reason
    `server_url` does — `shared` and `publisher` both need it, and a
    per-package copy is a fixture maintained in two places.
    """
    connection = await asyncpg.connect(migrated_db)
    transaction = connection.transaction()
    await transaction.start()
    try:
        yield connection
    finally:
        await transaction.rollback()
        await connection.close()
