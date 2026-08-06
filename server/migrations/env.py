"""Alembic environment.

The database URL comes from Calton's own settings rather than ``alembic.ini`` so that a
migration always targets the same database the application would open.

Calton starts from a single baseline migration shaped like the current upstream schema
(design §1.3); the 60-plus historical xorm migrations are deliberately not reproduced.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

import calton.models  # noqa: F401  -- importing registers every model on Base.metadata
from calton.config import get_settings
from calton.db.base import Base
from calton.db.session import build_engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    settings = get_settings()
    if settings.database.type == "mysql":
        url = build_engine().url.render_as_string(hide_password=False)
    else:
        url = f"sqlite+pysqlite:///{settings.database.path}"
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # batch mode is a SQLite workaround (it cannot ALTER in place); MySQL alters
        # directly and batch mode would only rewrite tables needlessly.
        render_as_batch=settings.database.type == "sqlite",
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = build_engine()

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot ALTER most things in place; batch mode rewrites the table.
            # MySQL alters in place, so batch mode is both unnecessary and wrong there.
            render_as_batch=engine.dialect.name == "sqlite",
        )

        with context.begin_transaction():
            context.run_migrations()

    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
