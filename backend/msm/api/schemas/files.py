"""Schémas des fichiers gérés et des configurations."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ManagedFileOut(BaseModel):
    """Un mod ou un plugin."""

    name: str
    size_bytes: int
    modified_at: str
    enabled: bool


class ToggleRequest(BaseModel):
    enabled: bool


class ConfigEntryOut(BaseModel):
    """Une entrée de l'arborescence de configuration."""

    name: str
    path: str
    is_directory: bool
    size_bytes: int = 0
    modified_at: str = ""
    format: str = ""
    #: Faux pour un fichier trop volumineux pour l'éditeur.
    editable: bool = False


class ConfigFileOut(BaseModel):
    path: str
    name: str
    format: str
    content: str
    encoding: str
    size_bytes: int
    modified_at: str


class ConfigWriteRequest(BaseModel):
    content: str = Field(max_length=2 * 1024 * 1024)


class ConfigWriteOut(BaseModel):
    path: str
    size_bytes: int
    modified_at: str


class PropertyOut(BaseModel):
    """Une clé de server.properties, avec de quoi construire son champ."""

    key: str
    value: str
    known: bool
    label: str
    type: str
    choices: list[str] = Field(default_factory=list)
    minimum: int | None = None
    maximum: int | None = None
    requires_restart: bool = True
    help: str = ""


class PropertiesOut(BaseModel):
    exists: bool
    entries: list[PropertyOut] = Field(default_factory=list)


class PropertiesUpdateRequest(BaseModel):
    changes: dict[str, Any] = Field(default_factory=dict)


class PropertiesUpdateOut(BaseModel):
    updated: list[str]
    #: Vrai si au moins une clé modifiée n'est prise en compte qu'au redémarrage.
    requires_restart: bool
