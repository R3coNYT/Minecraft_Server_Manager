"""Consultation des joueurs et actions de modération.

Toutes les actions passent par la **console du serveur**, jamais par une
modification directe des fichiers JSON : c'est la seule façon que le serveur en
tienne compte immédiatement — bannir quelqu'un en éditant `banned-players.json`
ne le déconnecte pas.

Conséquence assumée : ces actions exigent un serveur démarré. C'est cohérent avec
la réalité — expulser un joueur d'un serveur éteint n'a aucun sens.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from msm.core import commands
from msm.core.permissions import Permission
from msm.db.models.audit import AuditAction
from msm.db.models.server import Server
from msm.db.repositories import AuditRepository
from msm.db.repositories.player_repo import PlayerRepository
from msm.exceptions import ServerNotRunning
from msm.logging_conf import get_logger
from msm.minecraft.players import json_files
from msm.runtime.supervisor import Supervisor
from msm.security.rbac import AccessContext

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PlayerView:
    """Un joueur tel qu'affiché dans l'interface."""

    username: str
    uuid: str | None
    online: bool
    is_op: bool
    is_banned: bool
    is_whitelisted: bool
    op_level: int | None = None
    ban_reason: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    total_sessions: int = 0
    #: Toujours ``None`` : Minecraft n'expose pas le ping par joueur (voir README).
    ping_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PlayerService:
    """Cas d'usage liés aux joueurs d'un serveur."""

    def __init__(self, session: AsyncSession, supervisor: Supervisor) -> None:
        self._session = session
        self._supervisor = supervisor
        self._players = PlayerRepository(session)
        self._audit = AuditRepository(session)

    # ------------------------------------------------------------------ #
    #  Consultation
    # ------------------------------------------------------------------ #
    async def list_players(
        self, server: Server, *, include_offline: bool = True
    ) -> list[PlayerView]:
        """Liste les joueurs : connectés d'abord, puis les autres connus.

        Trois sources sont fusionnées — le runtime pour qui est en ligne, les
        fichiers du serveur pour les statuts, la base pour l'historique.
        """
        files = json_files.read_all(Path(server.directory))
        runtime = self._supervisor.find(server.id)
        online = dict(_online_map(runtime))

        known = {
            player.username.casefold(): player
            for player in await self._players.list_for_server(server.id)
        }

        views: list[PlayerView] = []
        seen: set[str] = set()

        for username, uuid in online.items():
            key = username.casefold()
            seen.add(key)
            record = known.get(key)
            views.append(
                self._build_view(
                    username,
                    uuid or files.uuid_of(username) or (record.uuid if record else None),
                    online=True,
                    files=files,
                    record=record,
                )
            )

        if include_offline:
            for key, record in known.items():
                if key in seen:
                    continue
                seen.add(key)
                views.append(
                    self._build_view(
                        record.username,
                        record.uuid or files.uuid_of(record.username),
                        online=False,
                        files=files,
                        record=record,
                    )
                )

            # Les joueurs cités dans les fichiers du serveur sans être jamais
            # passés par MSM doivent apparaître aussi : sans cela, il serait
            # impossible de débannir quelqu'un banni avant l'installation du
            # panneau, ou avec la commande tapée en console.
            for key, record in _referenced_players(files).items():
                if key in seen:
                    continue
                seen.add(key)
                views.append(
                    self._build_view(
                        record.username,
                        record.uuid or files.uuid_of(record.username),
                        online=False,
                        files=files,
                        record=None,
                    )
                )

        views.sort(key=lambda view: (not view.online, view.username.casefold()))
        return views

    def _build_view(
        self,
        username: str,
        uuid: str | None,
        *,
        online: bool,
        files: json_files.PlayerFilesSnapshot,
        record: Any | None,
    ) -> PlayerView:
        key = username.casefold()
        op_record = files.ops.get(key)
        ban_record = files.banned.get(key)
        return PlayerView(
            username=username,
            uuid=uuid,
            online=online,
            is_op=op_record is not None,
            is_banned=ban_record is not None,
            is_whitelisted=files.is_whitelisted(username),
            op_level=op_record.level if op_record else None,
            ban_reason=ban_record.reason if ban_record else None,
            first_seen=record.first_seen.isoformat() if record and record.first_seen else None,
            last_seen=record.last_seen.isoformat() if record and record.last_seen else None,
            total_sessions=record.total_sessions if record else 0,
        )

    async def sync_statuses(self, server: Server) -> None:
        """Reporte en base les statuts lus dans les fichiers du serveur."""
        files = json_files.read_all(Path(server.directory))
        await self._players.sync_statuses(
            server.id,
            ops=set(files.ops),
            banned=set(files.banned),
            whitelisted=set(files.whitelisted),
        )

    # ------------------------------------------------------------------ #
    #  Actions
    # ------------------------------------------------------------------ #
    async def op(self, server: Server, username: str, **kwargs: Any) -> dict[str, Any]:
        return await self._act(
            server,
            commands.build_op(username),
            permission=Permission.PLAYER_OP,
            action=AuditAction.PLAYER_OP,
            summary=f"{username} promu opérateur",
            username=username,
            **kwargs,
        )

    async def deop(self, server: Server, username: str, **kwargs: Any) -> dict[str, Any]:
        return await self._act(
            server,
            commands.build_deop(username),
            permission=Permission.PLAYER_OP,
            action=AuditAction.PLAYER_DEOP,
            summary=f"{username} n'est plus opérateur",
            username=username,
            **kwargs,
        )

    async def kick(
        self, server: Server, username: str, reason: str = "", **kwargs: Any
    ) -> dict[str, Any]:
        return await self._act(
            server,
            commands.build_kick(username, reason),
            permission=Permission.PLAYER_KICK,
            action=AuditAction.PLAYER_KICKED,
            summary=f"{username} expulsé" + (f" ({reason})" if reason else ""),
            username=username,
            **kwargs,
        )

    async def ban(
        self, server: Server, username: str, reason: str = "", **kwargs: Any
    ) -> dict[str, Any]:
        return await self._act(
            server,
            commands.build_ban(username, reason),
            permission=Permission.PLAYER_BAN,
            action=AuditAction.PLAYER_BANNED,
            summary=f"{username} banni" + (f" ({reason})" if reason else ""),
            username=username,
            **kwargs,
        )

    async def unban(self, server: Server, username: str, **kwargs: Any) -> dict[str, Any]:
        return await self._act(
            server,
            commands.build_pardon(username),
            permission=Permission.PLAYER_BAN,
            action=AuditAction.PLAYER_UNBANNED,
            summary=f"{username} débanni",
            username=username,
            **kwargs,
        )

    async def kill(self, server: Server, username: str, **kwargs: Any) -> dict[str, Any]:
        return await self._act(
            server,
            commands.build_kill(username),
            permission=Permission.PLAYER_KILL,
            action=AuditAction.PLAYER_KILLED,
            summary=f"{username} tué",
            username=username,
            **kwargs,
        )

    async def give(
        self, server: Server, username: str, item: str, count: int = 1, **kwargs: Any
    ) -> dict[str, Any]:
        return await self._act(
            server,
            commands.build_give(username, item, count),
            permission=Permission.PLAYER_GIVE,
            action=AuditAction.PLAYER_GIVE,
            summary=f"{count} x {item} donné(s) à {username}",
            username=username,
            **kwargs,
        )

    async def teleport(
        self, server: Server, username: str, destination: str, **kwargs: Any
    ) -> dict[str, Any]:
        return await self._act(
            server,
            commands.build_teleport(username, destination),
            permission=Permission.PLAYER_TELEPORT,
            action=AuditAction.PLAYER_TELEPORTED,
            summary=f"{username} téléporté vers {destination}",
            username=username,
            **kwargs,
        )

    # ------------------------------------------------------------------ #
    async def _act(
        self,
        server: Server,
        command: str,
        *,
        permission: Permission,
        action: AuditAction,
        summary: str,
        username: str,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Contrôle les droits, envoie la commande, journalise."""
        context.require(permission, action=summary.lower())

        runtime = self._supervisor.find(server.id)
        if runtime is None or not runtime.state.is_running:
            raise ServerNotRunning(
                "Action impossible : le serveur n'est pas démarré.",
                cause="Les actions sur les joueurs passent par la console du serveur.",
                remediation="Démarrer le serveur avant d'agir sur un joueur.",
            )

        sent = await runtime.send_command(command, actor=context.username)

        self._audit.record(
            action=action,
            summary=f"{summary} sur « {server.name} ».",
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            server_id=server.id,
            target_type="player",
            target_id=username,
            payload={"command": sent},
        )
        logger.info(
            "player_action",
            server_id=server.id,
            actor=context.username,
            target=username,
            command=sent,
        )
        return {"command": sent, "username": username}


def _referenced_players(
    files: json_files.PlayerFilesSnapshot,
) -> dict[str, json_files.PlayerRecord]:
    """Joueurs cités dans ops.json, banned-players.json ou whitelist.json."""
    referenced: dict[str, json_files.PlayerRecord] = {}
    for source in (files.banned, files.ops, files.whitelisted):
        for key, record in source.items():
            referenced.setdefault(key, record)
    return referenced


def _online_map(runtime: Any) -> dict[str, str | None]:
    """Joueurs actuellement connectés, tels que vus par le runtime."""
    if runtime is None or not runtime.state.is_running:
        return {}
    return dict(runtime.online_player_map)
