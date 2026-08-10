"""Schémas de la console et du journal d'audit."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from msm.core.log_line import LogLevel


class LogLineOut(BaseModel):
    """Une ligne de console."""

    seq: int
    ts: str
    text: str
    level: LogLevel
    thread: str | None = None
    category: str | None = None
    source: str
    server_time: str | None = None


class LogsOut(BaseModel):
    """Fenêtre d'historique de console.

    ``dropped`` indique le nombre de lignes sorties du tampon : l'interface peut
    ainsi afficher « X lignes antérieures non conservées » plutôt que de laisser
    croire à un historique complet.
    """

    lines: list[LogLineOut]
    first_seq: int | None = None
    last_seq: int | None = None
    dropped: int = 0


class CommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=32_500)
    #: Doit valoir ``true`` pour exécuter une commande sensible ou destructrice.
    confirm: bool = False


class CommandOut(BaseModel):
    command: str
    danger: str


class CommandInspectOut(BaseModel):
    """Analyse préalable d'une commande, pour la boîte de confirmation."""

    command: str
    danger: str
    requires_confirmation: bool
    requires_strong_confirmation: bool
    explanation: str | None = None


class StopOut(BaseModel):
    stage: str
    forced: bool
    exit_code: int | None = None
    duration_s: float
    status: dict[str, Any]


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    actor_username: str
    actor_role: str | None = None
    ip_address: str | None = None
    action: str
    result: str
    server_id: int | None = None
    target_type: str | None = None
    target_id: str | None = None
    summary: str
    payload: dict[str, Any] | None = None


class AuditPageOut(BaseModel):
    entries: list[AuditEntryOut]
    total: int
    limit: int
    offset: int
