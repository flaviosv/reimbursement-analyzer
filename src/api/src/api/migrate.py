import logging
import os
from importlib.resources import files

from yoyo import get_backend, read_migrations

MIGRATIONS_DIR = "migrations"
SUPPORTED_SCHEMES = ("postgres", "postgresql")

logger = logging.getLogger(__name__)


def migrations_path() -> str:
    return str(files("api") / MIGRATIONS_DIR)


def backend_url(database_url: str) -> str:
    scheme, separator, rest = database_url.partition("://")
    if not separator:
        raise ValueError(f"DATABASE_URL is not a valid DSN: {database_url!r}")
    # yoyo selects its driver from the scheme, and a bare postgresql:// means
    # psycopg2 — which this project deliberately does not install.
    if "+" in scheme:
        return database_url
    if scheme not in SUPPORTED_SCHEMES:
        raise ValueError(f"unsupported database scheme: {scheme!r}")
    return f"postgresql+psycopg://{rest}"


def apply_migrations(database_url: str) -> int:
    backend = get_backend(backend_url(database_url))
    migrations = read_migrations(migrations_path())
    with backend.lock():
        pending = backend.to_apply(migrations)
        logger.info("%d migration(s) to apply", len(pending))
        backend.apply_migrations(pending)
    return len(pending)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    applied = apply_migrations(os.environ["DATABASE_URL"])
    logger.info("migrations complete (%d applied)", applied)


if __name__ == "__main__":
    main()
