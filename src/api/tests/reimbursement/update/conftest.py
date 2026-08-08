import asyncpg
import pytest
from helpers import FakePool


@pytest.fixture
def fake_pool(db: asyncpg.Connection) -> FakePool:
    return FakePool(db)
