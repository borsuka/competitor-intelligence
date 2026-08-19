"""Alembic environment.

The database URL comes from application settings, never from ``alembic.ini`` — one source
of truth, and no credentials in a tracked file.

Migrations run through the async engine so the same driver (``asyncpg``) is exercised in
migration and at runtime.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings

# Importing the models package registers every table on Base.metadata, which is what
# autogenerate compares against.
from app.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", get_settings().database_url)


def include_object(object_, name, type_, reflected, compare_to) -> bool:
    """Keep pgvector's internal artefacts out of autogenerate output."""
    return not (type_ == "table" and name in {"vector", "spatial_ref_sys"})


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a database connection (``alembic upgrade --sql``)."""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
        # Every migration runs inside one transaction, so a failure leaves the schema
        # exactly as it was.
        transaction_per_migration=False,
    )
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
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
