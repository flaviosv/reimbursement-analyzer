from urllib.parse import urlsplit, urlunsplit

# Matches the postgres service in docker-compose.yml, so tests exercise the
# same major version the stack runs.
POSTGRES_IMAGE = "postgres:18"
TEST_DATABASE = "reimbursementanalyzer_test"
# Dropping and creating a database needs a session that is not attached to it;
# 'postgres' always exists on the server.
MAINTENANCE_DATABASE = "postgres"


def database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def with_database(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path=f"/{name}"))


def maintenance_url(url: str) -> str:
    return with_database(url, MAINTENANCE_DATABASE)


def target_database(server_url: str) -> str:
    """The database the suite should migrate into.

    A caller-supplied server URL names its own database; a bare server (the
    throwaway container) lands on the default test database.
    """
    name = database_name(server_url)
    if name in ("", MAINTENANCE_DATABASE):
        return TEST_DATABASE
    return name


def guard_is_test_database(url: str) -> None:
    name = database_name(url)
    if not name.endswith("_test"):
        raise RuntimeError(
            f"refusing to run against database {name!r}: the test suite drops "
            "and recreates its database, so the name must end in '_test'"
        )
