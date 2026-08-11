"""Joueurs, cache de skins, événements, sauvegardes et réglages globaux.

Les tables ``event_definitions``/``event_runs`` et ``backups`` sont créées dès
maintenant bien que leurs fonctionnalités arrivent en phases 4 et 5 : les définir
tôt évite une migration de schéma sur une base déjà en production, et matérialise
que l'architecture les prévoit.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from msm.db.base import Base, TimestampMixin
from msm.db.types import UtcDateTime


class Player(Base):
    """Joueur connu d'un serveur, conservé même hors ligne."""

    __tablename__ = "players"
    __table_args__ = (UniqueConstraint("server_id", "uuid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Absent tant que le serveur n'a pas révélé l'UUID (mode hors ligne).
    uuid: Mapped[str | None] = mapped_column(String(36), index=True)
    username: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    first_seen: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_seen: Mapped[datetime | None] = mapped_column(UtcDateTime)
    total_sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    is_op: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_whitelisted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class SkinCache(Base):
    """Cache des skins, indexé par UUID.

    Indispensable : sans lui, chaque rafraîchissement de la liste des joueurs
    déclencherait un appel à une API externe par joueur et par client connecté.
    """

    __tablename__ = "skin_cache"

    uuid: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str | None] = mapped_column(String(16))
    texture_url: Mapped[str | None] = mapped_column(String(512))
    #: Chemin local de l'avatar mis en cache, relatif au dossier de données.
    avatar_path: Mapped[str | None] = mapped_column(String(512))
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    #: Marqué quand l'API externe n'a rien renvoyé : évite de la solliciter en boucle.
    not_found: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class EventDefinition(Base, TimestampMixin):
    """Événement réutilisable : suite ordonnée d'actions et de délais."""

    __tablename__ = "event_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Nul = modèle global, réutilisable sur n'importe quel serveur.
    server_id: Mapped[int | None] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    #: Étapes validées par le registre d'actions au moment de l'enregistrement.
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class EventRunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EventRun(Base):
    """Exécution d'un événement, avec sa progression."""

    __tablename__ = "event_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("event_definitions.id", ondelete="SET NULL")
    )
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    status: Mapped[EventRunStatus] = mapped_column(
        SAEnum(EventRunStatus, native_enum=False, length=16),
        nullable=False,
        default=EventRunStatus.PENDING,
    )
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    log: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)


class BackupStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Backup(Base):
    """Sauvegarde d'un serveur (fonctionnalité de phase 5)."""

    __tablename__ = "backups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="full")
    status: Mapped[BackupStatus] = mapped_column(
        SAEnum(BackupStatus, native_enum=False, length=16),
        nullable=False,
        default=BackupStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(Text)


class AppSetting(Base, TimestampMixin):
    """Réglage global modifiable depuis l'interface, sans redéploiement."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
