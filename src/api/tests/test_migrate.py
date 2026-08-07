import os
from pathlib import Path

import pytest

from api.migrate import backend_url, migrations_path


def test_postgresql_scheme_is_adapted_to_psycopg() -> None:
    assert (
        backend_url("postgresql://user:pw@host:5432/db")
        == "postgresql+psycopg://user:pw@host:5432/db"
    )


def test_postgres_alias_is_normalised_to_postgresql() -> None:
    assert (
        backend_url("postgres://user:pw@host:5432/db")
        == "postgresql+psycopg://user:pw@host:5432/db"
    )


def test_already_adapted_dsn_is_left_unchanged() -> None:
    dsn = "postgresql+psycopg://user:pw@host:5432/db"
    assert backend_url(dsn) == dsn


def test_dsn_without_scheme_separator_is_rejected() -> None:
    with pytest.raises(ValueError, match="not a valid DSN"):
        backend_url("user:pw@host:5432/db")


def test_non_postgres_scheme_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported database scheme"):
        backend_url("mysql://user:pw@host:3306/db")


def test_migrations_path_resolves_inside_the_installed_package() -> None:
    path = Path(migrations_path())
    assert path.name == "migrations"
    # The SQL must ship inside the package so it survives into the prod image,
    # which copies only the venv (AD-007).
    assert (path.parent / "__init__.py").is_file()


def test_main_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.migrate import main

    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(KeyError):
        main()
    assert "DATABASE_URL" not in os.environ
