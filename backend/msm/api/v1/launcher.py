"""Intégration d'un serveur avec le serveur de fichiers de son launcher."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from msm.api.deps import (
    AppSettings,
    ClientIp,
    CsrfProtected,
    DbSession,
    ServerAccess,
    SupervisorDep,
)
from msm.api.schemas.launcher import (
    LauncherConfigRequest,
    LauncherOut,
    SideRequest,
    SyncRequest,
)
from msm.db.models.launcher import LauncherIntegration
from msm.runtime.supervisor import Supervisor
from msm.services.launcher_service import LauncherService, describe

router = APIRouter(tags=["launcher"])


def _service(
    request: Request, session: DbSession, supervisor: SupervisorDep, settings: AppSettings
) -> LauncherService:
    syncer = getattr(request.app.state, "launcher_syncer", None)
    return LauncherService(
        session,
        supervisor,
        settings=settings,
        transport=getattr(syncer, "transport", None),
    )


LauncherDep = Annotated[LauncherService, Depends(_service)]


def _out(integration: LauncherIntegration, supervisor: Supervisor) -> LauncherOut:
    runtime = supervisor.find(integration.server_id)
    running = runtime is not None and runtime.state.is_running
    return LauncherOut.model_validate(describe(integration, running=running))


@router.get(
    "/servers/{server_id}/launcher",
    response_model=LauncherOut,
    summary="Launcher integration status",
    responses={204: {"description": "No integration configured"}},
)
async def get_integration(
    access: ServerAccess, service: LauncherDep, supervisor: SupervisorDep
) -> LauncherOut | Response:
    server, _ = access
    integration = await service.get(server)
    if integration is None:
        # Pas une erreur : la plupart des serveurs n'ont pas de launcher dédié.
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return _out(integration, supervisor)


@router.put(
    "/servers/{server_id}/launcher",
    response_model=LauncherOut,
    summary="Configure the launcher integration",
    dependencies=[CsrfProtected],
)
async def configure_integration(
    payload: LauncherConfigRequest,
    access: ServerAccess,
    service: LauncherDep,
    supervisor: SupervisorDep,
    ip: ClientIp,
) -> LauncherOut:
    """Le jeton d'écriture est chiffré en base et n'est jamais renvoyé."""
    server, context = access
    integration = await service.configure(
        server,
        file_server_url=payload.file_server_url,
        sync_paths=payload.sync_paths,
        interval_minutes=payload.interval_minutes,
        enabled=payload.enabled,
        push_token=payload.push_token,
        clear_push_token=payload.clear_push_token,
        context=context,
        ip_address=ip,
    )
    return _out(integration, supervisor)


@router.delete(
    "/servers/{server_id}/launcher",
    summary="Remove the launcher integration",
    dependencies=[CsrfProtected],
)
async def remove_integration(
    access: ServerAccess, service: LauncherDep, ip: ClientIp
) -> dict[str, str]:
    """Les mods déjà installés restent en place."""
    server, context = access
    await service.remove(server, context=context, ip_address=ip)
    return {"status": "removed"}


@router.post(
    "/servers/{server_id}/launcher/sync",
    response_model=LauncherOut,
    summary="Synchronise now",
    dependencies=[CsrfProtected],
)
async def sync_integration(
    payload: SyncRequest,
    access: ServerAccess,
    service: LauncherDep,
    supervisor: SupervisorDep,
    ip: ClientIp,
) -> LauncherOut:
    """Si le serveur tourne, les changements sont mis en attente du prochain démarrage."""
    server, context = access
    integration = await service.sync_now(
        server, allow_mass_delete=payload.allow_mass_delete, context=context, ip_address=ip
    )
    return _out(integration, supervisor)


@router.post(
    "/servers/{server_id}/launcher/publish",
    response_model=LauncherOut,
    summary="Publish mod state to players",
    dependencies=[CsrfProtected],
)
async def publish_integration(
    access: ServerAccess, service: LauncherDep, supervisor: SupervisorDep
) -> LauncherOut:
    server, context = access
    return _out(await service.publish_now(server, context=context), supervisor)


@router.put(
    "/servers/{server_id}/launcher/side",
    response_model=LauncherOut,
    summary="Override a mod's side",
    dependencies=[CsrfProtected],
)
async def set_side(
    payload: SideRequest,
    access: ServerAccess,
    service: LauncherDep,
    supervisor: SupervisorDep,
) -> LauncherOut:
    """Un mod marqué `client` n'est jamais installé sur le serveur."""
    server, context = access
    integration = await service.set_side(server, payload.path, payload.side, context=context)
    return _out(integration, supervisor)
