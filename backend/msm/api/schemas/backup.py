"""Schémas des sauvegardes et de l'historique des ressources."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BackupOut(BaseModel):
    """Une sauvegarde, telle que listée dans l'interface."""

    id: int
    server_id: int
    kind: str
    status: str
    size_bytes: int | None
    created_at: datetime
    created_by: int | None
    error: str | None
    #: Faux tant que l'archive n'est pas écrite : conditionne téléchargement
    #: et restauration.
    available: bool


class BackupManifestOut(BaseModel):
    """Contenu déclaré par une archive, lu sans rien extraire."""

    created_at: str | None = None
    msm_version: str | None = None
    server: dict[str, Any] = Field(default_factory=dict)
    content: dict[str, Any] = Field(default_factory=dict)
    mods: list[dict[str, Any]] = Field(default_factory=list)
    plugins: list[dict[str, Any]] = Field(default_factory=list)


class RestoreRequest(BaseModel):
    """Restauration : la confirmation est explicite, jamais implicite."""

    confirm: bool = False


class MetricPointOut(BaseModel):
    ts: datetime
    cpu_percent: float
    memory_mb: float
    players_online: int


class MetricsOut(BaseModel):
    """Historique agrégé d'un serveur sur une période."""

    range: str
    bucket_s: int
    points: list[MetricPointOut]
    peak_cpu_percent: float
    peak_memory_mb: float
    peak_players: int
