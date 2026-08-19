"""Shared test configuration.

Unit tests run with no external dependencies at all.  Integration tests need PostgreSQL
and are skipped unless ``TEST_DATABASE_URL`` is set — see ``tests/integration/conftest.py``.
"""

from __future__ import annotations

import os

import pytest

# Set before any application import so the settings singleton is built in test mode.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-tests")
os.environ.setdefault("AI_PROVIDER", "mock")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Give each test a clean settings singleton.

    Tests that patch environment variables would otherwise leak configuration into every
    test that runs after them.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings():
    return get_settings()
