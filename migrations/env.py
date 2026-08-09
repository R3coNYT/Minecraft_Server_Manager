"""Environnement Alembic.

L'URL de la base provient toujours de la configuration applicative : les
migrations et l'application ne peuvent donc pas viser deux bases différentes.

``render_as_batch=True`` est indispensable avec SQLite, qui ne sait pas modifier
une colonne en place — Alembic recrée alors la table de façon transparente.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from msm.config import get_settings
from msm.db.base import Base
from msm.db import models  # noqa: F401 - enregistre toutes les tables

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Génère le SQL sans se connecter à la base."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Applique les migrations via le moteur asynchrone de l'application."""
    from msm.db.session import create_engine

    engine = create_engine(get_settings())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
