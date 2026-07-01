"""Fixtures for functional tests — in-process with real DB."""

import os
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dropbox.main import create_app
from dropbox.models.base import Base

# Read from env (set by CI/manifest or local), with default for CI
_DEFAULT_DB_URL = "postgresql+asyncpg://dropbox:dropbox@localhost:5432/dropbox"
_DB_URL = os.environ.get("DROPBOX_DATABASE_URL")
TEST_DATABASE_URL = _DB_URL if _DB_URL else _DEFAULT_DB_URL
os.environ.setdefault("DROPBOX_DATABASE_URL", TEST_DATABASE_URL)


@pytest_asyncio.fixture(scope="session")
async def engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Fresh session per test — each test gets its own connection."""
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        yield s


@pytest_asyncio.fixture
async def client(session):
    """In-process ASGI client with real DB."""
    app = create_app()

    async def override_get_session():
        yield session

    from dropbox.database import get_session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def fresh_namespace_id():
    return uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF
