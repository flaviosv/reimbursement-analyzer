import threading
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from helpers import maintenance_url, target_database_url
from yoyo import get_backend, read_migrations

from api.migrate import apply_migrations, backend_url, migrations_path

EXPECTED_MIGRATIONS = ["0001.create-reimbursement", "0002.create-human-review"]


def url_for(database: str) -> str:
    parts = urlsplit(target_database_url())
    return urlunsplit(parts._replace(path=f"/{database}"))


@pytest.fixture
def fresh_db(request: pytest.FixtureRequest) -> Iterator[str]:
    # A dedicated database per test: these tests roll migrations back, which
    # would tear the schema out from under the session-scoped suite.
    name = f"reimbursementanalyzer_{abs(hash(request.node.name)) % 10**8}_test"
    admin_url = maintenance_url(target_database_url())
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield url_for(name)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def tables_in(url: str) -> set[str]:
    with psycopg.connect(url) as conn:
        return {
            name
            for (name,) in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ).fetchall()
        }


def test_both_migrations_are_discovered_in_order() -> None:
    migrations = read_migrations(migrations_path())
    assert [m.id for m in migrations] == EXPECTED_MIGRATIONS


def test_first_apply_creates_both_tables(fresh_db: str) -> None:
    assert apply_migrations(fresh_db) == 2
    assert {"reimbursement", "human_review"} <= tables_in(fresh_db)


def test_reapply_is_a_no_op(fresh_db: str) -> None:
    apply_migrations(fresh_db)
    assert apply_migrations(fresh_db) == 0
    assert apply_migrations(fresh_db) == 0


def test_each_applied_version_is_recorded_in_the_ledger(fresh_db: str) -> None:
    apply_migrations(fresh_db)
    with psycopg.connect(fresh_db) as conn:
        recorded = [
            row[0]
            for row in conn.execute(
                "SELECT migration_id FROM _yoyo_migration ORDER BY applied_at_utc"
            ).fetchall()
        ]
    assert recorded == EXPECTED_MIGRATIONS


def test_rollback_order_is_the_reverse_of_apply_order(fresh_db: str) -> None:
    apply_migrations(fresh_db)
    backend = get_backend(backend_url(fresh_db))
    migrations = read_migrations(migrations_path())
    # human_review holds the foreign key, so it must go first; the reverse
    # order would fail outright.
    assert [m.id for m in backend.to_rollback(migrations)] == list(
        reversed(EXPECTED_MIGRATIONS)
    )


def test_full_rollback_removes_both_tables(fresh_db: str) -> None:
    apply_migrations(fresh_db)
    backend = get_backend(backend_url(fresh_db))
    migrations = read_migrations(migrations_path())
    with backend.lock():
        backend.rollback_migrations(backend.to_rollback(migrations))

    remaining = tables_in(fresh_db)
    assert "reimbursement" not in remaining
    assert "human_review" not in remaining
    with psycopg.connect(fresh_db) as conn:
        functions = conn.execute(
            "SELECT proname FROM pg_proc WHERE proname = 'set_updated_at'"
        ).fetchall()
    assert functions == []


def test_rollback_then_reapply_restores_the_schema(fresh_db: str) -> None:
    apply_migrations(fresh_db)
    backend = get_backend(backend_url(fresh_db))
    migrations = read_migrations(migrations_path())
    with backend.lock():
        backend.rollback_migrations(backend.to_rollback(migrations))
    assert apply_migrations(fresh_db) == 2
    assert {"reimbursement", "human_review"} <= tables_in(fresh_db)


def test_concurrent_runners_apply_each_migration_exactly_once(
    fresh_db: str,
) -> None:
    applied: list[int] = []
    errors: list[BaseException] = []
    start = threading.Barrier(2)

    def run() -> None:
        try:
            start.wait(timeout=10)
            applied.append(apply_migrations(fresh_db))
        except BaseException as exc:  # noqa: BLE001 - surfaced via assert below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    # backend.lock() serialises the two runners: one applies both migrations,
    # the other finds nothing left to do.
    assert sorted(applied) == [0, 2]

    with psycopg.connect(fresh_db) as conn:
        count = conn.execute("SELECT count(*) FROM _yoyo_migration").fetchone()
    assert count is not None
    assert count[0] == len(EXPECTED_MIGRATIONS)
