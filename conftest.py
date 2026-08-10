"""Workspace-level test fixtures.

The PostgreSQL server lives here, not under one package's tests, so every
package shares a single session-scoped container. Defined in `src/api/tests/`
it would start a second one the moment `src/shared/tests/` needed a database.

`migrate` is `api`'s own installed package now (setuptools + package-dir), so
it imports as `api.migrate` like any other `api` module. The DB-provisioning
utilities live in `shared.testing`, a normal package import every service's
test suite already reaches through.
"""

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

import asyncpg
import psycopg
import pytest
from api.migrate import apply_migrations
from shared.testing import (
    MAINTENANCE_DATABASE,
    POSTGRES_IMAGE,
    disposable_database_name,
    guard_is_test_database,
    maintenance_url,
    with_database,
)
from testcontainers.community.postgres import PostgresContainer


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Defense-in-depth backstop: a bare `-m` expression that forgets `and not
    e2e` (e.g. `-m "not integration"`) must not accidentally run the e2e
    suite against a real stack and a real GROQ_API_KEY. The documented gate
    commands already exclude e2e correctly — this only catches the case
    where a developer types a custom expression by hand.
    """
    markexpr = config.getoption("-m") or ""
    explicit_e2e = "e2e" in markexpr and "not e2e" not in markexpr
    if explicit_e2e:
        return

    skip_e2e = pytest.mark.skip(reason="e2e requires an explicit -m e2e (real stack + real GROQ_API_KEY)")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_e2e)


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
