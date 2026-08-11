"""Moteur et sessions SQLAlchemy asynchrones.

SQLite est le défaut, mais l'accès passe exclusivement par l'API asynchrone : le
jour où l'URL devient une URL PostgreSQL, rien d'autre ne change.

Deux réglages spécifiques à SQLite sont appliqués à chaque connexion :

* ``foreign_keys=ON`` — SQLite ignore les clés étrangères par défaut, ce qui
  laisserait passer des lignes orphelines ;
* ``journal_mode=WAL`` — permet les lectures pendant une écriture, indispensable
  quand plusieurs requêtes HTTP et le runtime écrivent en parallèle.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from msm.config import Settings
from msm.logging_conf import get_logger

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _prepare_sqlite_directory(url: str) -> None:
    """Crée le dossier du fichier SQLite s'il n'existe pas encore."""
    marker = "sqlite+aiosqlite:///"
    if not url.startswith(marker):
        return
    raw_path = url[len(marker) :]
    if raw_path in ("", ":memory:"):
        return
    Path(raw_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def create_engine(settings: Settings) -> AsyncEngine:
    """Crée le moteur asynchrone à partir des réglages."""
    url = settings.database_url
    _prepare_sqlite_directory(url)

    engine = create_async_engine(
        url,
        echo=settings.database_echo,
        future=True,
        pool_pre_ping=not _is_sqlite(url),
    )

    if _is_sqlite(url):

        @event.listens_for(engine.sync_engine, "connect")
        def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return engine


def init_engine(settings: Settings) -> AsyncEngine:
    """Initialise le moteur et la fabrique de sessions. Idempotent."""
    global _engine, _session_factory
    if _engine is None:
        _engine = create_engine(settings)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
        logger.info("database_engine_ready", dialect=_engine.dialect.name)
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Le moteur de base de données n'a pas été initialisé.")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("La fabrique de sessions n'a pas été initialisée.")
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Session transactionnelle : validée en sortie, annulée en cas d'erreur."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dépendance FastAPI fournissant une session par requête."""
    async with session_scope() as session:
        yield session


async def dispose_engine() -> None:
    """Ferme le moteur — appelé à l'arrêt de l'application."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        logger.info("database_engine_disposed")
    _engine = None
    _session_factory = None
