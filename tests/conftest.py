"""Async DB fixtures per SPEC-002's test plan.

Session-scoped NullPool engine, function-scoped connections, savepoint-based
rollback isolation. Migration tests use a dedicated scratch database instead.
"""

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://rag:rag@localhost:5432/rag")
SCRATCH_DB = "rag_migration_test"


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
async def migrated_engine(engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """The main test DB, migrated to head for the duration of the session."""
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    async with engine.connect() as conn:
        await conn.run_sync(lambda sync_conn: _upgrade(config, sync_conn))
        await conn.commit()
    yield engine


def _upgrade(config: object, sync_conn: object) -> None:
    from alembic import command
    from alembic.config import Config

    assert isinstance(config, Config)
    config.attributes["connection"] = sync_conn
    command.upgrade(config, "head")


@pytest.fixture
async def connection(migrated_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """Function-scoped connection wrapping each test in a rolled-back transaction."""
    async with migrated_engine.connect() as conn:
        transaction = await conn.begin()
        yield conn
        await transaction.rollback()


@pytest.fixture
async def session(connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """Savepoint-mode session bound to the rolled-back outer transaction."""
    async with AsyncSession(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    ) as sess:
        yield sess
