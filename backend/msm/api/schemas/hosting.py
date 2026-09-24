"""Schémas de l'hébergement : dossiers des comptes, plage de ports, quotas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class QuotaModel(BaseModel):
    """Limites d'un compte. ``null`` : pas de limite."""

    max_servers: int | None = Field(default=None, ge=0, le=1000)
    max_memory_per_server_mb: int | None = Field(default=None, ge=256, le=1_048_576)
    max_memory_total_mb: int | None = Field(default=None, ge=256, le=4_194_304)
    max_disk_mb: int | None = Field(default=None, ge=100, le=100_000_000)


class HostingSettingsModel(BaseModel):
    #: Dossier sous lequel chaque compte reçoit le sien ; vide : première racine autorisée.
    users_root: str | None = Field(default=None, max_length=1024)
    port_min: int = Field(default=25565, ge=1024, le=65535)
    port_max: int = Field(default=25664, ge=1024, le=65535)
    #: Quotas par défaut des comptes (les admins de MSM n'en ont pas).
    quota: QuotaModel = Field(default_factory=QuotaModel)
    #: Racine effective, calculée : lecture seule.
    users_root_effective: str | None = None


class UsageOut(BaseModel):
    servers: int
    memory_online_mb: int
    disk_mb: float


class MyQuotaOut(BaseModel):
    #: ``null`` : pas de limite (admin de MSM).
    quota: QuotaModel | None
    usage: UsageOut
