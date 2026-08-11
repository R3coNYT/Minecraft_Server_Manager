"""Joueurs : consultation, modération et skins.

Les actions sont identifiées par **pseudo** et non par UUID, contrairement à ce
que suggérait le plan initial. Raison : les commandes Minecraft prennent un
pseudo, et un serveur en mode hors ligne n'expose pas d'UUID stable. Passer par
l'UUID obligerait à une résolution supplémentaire, faillible, pour reconstruire
une information dont la commande n'a pas besoin.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response

from msm.api.deps import (
    AppSettings,
    ClientIp,
    CsrfProtected,
    DbSession,
    ServerAccess,
    SupervisorDep,
)
from msm.api.schemas import (
    BanRequest,
    GiveRequest,
    KickRequest,
    PlayerActionOut,
    PlayerOut,
    TeleportRequest,
)
from msm.core.permissions import Permission
from msm.services.player_service import PlayerService
from msm.services.skin_service import SkinService

router = APIRouter(prefix="/servers/{server_id}", tags=["joueurs"])

#: Un pseudo Minecraft : 1 à 16 caractères alphanumériques ou `_`.
UsernameParam = Annotated[str, Path(pattern=r"^[A-Za-z0-9_]{1,16}$", max_length=16)]


def _players(session: DbSession, supervisor: SupervisorDep) -> PlayerService:
    return PlayerService(session, supervisor)


PlayersDep = Annotated[PlayerService, Depends(_players)]


@router.get("/players", response_model=list[PlayerOut], summary="Lister les joueurs")
async def list_players(
    access: ServerAccess,
    service: PlayersDep,
    include_offline: bool = True,
) -> list[PlayerOut]:
    """Joueurs connectés, puis historique des joueurs connus.

    Fusionne trois sources : le runtime pour les présences, les fichiers du
    serveur pour les statuts, la base pour l'historique.
    """
    server, context = access
    context.require(Permission.PLAYER_VIEW, action="consulter les joueurs")
    players = await service.list_players(server, include_offline=include_offline)
    return [PlayerOut(**player.to_dict()) for player in players]


@router.get(
    "/players/{username}/skin.png",
    summary="Skin d'un joueur",
    response_class=Response,
)
async def player_skin(
    username: UsernameParam,
    access: ServerAccess,
    service: PlayersDep,
    session: DbSession,
    settings: AppSettings,
) -> Response:
    """Relaie l'image du skin, mise en cache par MSM.

    L'image transite par le panneau plutôt que d'être chargée directement par le
    navigateur : l'UUID des joueurs n'est ainsi jamais communiqué à un tiers
    depuis le poste de l'administrateur, et l'interface reste utilisable sur un
    réseau isolé une fois le cache constitué.
    """
    server, context = access
    context.require(Permission.PLAYER_VIEW, action="consulter les joueurs")

    players = await service.list_players(server)
    uuid = next(
        (p.uuid for p in players if p.username.casefold() == username.casefold() and p.uuid),
        None,
    )
    if uuid is None:
        return Response(status_code=404)

    image = await SkinService(session, settings).get_skin(uuid)
    if image is None:
        return Response(status_code=404)

    return Response(
        content=image,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


# --------------------------------------------------------------------------- #
#  Modération
# --------------------------------------------------------------------------- #
@router.post(
    "/players/{username}/op",
    response_model=PlayerActionOut,
    summary="Promouvoir opérateur",
    dependencies=[CsrfProtected],
)
async def op_player(
    username: UsernameParam, access: ServerAccess, service: PlayersDep, ip: ClientIp
) -> PlayerActionOut:
    server, context = access
    return PlayerActionOut(**await service.op(server, username, context=context, ip_address=ip))


@router.post(
    "/players/{username}/deop",
    response_model=PlayerActionOut,
    summary="Retirer les droits d'opérateur",
    dependencies=[CsrfProtected],
)
async def deop_player(
    username: UsernameParam, access: ServerAccess, service: PlayersDep, ip: ClientIp
) -> PlayerActionOut:
    server, context = access
    return PlayerActionOut(**await service.deop(server, username, context=context, ip_address=ip))


@router.post(
    "/players/{username}/kick",
    response_model=PlayerActionOut,
    summary="Expulser",
    dependencies=[CsrfProtected],
)
async def kick_player(
    username: UsernameParam,
    payload: KickRequest,
    access: ServerAccess,
    service: PlayersDep,
    ip: ClientIp,
) -> PlayerActionOut:
    server, context = access
    return PlayerActionOut(
        **await service.kick(server, username, payload.reason, context=context, ip_address=ip)
    )


@router.post(
    "/players/{username}/ban",
    response_model=PlayerActionOut,
    summary="Bannir",
    dependencies=[CsrfProtected],
)
async def ban_player(
    username: UsernameParam,
    payload: BanRequest,
    access: ServerAccess,
    service: PlayersDep,
    ip: ClientIp,
) -> PlayerActionOut:
    server, context = access
    return PlayerActionOut(
        **await service.ban(server, username, payload.reason, context=context, ip_address=ip)
    )


@router.post(
    "/players/{username}/unban",
    response_model=PlayerActionOut,
    summary="Lever un bannissement",
    dependencies=[CsrfProtected],
)
async def unban_player(
    username: UsernameParam, access: ServerAccess, service: PlayersDep, ip: ClientIp
) -> PlayerActionOut:
    server, context = access
    return PlayerActionOut(**await service.unban(server, username, context=context, ip_address=ip))


@router.post(
    "/players/{username}/kill",
    response_model=PlayerActionOut,
    summary="Tuer",
    dependencies=[CsrfProtected],
)
async def kill_player(
    username: UsernameParam, access: ServerAccess, service: PlayersDep, ip: ClientIp
) -> PlayerActionOut:
    server, context = access
    return PlayerActionOut(**await service.kill(server, username, context=context, ip_address=ip))


@router.post(
    "/players/{username}/give",
    response_model=PlayerActionOut,
    summary="Donner un objet",
    dependencies=[CsrfProtected],
)
async def give_player(
    username: UsernameParam,
    payload: GiveRequest,
    access: ServerAccess,
    service: PlayersDep,
    ip: ClientIp,
) -> PlayerActionOut:
    server, context = access
    return PlayerActionOut(
        **await service.give(
            server, username, payload.item, payload.count, context=context, ip_address=ip
        )
    )


@router.post(
    "/players/{username}/teleport",
    response_model=PlayerActionOut,
    summary="Téléporter",
    dependencies=[CsrfProtected],
)
async def teleport_player(
    username: UsernameParam,
    payload: TeleportRequest,
    access: ServerAccess,
    service: PlayersDep,
    ip: ClientIp,
) -> PlayerActionOut:
    server, context = access
    return PlayerActionOut(
        **await service.teleport(
            server, username, payload.destination, context=context, ip_address=ip
        )
    )
