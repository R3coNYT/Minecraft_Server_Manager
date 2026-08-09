"""Socle SQLAlchemy : classe de base, conventions de nommage, mixins.

La convention de nommage des contraintes est indispensable : sans elle, SQLite
génère des noms anonymes qu'Alembic ne sait pas modifier lors d'une migration.
La fixer dès le premier jour évite une impasse au premier changement de schéma.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Classe de base de tous les modèles."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def __repr__(self) -> str:  # pragma: no cover - confort de débogage
        identifier = getattr(self, "id", None)
        return f"<{type(self).__name__} id={identifier}>"


def utcnow() -> datetime:
    """Horodatage courant en UTC — jamais d'heure locale en base."""
    return datetime.now(UTC)


class TimestampMixin:
    """Colonnes de création et de mise à jour, gérées par la base."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
