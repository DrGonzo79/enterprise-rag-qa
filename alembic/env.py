"""Alembic environment: sync-DDL migrations run through the async engine's
run_sync wrapper (SPEC-002 Key decision 5) — single driver, asyncpg only."""

import asyncio
import os

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from rag_qa.db.models import Base
from rag_qa.env import load_env

config = context.config

url = config.get_main_option("sqlalchemy.url")
if not url:
    load_env()  # .env fills gaps for local runs; real env vars win (SPEC-001 KD-6)
    config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        # A test or caller already holds a (sync-wrapped) connection.
        do_run_migrations(connection)
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
