"""Schémas de la création de serveurs de zéro."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DistributionOut(BaseModel):
    key: str
    label: str
    server_type: str
    #: Une version de Minecraft se décline-t-elle en builds (loader, NeoForge…) ?
    has_builds: bool
    #: `installer` : un installeur s'exécute pendant la création (NeoForge).
    kind: str


class BuildOut(BaseModel):
    id: str
    label: str
    channel: str


class ProvisioningDefaultsOut(BaseModel):
    #: Racines autorisées pour les dossiers de serveurs ; vide = chemin libre.
    roots: list[str]
    #: Dossier proposé pour le nom donné : dans le dossier du compte.
    directory: str
    port: int
    #: Imposés pour un compte qui n'est pas admin de MSM : l'interface les affiche
    #: sans les laisser modifier.
    directory_locked: bool = False
    port_locked: bool = False
    #: Mémoire maximale permise par serveur ; ``None`` : pas de limite.
    max_memory_mb: int | None = None


class ProvisioningRequestIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    #: Choisi par un admin ; imposé (dossier du compte) pour les autres.
    directory: str | None = Field(default=None, max_length=1024)
    distribution: str = Field(min_length=1, max_length=32)
    version: str = Field(min_length=1, max_length=64)
    build: str | None = Field(default=None, max_length=64)
    memory_min_mb: int = Field(default=2048, ge=1, le=1024 * 1024)
    memory_max_mb: int = Field(default=4096, ge=1, le=1024 * 1024)
    #: Choisi par un admin ; attribué dans la plage des ports pour les autres.
    port: int | None = None
    #: Le CLUF de Mojang n'est accepté que si l'utilisateur l'a coché.
    accept_eula: bool = False
    start_after: bool = False


class ProvisioningStepOut(BaseModel):
    key: str
    label: str
    status: str
    detail: str


class ProvisioningErrorOut(BaseModel):
    message: str
    cause: str | None = None
    remediation: str | None = None


class ProvisioningJobOut(BaseModel):
    id: str
    name: str
    directory: str
    distribution: str
    version: str
    build: str | None
    status: str
    progress: float | None
    downloaded_bytes: int
    steps: list[ProvisioningStepOut]
    error: ProvisioningErrorOut | None
    server_id: int | None
    created_at: str
    finished_at: str | None
