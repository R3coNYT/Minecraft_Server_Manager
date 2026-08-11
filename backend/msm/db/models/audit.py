"""Journal d'audit.

Table en **ajout seul** : l'application n'expose ni modification ni suppression.
Un journal qu'on peut réécrire ne prouve rien.

Les champs ``actor_username`` et ``actor_role`` sont des copies figées au moment
de l'action : si le compte est supprimé ou change de rôle plus tard, le journal
continue de dire qui a fait quoi, avec quels droits, à ce moment-là.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from msm.db.base import Base
from msm.db.types import UtcDateTime


class AuditAction(str, Enum):
    """Nature de l'action journalisée."""

    # Authentification
    LOGIN = "auth.login"
    LOGIN_FAILED = "auth.login_failed"
    LOGOUT = "auth.logout"
    PASSWORD_CHANGED = "auth.password_changed"

    # Serveurs
    SERVER_CREATED = "server.created"
    SERVER_UPDATED = "server.updated"
    SERVER_DELETED = "server.deleted"
    SERVER_STARTED = "server.started"
    SERVER_STOPPED = "server.stopped"
    SERVER_RESTARTED = "server.restarted"
    SERVER_KILLED = "server.killed"

    # Console
    COMMAND_SENT = "console.command"

    # Joueurs
    PLAYER_OP = "player.op"
    PLAYER_DEOP = "player.deop"
    PLAYER_KICKED = "player.kicked"
    PLAYER_BANNED = "player.banned"
    PLAYER_UNBANNED = "player.unbanned"
    PLAYER_KILLED = "player.killed"
    PLAYER_GIVE = "player.give"
    PLAYER_TELEPORTED = "player.teleported"

    # Fichiers
    FILE_UPLOADED = "file.uploaded"
    FILE_DELETED = "file.deleted"
    FILE_ENABLED = "file.enabled"
    FILE_DISABLED = "file.disabled"
    CONFIG_UPDATED = "config.updated"
    PROPERTIES_UPDATED = "properties.updated"
    EULA_ACCEPTED = "server.eula_accepted"

    # Événements
    EVENT_RUN = "event.run"

    # Sauvegardes
    BACKUP_CREATED = "backup.created"
    BACKUP_RESTORED = "backup.restored"
    BACKUP_DELETED = "backup.deleted"
    #: Une archive emporte les mondes hors de la machine : la sortie se journalise.
    BACKUP_DOWNLOADED = "backup.downloaded"

    # Tâches programmées
    SCHEDULE_UPDATED = "schedule.updated"
    SCHEDULE_RUN = "schedule.run"

    # Administration
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_DELETED = "user.deleted"
    PERMISSIONS_UPDATED = "user.permissions_updated"
    SETTINGS_UPDATED = "settings.updated"


class AuditResult(str, Enum):
    SUCCESS = "SUCCESS"
    DENIED = "DENIED"
    ERROR = "ERROR"


class AuditLog(Base):
    """Une entrée du journal d'audit."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_ts_desc", "ts"),
        Index("ix_audit_logs_server_ts", "server_id", "ts"),
        Index("ix_audit_logs_actor_ts", "actor_id", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    #: Nul pour les actions du système (redémarrage automatique, tâche planifiée).
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_username: Mapped[str] = mapped_column(String(64), nullable=False, default="système")
    actor_role: Mapped[str | None] = mapped_column(String(16))
    ip_address: Mapped[str | None] = mapped_column(String(45))

    action: Mapped[AuditAction] = mapped_column(
        SAEnum(AuditAction, native_enum=False, length=48), nullable=False, index=True
    )
    result: Mapped[AuditResult] = mapped_column(
        SAEnum(AuditResult, native_enum=False, length=8),
        nullable=False,
        default=AuditResult.SUCCESS,
    )

    server_id: Mapped[int | None] = mapped_column(ForeignKey("servers.id", ondelete="SET NULL"))
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(128))

    #: Phrase lisible affichée dans l'interface d'audit.
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    #: Détail structuré (commande exacte, ancienne/nouvelle valeur…).
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
