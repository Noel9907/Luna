"""
Alembic environment.

Two things here are not boilerplate.

The URL comes from the application settings rather than alembic.ini, so there
is one place credentials live and no chance of the two drifting apart.

`include_object` skips the pgvector extension's own objects. Autogenerate
otherwise notices tables and types it did not create and offers to drop them,
which is a very bad suggestion to accept at three in the morning.
"""

from __future__ import annotations

import pathlib
import sys

from alembic import context
from sqlalchemy import create_engine, pool, text

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config
target_metadata = Base.metadata

EXTENSION_TABLES = {"vector", "spatial_ref_sys"}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    if type_ == "table" and name in EXTENSION_TABLES:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(settings().database_url, poolclass=pool.NullPool, future=True)
    with engine.connect() as connection:
        # pgvector has to exist before any table with a Vector column is
        # created, and CREATE EXTENSION needs the owner role. Doing it here
        # rather than in a migration means a fresh database works on the first
        # `alembic upgrade head` with no separate setup step.
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
