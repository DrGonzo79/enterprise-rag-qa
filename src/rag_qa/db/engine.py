"""Async engine and session factory.

Pool bounds per SPEC-002 Key decision 8: Azure PostgreSQL Flexible Server
burstable tier has a low max_connections ceiling and Container Apps may run
up to 3 replicas, each holding its own pool.
"""

import os

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

POOL_SIZE = 5
POOL_MAX_OVERFLOW = 5


def get_database_url() -> str:
    return os.environ["DATABASE_URL"]


def create_engine(url: str | None = None) -> AsyncEngine:
    return create_async_engine(
        url or get_database_url(),
        pool_size=POOL_SIZE,
        max_overflow=POOL_MAX_OVERFLOW,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
