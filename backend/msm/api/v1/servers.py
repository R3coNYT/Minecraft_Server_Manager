"""Serveurs : consultation, configuration et cycle de vie."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from msm.api.deps import (
    ClientIp,
    CsrfProtected,
    CurrentUser,
    DbSession,
    GlobalContext,
    ServerAccess,
    ServerServiceDep,
    SupervisorDep,
    require_permission,
    require_server_permission,
)
from msm.api.schemas import (
    DashboardOut,
    DetectionOut,
    DetectRequest,
    ServerCreateRequest,
    ServerOut,
    ServerUpdateRequest,
    StopOut,
)
from msm.core.permissions import Permission
from msm.db.models.server import Server
from msm.runtime.stats import system_stats
from msm.security.rbac import AccessContext
from msm.services.lifecycle_service import LifecycleService

router = APIRouter(prefix="/servers", tags=["serveurs"])


async def _to_out(
    server: Server, service: ServerServiceDep, supervisor: SupervisorDep
) -> ServerOut:
    """Assemble la vue d'un serveur : configuration + capacités + état runtime."""
    runtime = supervisor.find(server.id)
    return ServerOut.model_validate(
        {
            **{
                field: getattr(server, field)
                for field in (
                    "id",
                    "name",
                    "slug",
                    "description",
                    "directory",
                    "server_type",
                    "minecraft_version",
                    "launcher_key",
                    "enabled",
                    "sort_order",
                    "color",
                )
            },
            "settings": server.settings,
            "capabilities": await service.capabilities(server),
            "status": runtime.snapshot() if runtime else None,
        }
    )


# --------------------------------------------------------------------------- #
#  Consultation
# --------------------------------------------------------------------------- #
@router.get("", response_model=list[ServerOut], summary="Lister les serveurs")
async def list_servers(
    service: ServerServiceDep,
    supervisor: SupervisorDep,
    context: GlobalContext,
) -> list[ServerOut]:
    """Serveurs visibles par l'utilisateur."""
    context.require(Permission.SERVER_VIEW, action="consulter les serveurs")
    return [await _to_out(server, service, supervisor) for server in await service.list_servers()]


@router.get("/dashboard", response_model=DashboardOut, summary="Tableau de bord")
async def dashboard(
    service: ServerServiceDep,
    supervisor: SupervisorDep,
    context: GlobalContext,
) -> DashboardOut:
    """Vue d'ensemble : agrégats, serveurs et ressources de la machine."""
    context.require(Permission.SERVER_VIEW, action="consulter le tableau de bord")
    servers = [
        await _to_out(server, service, supervisor) for server in await service.list_servers()
    ]
    return DashboardOut(
        summary=supervisor.summary(),
        servers=servers,
        system=system_stats(),
    )


@router.post(
    "/detect",
    response_model=DetectionOut,
    summary="Analyser un dossier",
    dependencies=[CsrfProtected],
)
async def detect_directory(
    payload: DetectRequest,
    service: ServerServiceDep,
    _: Annotated[AccessContext, Depends(require_permission(Permission.SERVER_CREATE))],
) -> DetectionOut:
    """Propose une configuration à partir du contenu d'un dossier.

    Aucune écriture : le résultat est une suggestion que l'administrateur reste
    libre de modifier avant de créer le serveur.
    """
    result = service.detect_directory(payload.directory)
    return DetectionOut(
        directory=str(result.directory),
        exists=result.exists,
        server_type=result.server_type,
        minecraft_version=result.minecraft_version,
        launcher_key=result.launcher_key,
        jar_path=result.jar_path,
        script_path=result.script_path,
        jars=[
            {
                "name": jar.name,
                "size_bytes": jar.size_bytes,
                "server_type": jar.server_type,
                "minecraft_version": jar.minecraft_version,
                "score": jar.score,
            }
            for jar in result.jars
        ],
        scripts=list(result.scripts),
        capabilities=sorted(capability.value for capability in result.capabilities),
        eula_accepted=result.eula_accepted,
        port=result.port,
        notes=list(result.notes),
    )


@router.get("/{server_id}", response_model=ServerOut, summary="Détail d'un serveur")
async def get_server(
    access: ServerAccess, service: ServerServiceDep, supervisor: SupervisorDep
) -> ServerOut:
    server, _ = access
    return await _to_out(server, service, supervisor)


@router.get("/{server_id}/status", summary="État du serveur")
async def server_status(access: ServerAccess, supervisor: SupervisorDep) -> dict[str, Any]:
    """Instantané du runtime, sans relire la base."""
    server, _ = access
    runtime = supervisor.find(server.id)
    return runtime.snapshot() if runtime else {"id": server.id, "state": "UNKNOWN"}


# --------------------------------------------------------------------------- #
#  Configuration
# --------------------------------------------------------------------------- #
@router.post(
    "",
    response_model=ServerOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ajouter un serveur",
    dependencies=[CsrfProtected],
)
async def create_server(
    payload: ServerCreateRequest,
    service: ServerServiceDep,
    supervisor: SupervisorDep,
    user: CurrentUser,
    ip: ClientIp,
    _: Annotated[AccessContext, Depends(require_permission(Permission.SERVER_CREATE))],
) -> ServerOut:
    server = await service.create_server(
        name=payload.name,
        directory=payload.directory,
        launcher_key=payload.launcher_key,
        server_type=payload.server_type,
        minecraft_version=payload.minecraft_version,
        description=payload.description,
        settings_overrides=(
            payload.settings.model_dump(exclude_none=True) if payload.settings else None
        ),
        actor=user,
        ip_address=ip,
    )
    return await _to_out(server, service, supervisor)


@router.put(
    "/{server_id}",
    response_model=ServerOut,
    summary="Modifier un serveur",
    dependencies=[CsrfProtected],
)
async def update_server(
    payload: ServerUpdateRequest,
    access: Annotated[
        tuple[Server, AccessContext], Depends(require_server_permission(Permission.SERVER_EDIT))
    ],
    service: ServerServiceDep,
    supervisor: SupervisorDep,
    user: CurrentUser,
    ip: ClientIp,
) -> ServerOut:
    server, _ = access
    changes = payload.model_dump(exclude_unset=True, exclude={"settings"})
    settings_changes = payload.settings.model_dump(exclude_none=True) if payload.settings else None
    server = await service.update_server(
        server,
        changes=changes,
        settings_changes=settings_changes,
        actor=user,
        ip_address=ip,
    )
    return await _to_out(server, service, supervisor)


@router.delete(
    "/{server_id}",
    summary="Retirer un serveur du panel",
    dependencies=[CsrfProtected],
)
async def delete_server(
    access: Annotated[
        tuple[Server, AccessContext], Depends(require_server_permission(Permission.SERVER_DELETE))
    ],
    service: ServerServiceDep,
    user: CurrentUser,
    ip: ClientIp,
) -> dict[str, str]:
    """Retire le serveur du panel. **Aucun fichier n'est supprimé du disque.**"""
    server, _ = access
    name = server.name
    await service.delete_server(server, actor=user, ip_address=ip)
    return {
        "status": "supprimé",
        "detail": f"« {name} » a été retiré du panel ; ses fichiers sont intacts.",
    }


# --------------------------------------------------------------------------- #
#  Cycle de vie
# --------------------------------------------------------------------------- #
def _lifecycle(session: DbSession, supervisor: SupervisorDep) -> LifecycleService:
    return LifecycleService(session, supervisor)


LifecycleDep = Annotated[LifecycleService, Depends(_lifecycle)]


@router.post("/{server_id}/start", summary="Démarrer", dependencies=[CsrfProtected])
async def start_server(
    access: ServerAccess, lifecycle: LifecycleDep, ip: ClientIp
) -> dict[str, Any]:
    server, context = access
    return await lifecycle.start(server, context=context, ip_address=ip)


@router.post(
    "/{server_id}/stop",
    response_model=StopOut,
    summary="Arrêter",
    dependencies=[CsrfProtected],
)
async def stop_server(access: ServerAccess, lifecycle: LifecycleDep, ip: ClientIp) -> StopOut:
    server, context = access
    return StopOut(**await lifecycle.stop(server, context=context, ip_address=ip))


@router.post("/{server_id}/restart", summary="Redémarrer", dependencies=[CsrfProtected])
async def restart_server(
    access: ServerAccess, lifecycle: LifecycleDep, ip: ClientIp
) -> dict[str, Any]:
    server, context = access
    return await lifecycle.restart(server, context=context, ip_address=ip)


@router.post("/{server_id}/kill", summary="Arrêt forcé", dependencies=[CsrfProtected])
async def kill_server(
    access: ServerAccess, lifecycle: LifecycleDep, ip: ClientIp
) -> dict[str, Any]:
    """Terminaison immédiate du processus : le monde n'est pas sauvegardé."""
    server, context = access
    return await lifecycle.kill(server, context=context, ip_address=ip)


@router.get("/{server_id}/capabilities", summary="Fonctionnalités disponibles")
async def server_capabilities(access: ServerAccess, service: ServerServiceDep) -> list[str]:
    """Onglets à afficher, déduits du contenu réel du dossier du serveur."""
    server, _ = access
    return await service.capabilities(server)
