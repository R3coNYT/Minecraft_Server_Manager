"""Partage d'un serveur : les membres et leur rôle dessus.

Seul le propriétaire partage son serveur (permission `server:members`). Un membre
est soit **admin du serveur** — il le fait vivre, sans pouvoir le supprimer ni le
partager —, soit **membre** en lecture seule, limité à la vue d'ensemble.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from msm.core.permissions import Permission, ServerRole
from msm.db.models.audit import AuditAction
from msm.db.models.server import Server, ServerMember
from msm.db.models.user import User
from msm.db.repositories import AuditRepository, ServerMemberRepository, UserRepository
from msm.exceptions import NotFoundError, ValidationError
from msm.i18n import tr
from msm.logging_conf import get_logger
from msm.security.rbac import AccessContext

logger = get_logger(__name__)

#: Rôles qu'un partage peut donner. Le propriétaire est unique, jamais « ajouté ».
SHAREABLE_ROLES: frozenset[ServerRole] = frozenset({ServerRole.ADMIN, ServerRole.VIEWER})


class MemberService:
    def __init__(self, session: AsyncSession) -> None:
        self._members = ServerMemberRepository(session)
        self._users = UserRepository(session)
        self._audit = AuditRepository(session)

    async def list(self, server: Server, *, context: AccessContext) -> list[ServerMember]:
        context.require(Permission.SERVER_MEMBERS, action=tr("manage who can access this server"))
        return await self._members.list_for_server(server.id)

    async def share(
        self,
        server: Server,
        *,
        username: str,
        role: ServerRole,
        context: AccessContext,
        actor: User,
        ip_address: str | None = None,
    ) -> ServerMember:
        """Partage le serveur avec un compte, ou change son rôle s'il y est déjà."""
        context.require(Permission.SERVER_MEMBERS, action=tr("manage who can access this server"))
        if role not in SHAREABLE_ROLES:
            raise ValidationError(
                tr("Invalid role."),
                cause=tr("A server can only be shared as admin or as viewer."),
                remediation=tr("Choose “admin” or “viewer”."),
            )

        user = await self._users.get_by_username(username.strip())
        if user is None or not user.is_active or user.is_banned:
            raise NotFoundError(
                tr("Account not found."),
                cause=tr("No active account is called “{username}”.", username=username.strip()),
                remediation=tr("Check the username: it is the one shown in the top right corner."),
            )
        if user.id == server.owner_id:
            raise ValidationError(
                tr("This account owns the server."),
                cause=tr("“{username}” already has every right on it.", username=user.username),
                remediation=tr("Choose another account."),
            )

        existing = await self._members.get(user.id, server.id)
        member = await self._members.upsert(user_id=user.id, server_id=server.id, role=role)
        self._audit.record(
            action=AuditAction.MEMBER_UPDATED if existing else AuditAction.MEMBER_ADDED,
            summary=tr(
                "Server “{server}” shared with {username} ({role}).",
                server=server.name,
                username=user.username,
                role=role.value,
            ),
            actor_id=actor.id,
            actor_username=actor.username,
            actor_role=actor.role.value,
            ip_address=ip_address,
            server_id=server.id,
            payload={"user_id": user.id, "username": user.username, "role": role.value},
        )
        logger.info("server_shared", server_id=server.id, user_id=user.id, role=role.value)
        return member

    async def remove(
        self,
        server: Server,
        *,
        user_id: int,
        context: AccessContext,
        actor: User,
        ip_address: str | None = None,
    ) -> None:
        context.require(Permission.SERVER_MEMBERS, action=tr("manage who can access this server"))
        member = await self._members.get(user_id, server.id)
        if member is None:
            raise NotFoundError(
                tr("Member not found."),
                cause=tr("This account has no access to the server."),
                remediation=tr("Refresh the member list."),
            )
        account = await self._users.get(user_id)
        username = account.username if account else str(user_id)
        await self._members.delete(member)
        self._audit.record(
            action=AuditAction.MEMBER_REMOVED,
            summary=tr(
                "{username} no longer has access to server “{server}”.",
                username=username,
                server=server.name,
            ),
            actor_id=actor.id,
            actor_username=actor.username,
            actor_role=actor.role.value,
            ip_address=ip_address,
            server_id=server.id,
            payload={"user_id": user_id, "username": username},
        )
        logger.info("server_unshared", server_id=server.id, user_id=user_id)
