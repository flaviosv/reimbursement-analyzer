import psycopg
import pytest
from helpers import (
    database_name,
    guard_is_test_database,
    maintenance_url,
    target_database,
    with_database,
)

from api.migrate import apply_migrations


@pytest.mark.parametrize(
    "database",
    ["reimbursementanalyzer", "postgres", "reimbursementanalyzer_testing", "prod"],
)
def test_guard_refuses_any_database_not_suffixed_test(database: str) -> None:
    with pytest.raises(RuntimeError, match="must end in '_test'"):
        guard_is_test_database(f"postgresql://host:5433/{database}")


def test_guard_accepts_a_test_suffixed_database() -> None:
    guard_is_test_database("postgresql://host:5433/reimbursementanalyzer_test")


def test_maintenance_url_targets_the_postgres_database() -> None:
    assert (
        maintenance_url("postgresql://host:5433/reimbursementanalyzer_test")
        == "postgresql://host:5433/postgres"
    )


def test_database_name_is_extracted_from_the_dsn() -> None:
    assert database_name("postgresql://host:5432/reimbursementanalyzer_test") == "reimbursementanalyzer_test"


def test_with_database_swaps_only_the_database() -> None:
    assert (
        with_database("postgresql://host:5433/one", "two")
        == "postgresql://host:5433/two"
    )


@pytest.mark.parametrize("server", ["postgresql://host:5433/postgres", "postgresql://host:5433"])
def test_bare_server_falls_back_to_the_default_test_database(server: str) -> None:
    assert target_database(server) == "reimbursementanalyzer_test"


def test_caller_supplied_database_is_respected() -> None:
    assert target_database("postgresql://host:5433/custom_test") == "custom_test"


def test_migrated_db_fixture_yields_a_live_connection(conn: psycopg.Connection) -> None:
    assert conn.execute("SELECT 1").fetchone() == (1,)


def test_suite_runs_against_the_expected_postgres_major(
    conn: psycopg.Connection,
) -> None:
    version = conn.execute("SHOW server_version_num").fetchone()
    assert version is not None
    # uuidv7() and UNIQUE NULLS NOT DISTINCT both need a modern server; this
    # pins the suite to the same major the compose stack runs.
    assert int(version[0]) >= 180000


def test_reapplying_migrations_is_a_no_op(migrated_db: str) -> None:
    assert apply_migrations(migrated_db) == 0
