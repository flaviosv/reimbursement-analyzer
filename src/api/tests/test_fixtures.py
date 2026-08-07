import psycopg
import pytest
from helpers import database_name, guard_is_test_database, maintenance_url

from api.migrate import apply_migrations


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@localhost:5433/reimbursementanalyzer",
        "postgresql://u:p@localhost:5433/postgres",
        "postgresql://u:p@localhost:5433/reimbursementanalyzer_testing",
    ],
)
def test_guard_refuses_any_database_not_suffixed_test(url: str) -> None:
    with pytest.raises(RuntimeError, match="must end in '_test'"):
        guard_is_test_database(url)


def test_guard_accepts_a_test_suffixed_database() -> None:
    guard_is_test_database("postgresql://u:p@localhost:5433/reimbursementanalyzer_test")


def test_maintenance_url_targets_the_postgres_database() -> None:
    assert (
        maintenance_url("postgresql://u:p@localhost:5433/reimbursementanalyzer_test")
        == "postgresql://u:p@localhost:5433/postgres"
    )
    assert database_name("postgresql://u:p@h:5432/reimbursementanalyzer_test") == "reimbursementanalyzer_test"


def test_migrated_db_fixture_yields_a_live_connection(conn: psycopg.Connection) -> None:
    assert conn.execute("SELECT 1").fetchone() == (1,)


def test_reapplying_migrations_is_a_no_op(migrated_db: str) -> None:
    # The session fixture already migrated; a second run must find nothing to do.
    assert apply_migrations(migrated_db) == 0
