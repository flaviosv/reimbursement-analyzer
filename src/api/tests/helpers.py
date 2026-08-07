import os
from urllib.parse import urlsplit, urlunsplit

DEFAULT_TEST_DATABASE_URL = (
    "postgresql://reimbursementanalyzer:reimbursementanalyzer@localhost:5433/reimbursementanalyzer_test"
)
# Dropping and creating the test database needs a session that is not attached
# to it; 'postgres' always exists on the server.
MAINTENANCE_DATABASE = "postgres"


def target_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


def database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def guard_is_test_database(url: str) -> None:
    name = database_name(url)
    if not name.endswith("_test"):
        raise RuntimeError(
            f"refusing to run against database {name!r}: the test suite drops "
            "and recreates its database, so the name must end in '_test'"
        )


def maintenance_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{MAINTENANCE_DATABASE}"))
