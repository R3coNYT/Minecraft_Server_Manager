"""Schémas des tâches programmées, des notifications et des téléchargements."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
#  Tâches programmées
# --------------------------------------------------------------------------- #
class RuleIn(BaseModel):
    """Règle de déclenchement, validée côté serveur."""

    trigger: str
    interval_minutes: int | None = None
    hour: int | None = None
    minute: int | None = 0
    days: list[int] | None = None
    timezone: str = "UTC"


class ScheduleCreateRequest(BaseModel):
    name: str
    action: str
    rule: RuleIn
    payload: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ScheduleUpdateRequest(BaseModel):
    name: str | None = None
    rule: RuleIn | None = None
    payload: dict[str, Any] | None = None
    enabled: bool | None = None


class ScheduleOut(BaseModel):
    id: int
    server_id: int
    name: str
    action: str
    payload: dict[str, Any]
    rule: dict[str, Any]
    #: Résumé lisible calculé côté serveur, pour rester cohérent avec l'audit.
    summary: str
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_status: str
    last_error: str | None


# --------------------------------------------------------------------------- #
#  Notifications
# --------------------------------------------------------------------------- #
class NotificationEventOut(BaseModel):
    key: str
    label: str


class NotificationSettingsOut(BaseModel):
    """Réglages renvoyés à l'interface — jamais l'adresse du webhook."""

    enabled: bool
    events: list[str]
    webhook_configured: bool
    webhook_hint: str | None
    webhook_unreadable: bool


class NotificationSettingsRequest(BaseModel):
    enabled: bool | None = None
    events: list[str] | None = None
    webhook_url: str | None = None
    clear_webhook: bool = False


# --------------------------------------------------------------------------- #
#  Téléchargements
# --------------------------------------------------------------------------- #
class DownloadSourceOut(BaseModel):
    key: str
    label: str


class VersionOut(BaseModel):
    id: str
    channel: str
    minecraft_version: str


class InstallRequest(BaseModel):
    source: str
    version: str


class InstallOut(BaseModel):
    file: str
    path: str
    previous_jar: str | None
    size_bytes: int
    version: str
