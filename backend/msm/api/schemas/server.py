"""Schémas des serveurs : configuration, détection, état."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from msm.core.restart_policy import AutoRestartMode
from msm.minecraft.types import ServerType


class ServerSettingsIn(BaseModel):
    """Réglages modifiables d'un serveur. Tous facultatifs : mise à jour partielle."""

    java_path: str | None = Field(default=None, max_length=1024)
    jar_path: str | None = Field(default=None, max_length=1024)
    script_path: str | None = Field(default=None, max_length=1024)
    custom_argv: list[str] | None = None
    jvm_args: list[str] | None = None
    extra_args: list[str] | None = None
    env: dict[str, str] | None = None

    memory_min_mb: int | None = Field(default=None, ge=128, le=1_048_576)
    memory_max_mb: int | None = Field(default=None, ge=128, le=1_048_576)
    port: int | None = Field(default=None, ge=1, le=65535)

    stop_command: str | None = Field(default=None, max_length=64)
    stop_timeout_s: float | None = Field(default=None, gt=0, le=3600)
    kill_timeout_s: float | None = Field(default=None, gt=0, le=3600)
    start_timeout_s: float | None = Field(default=None, gt=0, le=7200)

    auto_restart: AutoRestartMode | None = None
    restart_delay_s: float | None = Field(default=None, ge=0, le=3600)
    max_consecutive_crashes: int | None = Field(default=None, ge=1, le=100)
    autostart_on_boot: bool | None = None
    auto_accept_eula: bool | None = None
    log_history_lines: int | None = Field(default=None, ge=100, le=200_000)
    use_pty: bool | None = None


class ServerSettingsOut(BaseModel):
    """Réglages renvoyés au client. Le mot de passe RCON n'y figure jamais."""

    model_config = ConfigDict(from_attributes=True)

    java_path: str | None = None
    jar_path: str | None = None
    script_path: str | None = None
    custom_argv: list[str] = Field(default_factory=list)
    jvm_args: list[str] = Field(default_factory=list)
    extra_args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    memory_min_mb: int | None = None
    memory_max_mb: int | None = None
    port: int | None = None
    stop_command: str
    stop_timeout_s: float
    kill_timeout_s: float
    start_timeout_s: float
    auto_restart: AutoRestartMode
    restart_delay_s: float
    max_consecutive_crashes: int
    autostart_on_boot: bool
    auto_accept_eula: bool
    log_history_lines: int
    use_pty: bool
    rcon_enabled: bool


class ServerCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    directory: str = Field(min_length=1, max_length=1024)
    launcher_key: str = Field(min_length=1, max_length=32)
    server_type: ServerType = ServerType.UNKNOWN
    minecraft_version: str | None = Field(default=None, max_length=32)
    description: str | None = None
    settings: ServerSettingsIn | None = None


class ServerUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    directory: str | None = Field(default=None, min_length=1, max_length=1024)
    launcher_key: str | None = Field(default=None, min_length=1, max_length=32)
    server_type: ServerType | None = None
    minecraft_version: str | None = Field(default=None, max_length=32)
    description: str | None = None
    enabled: bool | None = None
    sort_order: int | None = None
    color: str | None = Field(default=None, max_length=16)
    settings: ServerSettingsIn | None = None


class ServerOut(BaseModel):
    """Serveur tel qu'exposé par l'API, état runtime compris."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    description: str | None = None
    directory: str
    server_type: ServerType
    minecraft_version: str | None = None
    launcher_key: str
    enabled: bool
    sort_order: int
    color: str | None = None
    settings: ServerSettingsOut | None = None
    #: Onglets réellement disponibles, déduits du contenu du dossier.
    capabilities: list[str] = Field(default_factory=list)
    #: Instantané du runtime ; ``None`` si le serveur n'est pas sous supervision.
    status: dict[str, Any] | None = None


class JarCandidateOut(BaseModel):
    name: str
    size_bytes: int
    server_type: ServerType
    minecraft_version: str | None = None
    score: int


class DetectRequest(BaseModel):
    directory: str = Field(min_length=1, max_length=1024)


class DetectionOut(BaseModel):
    """Résultat d'analyse d'un dossier : des suggestions, pas des décisions."""

    directory: str
    exists: bool
    server_type: ServerType
    minecraft_version: str | None = None
    launcher_key: str | None = None
    jar_path: str | None = None
    script_path: str | None = None
    jars: list[JarCandidateOut] = Field(default_factory=list)
    scripts: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    eula_accepted: bool | None = None
    port: int | None = None
    notes: list[str] = Field(default_factory=list)


class DashboardOut(BaseModel):
    """Vue d'ensemble du tableau de bord."""

    summary: dict[str, Any]
    servers: list[ServerOut]
    system: dict[str, Any]
