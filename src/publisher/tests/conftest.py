import pytest
from shared.config import load_config


@pytest.fixture(autouse=True)
def _clear_config_cache() -> None:
    # load_config() is @lru_cache'd for production (read env once, reuse
    # forever) — without this, whichever test calls it first would poison
    # every later test's view of the environment for the rest of the run.
    # pytest's per-directory conftest scoping does not fan this out from the
    # sibling api/shared test trees, so it is declared again here.
    load_config.cache_clear()
