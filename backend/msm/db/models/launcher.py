"""Intégration d'un serveur avec le serveur de fichiers de son launcher.

Deux tables :

* ``launcher_integrations`` — une par serveur : l'adresse du serveur de fichiers,
  la cadence de synchronisation, l'état de la dernière synchronisation et de la
  dernière publication ;
* ``launcher_files`` — ce que MSM a **lui-même** installé. C'est ce qui lui
  permet de ne jamais supprimer un mod ajouté à la main : seul un fichier suivi
  ici peut être retiré par une synchronisation.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from msm.db.base import Base, TimestampMixin
from msm.db.types import UtcDateTime


class SyncStatus(str, Enum):
    """Issue de la dernière synchronisation."""

    NEVER = "NEVER"
    UP_TO_DATE = "UP_TO_DATE"
    APPLIED = "APPLIED"
    #: Changements téléchargés, appliqués au prochain démarrage du serveur.
    PENDING_RESTART = "PENDING_RESTART"
    #: Suppression massive détectée : rien n'est fait sans confirmation.
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class LauncherIntegration(Base, TimestampMixin):
    """Réglages et état de synchronisation d'un serveur."""

    __tablename__ = "launcher_integrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    file_server_url: Mapped[str] = mapped_column(String(512), nullable=False)
    #: Dossiers synchronisés, avec leur `/` final — `mods/` par défaut.
    sync_paths: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

    #: Jeton d'écriture vers le serveur de fichiers, chiffré.
    push_token_encrypted: Mapped[str | None] = mapped_column(Text)

    #: Côté forcé par l'administrateur, par chemin : il l'emporte sur tout le reste.
    side_overrides: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    #: Côté détecté dans les JAR, par empreinte : un fichier n'est inspecté qu'une fois.
    side_cache: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)

    # --- Dernier manifest lu -------------------------------------------------
    manifest_etag: Mapped[str | None] = mapped_column(String(256))
    pack_version: Mapped[str | None] = mapped_column(String(64))
    #: Entrées du manifest sous les dossiers synchronisés, pour l'affichage.
    manifest_entries: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )

    # --- Synchronisation -----------------------------------------------------
    next_sync_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_sync_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_sync_status: Mapped[SyncStatus] = mapped_column(
        SAEnum(SyncStatus, native_enum=False, length=16), nullable=False, default=SyncStatus.NEVER
    )
    last_sync_error: Mapped[str | None] = mapped_column(Text)
    last_sync_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    #: Plan mis en attente parce que le serveur tournait.
    pending_plan: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # --- Publication de l'état aux joueurs -----------------------------------
    #: Augmente à chaque changement de la liste des mods désactivés.
    state_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pushed_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    disabled_files: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    last_push_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_push_error: Mapped[str | None] = mapped_column(Text)


class LauncherFile(Base):
    """Un fichier installé par une synchronisation."""

    __tablename__ = "launcher_files"
    __table_args__ = (UniqueConstraint("server_id", "path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Chemin du manifest, sans le suffixe `.disabled`.
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Date du fichier à l'installation : tant qu'elle ne bouge pas, pas de re-hachage.
    mtime_ns: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    installed_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
