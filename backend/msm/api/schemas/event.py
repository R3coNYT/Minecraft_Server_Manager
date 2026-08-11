"""Schémas des événements."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ActionFieldOut(BaseModel):
    """Un champ de formulaire, décrit par l'action elle-même."""

    name: str
    label: str
    type: str
    required: bool
    default: Any = None
    placeholder: str = ""
    help: str = ""
    minimum: int | None = None
    maximum: int | None = None


class ActionOut(BaseModel):
    """Un type d'action disponible. Le frontend en déduit son formulaire."""

    key: str
    label: str
    description: str
    danger: str
    fields: list[ActionFieldOut] = Field(default_factory=list)


class StepIn(BaseModel):
    action: str = Field(min_length=1, max_length=32)
    params: dict[str, Any] = Field(default_factory=dict)


class StepOut(BaseModel):
    action: str
    params: dict[str, Any]
    #: Résumé lisible, calculé côté serveur pour rester cohérent avec l'audit.
    summary: str = ""


class EventCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    steps: list[StepIn] = Field(min_length=1)


class EventUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    steps: list[StepIn] | None = None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    #: Nul pour un modèle global, réutilisable sur n'importe quel serveur.
    server_id: int | None = None
    steps: list[StepOut] = Field(default_factory=list)
    #: Niveau de risque le plus élevé de la séquence.
    danger: str = "SAFE"


class QuickActionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=32)
    params: dict[str, Any] = Field(default_factory=dict)
    #: Doit valoir ``true`` pour une action destructrice comme `kill @a`.
    confirm: bool = False


class QuickActionOut(BaseModel):
    summary: str
    commands: list[str] = Field(default_factory=list)


class RunRequest(BaseModel):
    confirm: bool = False


class EventRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int | None = None
    status: str
    current_step: int
    total_steps: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
