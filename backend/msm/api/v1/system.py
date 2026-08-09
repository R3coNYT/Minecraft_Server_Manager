"""Points d'entrée système : santé, version, ressources de la machine."""

from __future__ import annotations

import platform
import sys
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from msm import __version__
from msm.launchers import registry as launcher_registry
from msm.runtime.backends import get_backend
from msm.runtime.stats import system_stats

router = APIRouter(tags=["système"])


class HealthResponse(BaseModel):
    """Réponse de la sonde de santé."""

    status: str
    version: str
    python: str
    platform: str
    process_backend: str
    servers_registered: int


@router.get("/health", response_model=HealthResponse, summary="Sonde de santé")
async def health(request: Request) -> HealthResponse:
    """Vérifie que l'application répond et expose son environnement d'exécution."""
    supervisor = getattr(request.app.state, "supervisor", None)
    return HealthResponse(
        status="ok",
        version=__version__,
        python=sys.version.split()[0],
        platform=f"{platform.system()} {platform.release()}",
        process_backend=get_backend().name,
        servers_registered=len(supervisor) if supervisor is not None else 0,
    )


@router.get("/system/stats", summary="Ressources de la machine hôte")
async def host_stats() -> dict[str, Any]:
    """CPU, mémoire et disque de la machine, pour le tableau de bord."""
    return system_stats()


@router.get("/system/launchers", summary="Méthodes de démarrage disponibles")
async def launchers() -> list[dict[str, str | None]]:
    """Liste les launchers, avec la raison d'indisponibilité le cas échéant.

    Permet à l'interface de griser les options impossibles sur cette machine
    plutôt que de laisser l'utilisateur découvrir l'échec au démarrage.
    """
    return launcher_registry.describe_all()
