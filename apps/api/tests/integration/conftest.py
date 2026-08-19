"""Integration test fixtures.

These tests need a real PostgreSQL with pgvector — SQLite cannot represent JSONB, arrays
or vector columns, and a test that runs against a different database than production is
testing the wrong thing.

Skipped entirely unless ``TEST_DATABASE_URL`` is set:

    docker compose up -d postgres
    TEST_DATABASE_URL=postgresql+asyncpg://sentinel:sentinel@localhost:5442/sentinel_test \\
        pytest tests/integration
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.integration

requires_database = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set; see tests/integration/conftest.py",
)


@pytest.fixture(scope="session", autouse=True)
def _test_environment():
    """Configure the app for tests before anything imports settings."""
    os.environ["ENVIRONMENT"] = "test"
    os.environ["SECRET_KEY"] = "integration-test-secret-key-long-enough"
    os.environ["AI_PROVIDER"] = "mock"
    os.environ["RATE_LIMIT_ENABLED"] = "false"
    os.environ["COOKIE_SECURE"] = "false"
    if TEST_DATABASE_URL:
        os.environ["DATABASE_URL"] = TEST_DATABASE_URL

    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture(scope="session")
async def engine():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")

    from app.db.models import Base

    # NullPool: pytest-asyncio gives each test its own event loop, and a pooled
    # asyncpg connection created in one loop cannot be reused in another.
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)

    from sqlalchemy import text

    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        # A dedicated test database is created from the models rather than by replaying
        # migrations: migrations are verified separately, and this keeps the suite fast.
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(engine) -> AsyncGenerator[AsyncClient, None]:
    """An HTTP client bound to the ASGI app, sharing the test engine."""
    from app.db.session import get_session
    from app.main import create_app

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture
async def cleanup(engine):
    """Truncate between tests so each one starts from a known state."""
    yield
    from sqlalchemy import text

    from app.db.models import Base

    tables = ", ".join(f'"{table}"' for table in reversed(Base.metadata.sorted_tables))
    async with engine.begin() as connection:
        # TRUNCATE needs ACCESS EXCLUSIVE, so a test that left a transaction open on the
        # shared session would block here forever. A timeout turns that into a legible
        # failure instead of a run that never finishes.
        await connection.execute(text("SET lock_timeout = '10s'"))
        await connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
