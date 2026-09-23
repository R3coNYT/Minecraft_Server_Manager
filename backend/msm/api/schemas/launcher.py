"""Schémas de l'intégration launcher."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LauncherConfigRequest(BaseModel):
    """Réglages envoyés par l'interface. Le jeton n'est jamais renvoyé."""

    file_server_url: str = Field(min_length=1, max_length=512)
    sync_paths: list[str] | None = None
    interval_minutes: int = 30
    enabled: bool = True
    #: Nouveau jeton d'écriture ; absent = inchangé.
    push_token: str | None = Field(default=None, max_length=512)
    clear_push_token: bool = False


class SyncRequest(BaseModel):
    #: Confirme une suppression massive que la synchronisation a bloquée.
    allow_mass_delete: bool = False


class SideRequest(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    #: `client`, `server`, `both` ; `null` revient à la détection automatique.
    side: str | None = None


class PendingOut(BaseModel):
    installs: int
    removes: int
    download_bytes: int
    server_running: bool


class PublishOut(BaseModel):
    state_revision: int
    pushed_revision: int
    up_to_date: bool
    disabled_files: list[str]
    last_push_at: datetime | None
    last_push_error: str | None


class LauncherModOut(BaseModel):
    path: str
    sha256: str
    size: int
    side: str
    #: `override`, `manifest`, `detected` ou `default`.
    side_source: str
    declared_side: str | None = None
    override: str | None = None
    disabled_upstream: bool = False
    #: `enabled`, `disabled` ou `absent` sur le serveur Minecraft.
    on_server: str


class LauncherOut(BaseModel):
    enabled: bool
    file_server_url: str
    sync_paths: list[str]
    interval_minutes: int
    push_configured: bool
    push_token_hint: str | None
    push_token_unreadable: bool
    next_sync_at: datetime | None
    last_sync_at: datetime | None
    last_sync_status: str
    last_sync_error: str | None
    last_sync_summary: dict[str, Any]
    pack_version: str | None
    pending: PendingOut | None
    publish: PublishOut
    mods: list[LauncherModOut]
