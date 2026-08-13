"""Alembic environment script.

Runs in two contexts:

1. Real app startup (`rivulets.db.migrate.run_migrations`): the caller
   already has an open connection -- the sync bridge of the app's async
   engine, via `AsyncConnection.run_sync` -- and hands it to us through
   `config.attributes["connection"]`, Alembic's own "connection sharing"
   pattern (see its cookbook). Migrations then run on the same connection
   as the rest of app startup instead of opening a second one to the same
   SQLite file.
2. `alembic revision --autogenerate` from the CLI during development: no
   connection is pre-supplied, so this falls back to `engine_from_config`
   using `alembic.ini`'s `sqlalchemy.url`.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from rivulets.db import models  # noqa: F401 # pyright: ignore[reportUnusedImport]
from rivulets.db.base import Base

# `models` (imported above) registers every table on Base.metadata as a
# side effect of the model class bodies running. App startup gets this for
# free by transitively importing rivulets.db.models through the api/
# package before migrations run; a bare `alembic revision --autogenerate`
# process has no other reason to import it, so it's done explicitly here.

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is not None:
        context.configure(connection=connectable, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
