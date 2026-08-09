import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import files

import psycopg
from dotenv import load_dotenv
from yoyo import get_backend, read_migrations

SUPPORTED_SCHEMES = ("postgres", "postgresql")
LOCK_KEY = 4_150_942_001
LOCK_TIMEOUT_SECONDS = 300.0
LOCK_POLL_SECONDS = 0.5

logger = logging.getLogger(__name__)


class MigrationLockTimeout(RuntimeError):
    pass


def migrations_path() -> str:
    return str(files("api") / "migrations")


def _dsn_body(database_url: str) -> str:
    scheme, separator, rest = database_url.partition("://")
    if not separator:
        # Never echo the DSN: it carries the password and this reaches the logs.
        raise ValueError("DATABASE_URL is not a valid DSN: expected '<scheme>://...'")
    base_scheme = scheme.partition("+")[0]
    if base_scheme not in SUPPORTED_SCHEMES:
        raise ValueError(f"unsupported database scheme: {base_scheme!r}")
    return rest


def backend_url(database_url: str) -> str:
    # yoyo picks its driver from the scheme, and a bare postgresql:// means
    # psycopg2, which is not installed.
    return f"postgresql+psycopg://{_dsn_body(database_url)}"


def connection_url(database_url: str) -> str:
    return f"postgresql://{_dsn_body(database_url)}"


@contextmanager
def advisory_lock(
    database_url: str, timeout: float = LOCK_TIMEOUT_SECONDS
) -> Iterator[None]:
    """Serialise migration runners across processes.

    PostgreSQL drops a session-scoped advisory lock as soon as the connection
    goes away, so a runner killed mid-migration cannot leave one behind. yoyo's
    own lock is a table row removed in a `finally` that SIGKILL never reaches;
    that row would outlive the process and, because every service waits on the
    migrate job completing, hold the whole stack down until cleared by hand.
    """
    with psycopg.connect(connection_url(database_url), autocommit=True) as lock_conn:
        deadline = time.monotonic() + timeout
        while not _try_acquire(lock_conn):
            if time.monotonic() >= deadline:
                raise MigrationLockTimeout(
                    f"another migration runner has held the lock for over {timeout:g}s"
                )
            time.sleep(LOCK_POLL_SECONDS)
        yield


def _try_acquire(connection: psycopg.Connection) -> bool:
    row = connection.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()
    return bool(row and row[0])


def apply_migrations(
    database_url: str, lock_timeout: float = LOCK_TIMEOUT_SECONDS
) -> int:
    backend = get_backend(backend_url(database_url))
    migrations = read_migrations(migrations_path())
    with advisory_lock(database_url, lock_timeout):
        # Holding the advisory lock proves no other runner exists, which is the
        # one condition that makes clearing yoyo's lock table safe.
        backend.break_lock()
        pending = backend.to_apply(migrations)
        logger.info("%d migration(s) to apply", len(pending))
        backend.apply_migrations(pending)
    return len(pending)


def main() -> None:
    load_dotenv()
    database_url = os.environ["DATABASE_URL"]
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    applied = apply_migrations(database_url)
    logger.info("migrations complete (%d applied)", applied)


if __name__ == "__main__":
    main()
