"""Serveurs Minecraft : identité, réglages, état persistant, droits par serveur."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from msm.core.restart_policy import AutoRestartMode
from msm.core.states import ServerState
from msm.db.base import Base, TimestampMixin
from msm.minecraft.types import ServerType

if TYPE_CHECKING:
    from msm.db.models.user import User


class Server(Base, TimestampMixin):
    """Un serveur Minecraft géré par le panel."""

    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)

    #: Chemin absolu du dossier du serveur. Toute opération sur fichier est
    #: confinée à ce répertoire ; il n'est modifiable qu'après revalidation.
    directory: Mapped[str] = mapped_column(String(1024), nullable=False)

    server_type: Mapped[ServerType] = mapped_column(
        Enum(ServerType, native_enum=False, length=24),
        nullable=False,
        default=ServerType.UNKNOWN,
    )
    minecraft_version: Mapped[str | None] = mapped_column(String(32))
    #: Clé du launcher (`jar`, `shell`, `batch`, `custom`) — voir le registre.
    launcher_key: Mapped[str] = mapped_column(String(32), nullable=False, default="jar")

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    color: Mapped[str | None] = mapped_column(String(16))

    settings: Mapped[ServerSettings] = relationship(
        back_populates="server", cascade="all, delete-orphan", uselist=False
    )
    runtime_state: Mapped[ServerRuntimeStateRow] = relationship(
        back_populates="server", cascade="all, delete-orphan", uselist=False
    )
    permissions: Mapped[list[ServerPermission]] = relationship(
        back_populates="server", cascade="all, delete-orphan"
    )


class ServerSettings(Base, TimestampMixin):
    """Réglages de démarrage et d'exploitation d'un serveur."""

    __tablename__ = "server_settings"

    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True
    )

    # --- Démarrage -------------------------------------------------------
    java_path: Mapped[str | None] = mapped_column(String(1024))
    jar_path: Mapped[str | None] = mapped_column(String(1024))
    script_path: Mapped[str | None] = mapped_column(String(1024))
    #: Liste d'arguments du launcher « personnalisé ». Jamais une chaîne shell.
    custom_argv: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    jvm_args: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    extra_args: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    env: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)

    memory_min_mb: Mapped[int | None] = mapped_column(Integer)
    memory_max_mb: Mapped[int | None] = mapped_column(Integer)
    port: Mapped[int | None] = mapped_column(Integer)

    # --- Arrêt -----------------------------------------------------------
    stop_command: Mapped[str] = mapped_column(String(64), nullable=False, default="stop")
    stop_timeout_s: Mapped[float] = mapped_column(Float, nullable=False, default=60.0)
    kill_timeout_s: Mapped[float] = mapped_column(Float, nullable=False, default=15.0)
    start_timeout_s: Mapped[float] = mapped_column(Float, nullable=False, default=300.0)

    # --- Exploitation ----------------------------------------------------
    auto_restart: Mapped[AutoRestartMode] = mapped_column(
        Enum(AutoRestartMode, native_enum=False, length=16),
        nullable=False,
        default=AutoRestartMode.NEVER,
    )
    restart_delay_s: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)
    max_consecutive_crashes: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    autostart_on_boot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_accept_eula: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    log_history_lines: Mapped[int] = mapped_column(Integer, nullable=False, default=5000)
    use_pty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- RCON (canal secondaire, facultatif) -----------------------------
    rcon_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rcon_port: Mapped[int | None] = mapped_column(Integer)
    #: Chiffré avec la clé applicative — jamais stocké en clair.
    rcon_password_enc: Mapped[str | None] = mapped_column(String(512))

    server: Mapped[Server] = relationship(back_populates="settings")


class ServerRuntimeStateRow(Base):
    """État du processus, persisté pour permettre la réadoption au redémarrage.

    ``process_create_time`` est indispensable : un PID seul peut avoir été recyclé
    par le système et désigner un processus totalement étranger. Comparer aussi la
    date de création évite d'envoyer un signal à la mauvaise cible.
    """

    __tablename__ = "server_runtime_state"

    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True
    )

    state: Mapped[ServerState] = mapped_column(
        Enum(ServerState, native_enum=False, length=16),
        nullable=False,
        default=ServerState.OFFLINE,
    )
    pid: Mapped[int | None] = mapped_column(Integer)
    group_id: Mapped[int | None] = mapped_column(Integer)
    process_create_time: Mapped[float | None] = mapped_column(Float)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_exit_code: Mapped[int | None] = mapped_column(Integer)
    consecutive_crashes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stop_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    server: Mapped[Server] = relationship(back_populates="runtime_state")


class ServerPermission(Base, TimestampMixin):
    """Surcharge de droits d'un utilisateur sur un serveur donné.

    Permet à un modérateur d'administrer certains serveurs et pas d'autres, sans
    créer un rôle global par combinaison.
    """

    __tablename__ = "server_permissions"
    __table_args__ = (UniqueConstraint("user_id", "server_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: Permissions ajoutées au rôle, sous forme de valeurs de `Permission`.
    granted: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    #: Permissions retirées. En cas de conflit, le refus l'emporte.
    revoked: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    user: Mapped[User] = relationship(back_populates="server_permissions")
    server: Mapped[Server] = relationship(back_populates="permissions")
