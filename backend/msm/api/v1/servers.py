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
    MemberOut,
    MemberRequest,
    ServerCreateRequest,
    ServerOut,
    ServerUpdateRequest,
    StopOut,
)
from msm.core.permissions import Permission, ServerRole
from msm.db.models.server import Server
from msm.db.models.user import User
from msm.db.repositories import ServerMemberRepository
from msm.i18n import tr
from msm.runtime.stats import system_stats
from msm.security.access import server_context
from msm.security.rbac import AccessContext, build_server_context
from msm.services.lifecycle_service import LifecycleService
from msm.services.member_service import MemberService

router = APIRouter(prefix="/servers", tags=["servers"])


async def _to_out(
    server: Server,
    service: ServerServiceDep,
    supervisor: SupervisorDep,
    context: AccessContext,
) -> ServerOut:
    """Assemble la vue d'un serveur : configuration, capacités, état, accès du compte."""
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
            "owner_id": server.owner_id,
            "owner_username": server.owner.username,
            "access": context.server_role,
            "shared": context.server_role not in (None, ServerRole.OWNER),
            "permissions": sorted(context.permissions, key=lambda item: item.value),
        }
    )


async def _visible(
    user: User, session: DbSession, service: ServerServiceDep, supervisor: SupervisorDep
) -> list[ServerOut]:
    """Les serveurs que ce compte voit, chacun avec ses droits dessus."""
    roles = await ServerMemberRepository(session).roles_for_user(user.id)
    return [
        await _to_out(
            server,
            service,
            supervisor,
            build_server_context(user, server, member_role=roles.get(server.id)),
        )
        for server in await service.list_servers(user)
    ]


def _summary(servers: list[ServerOut], supervisor: SupervisorDep) -> dict[str, Any]:
    """Agrégats du tableau de bord, calculés sur les seuls serveurs visibles."""
    runtimes = [runtime for server in servers if (runtime := supervisor.find(server.id))]
    online = [runtime for runtime in runtimes if runtime.state.is_running]
    return {
        "servers_total": len(servers),
        "servers_online": len(online),
        "servers_offline": len(servers) - len(online),
        "players_online": sum(len(runtime.online_players) for runtime in online),
        "cpu_percent": round(sum(runtime.stats.cpu_percent for runtime in online), 1),
        "memory_mb": round(sum(runtime.stats.memory_mb for runtime in online), 1),
    }


# --------------------------------------------------------------------------- #
#  Consultation
# --------------------------------------------------------------------------- #
@router.get("", response_model=list[ServerOut], summary="List servers")
async def list_servers(
    user: CurrentUser,
    session: DbSession,
    service: ServerServiceDep,
    supervisor: SupervisorDep,
) -> list[ServerOut]:
    """Serveurs visibles par l'utilisateur, les siens en premier."""
    return await _visible(user, session, service, supervisor)


@router.get("/dashboard", response_model=DashboardOut, summary="Dashboard")
async def dashboard(
    user: CurrentUser,
    session: DbSession,
    service: ServerServiceDep,
    supervisor: SupervisorDep,
    context: GlobalContext,
) -> DashboardOut:
    """Vue d'ensemble : agrégats et serveurs visibles, ressources de la machine pour l'admin."""
    servers = await _visible(user, session, service, supervisor)
    return DashboardOut(
        summary=_summary(servers, supervisor),
        servers=servers,
        system=system_stats() if context.has(Permission.SYSTEM_VIEW) else None,
    )


@router.post(
    "/detect",
    response_model=DetectionOut,
    summary="Analyse a folder",
    dependencies=[CsrfProtected],
)
async def detect_directory(
    payload: DetectRequest,
    service: ServerServiceDep,
    _: Annotated[AccessContext, Depends(require_permission(Permission.SERVER_REGISTER))],
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


@router.get("/{server_id}", response_model=ServerOut, summary="Server details")
async def get_server(
    access: ServerAccess, service: ServerServiceDep, supervisor: SupervisorDep
) -> ServerOut:
    server, context = access
    return await _to_out(server, service, supervisor, context)


@router.get("/{server_id}/status", summary="Server status")
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
    summary="Add a server",
    dependencies=[CsrfProtected],
)
async def create_server(
    payload: ServerCreateRequest,
    service: ServerServiceDep,
    supervisor: SupervisorDep,
    session: DbSession,
    user: CurrentUser,
    ip: ClientIp,
    _: Annotated[AccessContext, Depends(require_permission(Permission.SERVER_REGISTER))],
) -> ServerOut:
    """Enregistre un dossier existant de la machine : réservé aux admins de MSM."""
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
    return await _to_out(server, service, supervisor, await server_context(session, user, server))


@router.put(
    "/{server_id}",
    response_model=ServerOut,
    summary="Update a server",
    dependencies=[CsrfProtected],
)
async def update_server(
    payload: ServerUpdateRequest,
    access: ServerAccess,
    service: ServerServiceDep,
    supervisor: SupervisorDep,
    user: CurrentUser,
    ip: ClientIp,
) -> ServerOut:
    """Chaque champ modifié exige son propre droit (voir `ServerService.update_server`)."""
    server, context = access
    changes = payload.model_dump(exclude_unset=True, exclude={"settings"})
    settings_changes = payload.settings.model_dump(exclude_none=True) if payload.settings else None
    server = await service.update_server(
        server,
        changes=changes,
        settings_changes=settings_changes,
        context=context,
        actor=user,
        ip_address=ip,
    )
    return await _to_out(server, service, supervisor, context)


@router.delete(
    "/{server_id}",
    summary="Remove a server from the panel",
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
        "status": "deleted",
        "detail": tr(
            "“{name}” has been removed from the panel; its files are untouched.", name=name
        ),
    }


# --------------------------------------------------------------------------- #
#  Cycle de vie
# --------------------------------------------------------------------------- #
def _lifecycle(session: DbSession, supervisor: SupervisorDep) -> LifecycleService:
    return LifecycleService(session, supervisor)


LifecycleDep = Annotated[LifecycleService, Depends(_lifecycle)]


@router.post("/{server_id}/start", summary="Start", dependencies=[CsrfProtected])
async def start_server(
    access: ServerAccess, lifecycle: LifecycleDep, ip: ClientIp
) -> dict[str, Any]:
    server, context = access
    return await lifecycle.start(server, context=context, ip_address=ip)


@router.post(
    "/{server_id}/stop",
    response_model=StopOut,
    summary="Stop",
    dependencies=[CsrfProtected],
)
async def stop_server(access: ServerAccess, lifecycle: LifecycleDep, ip: ClientIp) -> StopOut:
    server, context = access
    return StopOut(**await lifecycle.stop(server, context=context, ip_address=ip))


@router.post("/{server_id}/restart", summary="Restart", dependencies=[CsrfProtected])
async def restart_server(
    access: ServerAccess, lifecycle: LifecycleDep, ip: ClientIp
) -> dict[str, Any]:
    server, context = access
    return await lifecycle.restart(server, context=context, ip_address=ip)


@router.post("/{server_id}/kill", summary="Force stop", dependencies=[CsrfProtected])
async def kill_server(
    access: ServerAccess, lifecycle: LifecycleDep, ip: ClientIp
) -> dict[str, Any]:
    """Terminaison immédiate du processus : le monde n'est pas sauvegardé."""
    server, context = access
    return await lifecycle.kill(server, context=context, ip_address=ip)


# --------------------------------------------------------------------------- #
#  Membres — partage du serveur
# --------------------------------------------------------------------------- #
def _members(session: DbSession) -> MemberService:
    return MemberService(session)


MembersDep = Annotated[MemberService, Depends(_members)]


def _member_out(member: Any) -> MemberOut:
    return MemberOut(
        user_id=member.user_id,
        username=member.user.username,
        role=member.role,
        added_at=member.created_at,
    )


@router.get("/{server_id}/members", response_model=list[MemberOut], summary="Members")
async def list_members(access: ServerAccess, members: MembersDep) -> list[MemberOut]:
    """Comptes avec qui le serveur est partagé. Réservé au propriétaire."""
    server, context = access
    return [_member_out(member) for member in await members.list(server, context=context)]


@router.put(
    "/{server_id}/members",
    response_model=MemberOut,
    summary="Share the server",
    dependencies=[CsrfProtected],
)
async def share_server(
    payload: MemberRequest,
    access: ServerAccess,
    members: MembersDep,
    session: DbSession,
    user: CurrentUser,
    ip: ClientIp,
) -> MemberOut:
    """Partage le serveur avec un compte, ou change son rôle s'il y est déjà."""
    server, context = access
    member = await members.share(
        server,
        username=payload.username,
        role=payload.role,
        context=context,
        actor=user,
        ip_address=ip,
    )
    await session.refresh(member, ["user", "created_at"])
    return _member_out(member)


@router.delete(
    "/{server_id}/members/{user_id}",
    summary="Stop sharing the server with an account",
    dependencies=[CsrfProtected],
)
async def remove_member(
    user_id: int,
    access: ServerAccess,
    members: MembersDep,
    user: CurrentUser,
    ip: ClientIp,
) -> dict[str, str]:
    server, context = access
    await members.remove(server, user_id=user_id, context=context, actor=user, ip_address=ip)
    return {"status": "removed"}


@router.get("/{server_id}/capabilities", summary="Available features")
async def server_capabilities(access: ServerAccess, service: ServerServiceDep) -> list[str]:
    """Onglets à afficher, déduits du contenu réel du dossier du serveur."""
    server, _ = access
    return await service.capabilities(server)
