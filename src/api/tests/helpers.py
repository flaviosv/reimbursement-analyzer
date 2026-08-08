import os
from secrets import token_hex
from urllib.parse import urlsplit, urlunsplit

# Matches the postgres service in docker-compose.yml, so tests exercise the
# same major version the stack runs.
POSTGRES_IMAGE = "postgres:18"
# Dropping and creating a database needs a session that is not attached to it;
# 'postgres' always exists on the server.
MAINTENANCE_DATABASE = "postgres"


def database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def with_database(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path=f"/{name}"))


def maintenance_url(url: str) -> str:
    return with_database(url, MAINTENANCE_DATABASE)


def disposable_database_name() -> str:
    """A database name no concurrent run can collide with.

    The suite drops its database WITH (FORCE), which terminates whatever
    backends are attached. Under a shared constant name that is not a race but
    mutual destruction -- two runs against one server tear each other down
    mid-assertion -- and pytest-xdist cannot work at all.
    """
    return f"reimbursementanalyzer_{os.getpid()}_{token_hex(4)}_test"


def guard_is_test_database(url: str) -> None:
    name = database_name(url)
    if not name.endswith("_test"):
        raise RuntimeError(
            f"refusing to run against database {name!r}: the test suite drops "
            "and recreates its database, so the name must end in '_test'"
        )


def valid_reimbursement_item(request_id: str = "REQ-0001", **extra: object) -> dict:
    """The canonical minimal-valid POST /api/v1/reimbursement item shape —
    a single source of truth for test_validation.py, test_route.py, and
    test_integration.py, which each need a slightly different usage pattern
    (a fixed dict vs. a request_id-keyed factory) but were previously
    maintaining three independently hand-copied versions of this shape."""
    return {
        "request_id": request_id,
        "submitted_by": "person@example.com",
        "submitted_at": "2026-01-01T12:00:00Z",
        **extra,
    }
