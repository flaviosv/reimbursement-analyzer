import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from api.migrate import apply_migrations
from helpers import (
    MAINTENANCE_DATABASE,
    POSTGRES_IMAGE,
    disposable_database_name,
    guard_is_test_database,
    maintenance_url,
    with_database,
)
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
def conn(migrated_db: str) -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(migrated_db)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()
