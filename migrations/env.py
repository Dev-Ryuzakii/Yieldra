"""Alembic environment — async engine, autogenerate target = Base.metadata."""

import asyncio
from logging.config import fileConfig

from alembic import context
from app.config import settings
from app.database import Base, engine
import app.models  # noqa: F401  (registers all tables on Base.metadata)

config = context.config
# ConfigParser treats percent signs in encoded database passwords as interpolation.
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    # Use the same pooled-connection settings as the app: Afribase needs
    # public search_path and disabled prepared-statement caches.
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
