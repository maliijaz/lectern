"""Test fixtures.

Every test runs against a throwaway data directory and the ``fake`` LLM provider, so the
suite needs no Ollama, no network and no GPU.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest


def _configure_environment() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="ta-test-"))
    os.environ.update(
        {
            "TA_DATA_DIR": str(tmp),
            "TA_DATABASE_URL": f"sqlite+aiosqlite:///{(tmp / 'test.db').as_posix()}",
            "TA_LLM_PROVIDER": "fake",
            "TA_LLM_MODEL": "fake-model",
            "TA_WORKER_CONCURRENCY": "1",
            "TA_DEBUG": "true",
        }
    )
    return tmp


# Must happen before anything imports app.config, whose Settings are cached.
DATA_DIR = _configure_environment()


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA_DIR


@pytest.fixture(autouse=True)
def _reset_caches() -> Iterator[None]:
    """Clear the settings caches between tests so overrides do not leak."""
    from app.config import get_settings
    from app.services import settings_service

    get_settings.cache_clear()
    settings_service.invalidate_cache()
    yield
    get_settings.cache_clear()
    settings_service.invalidate_cache()


@pytest.fixture
async def db() -> AsyncIterator:
    """A session against a freshly created schema."""
    from app.db.session import init_db, session_scope

    await init_db()
    async with session_scope() as session:
        yield session


@pytest.fixture
def client() -> Iterator:
    """A TestClient that runs the real lifespan — workers included."""
    from starlette.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def fake_llm():
    """The FakeProvider instance the app will use, so tests can script replies."""
    from app.config import get_settings
    from app.llm.registry import build_provider

    return build_provider(get_settings())
