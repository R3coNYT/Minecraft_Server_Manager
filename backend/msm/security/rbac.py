"""Évaluation des droits d'un utilisateur, éventuellement sur un serveur donné.

Toute vérification porte sur le couple ``(permission, serveur)`` : un user peut
administrer son serveur et n'avoir aucun accès à celui d'un autre. Une permission
« globale » n'est qu'un cas particulier où le serveur vaut ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass

from msm.core.permissions import (
    Permission,
    Role,
    ServerRole,
    global_permissions,
    server_permissions,
)
from msm.db.models.server import Server
from msm.db.models.user import User
from msm.exceptions import PermissionDenied
from msm.i18n import tr


@dataclass(frozen=True, slots=True)
class AccessContext:
    """Droits effectifs d'un utilisateur sur un périmètre donné."""

    user_id: int
    username: str
    role: Role
    permissions: frozenset[Permission]
    server_id: int | None = None
    #: Rôle sur le serveur du périmètre ; `None` hors serveur, ou sans rôle dessus.
    server_role: ServerRole | None = None

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions

    def require(self, permission: Permission, *, action: str | None = None) -> None:
        """Exige une permission, ou lève :class:`PermissionDenied`."""
        if self.has(permission):
            return
        message = (
            tr("Action not allowed on this server (#{server_id}).", server_id=self.server_id)
            if self.server_id is not None
            else tr("Action not allowed.")
        )
        cause = (
            tr(
                "Your access lacks the “{permission}” permission required to {action}.",
                permission=permission.value,
                action=action,
            )
            if action
            else tr("Your access lacks the “{permission}” permission.", permission=permission.value)
        )
        remediation = (
            tr("Ask the server's owner to give you more rights on it.")
            if self.server_id is not None
            else tr("Ask an administrator to grant you this permission.")
        )
        raise PermissionDenied(
            message,
            cause=cause,
            remediation=remediation,
            context={"permission": permission.value, "server_id": self.server_id},
        )


def build_context(user: User) -> AccessContext:
    """Droits de l'utilisateur sur MSM lui-même, hors de tout serveur."""
    return AccessContext(
        user_id=user.id,
        username=user.username,
        role=user.role,
        permissions=global_permissions(user.role),
    )


def server_role_of(user: User, server: Server, member_role: ServerRole | None) -> ServerRole | None:
    """Rôle de l'utilisateur sur le serveur : propriétaire, ou celui de son adhésion."""
    if server.owner_id == user.id:
        return ServerRole.OWNER
    return member_role


def build_server_context(
    user: User, server: Server, *, member_role: ServerRole | None = None
) -> AccessContext:
    """Droits effectifs sur un serveur. `member_role` vient de `server_members`."""
    role = server_role_of(user, server, member_role)
    return AccessContext(
        user_id=user.id,
        username=user.username,
        role=user.role,
        permissions=server_permissions(user.role, role),
        server_id=server.id,
        server_role=role,
    )
