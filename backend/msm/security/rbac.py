"""Évaluation des droits d'un utilisateur, éventuellement sur un serveur donné.

Toute vérification porte sur le couple ``(permission, serveur)`` : un modérateur
peut administrer un serveur et n'avoir aucun accès à un autre. Une permission
« globale » n'est qu'un cas particulier où le serveur vaut ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass

from msm.core.permissions import Permission, Role, effective_permissions
from msm.db.models.server import ServerPermission
from msm.db.models.user import User
from msm.exceptions import PermissionDenied
from msm.logging_conf import get_logger

logger = get_logger(__name__)


def _parse(values: list[str] | None) -> frozenset[Permission]:
    """Convertit les valeurs stockées en base, en ignorant les inconnues.

    Une permission retirée du code après une mise à jour ne doit pas empêcher un
    utilisateur de se connecter.
    """
    if not values:
        return frozenset()
    parsed: set[Permission] = set()
    for value in values:
        try:
            parsed.add(Permission(value))
        except ValueError:
            logger.warning("unknown_permission_ignored", value=value)
    return frozenset(parsed)


@dataclass(frozen=True, slots=True)
class AccessContext:
    """Droits effectifs d'un utilisateur sur un périmètre donné."""

    user_id: int
    username: str
    role: Role
    permissions: frozenset[Permission]
    server_id: int | None = None

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions

    def require(self, permission: Permission, *, action: str | None = None) -> None:
        """Exige une permission, ou lève :class:`PermissionDenied`."""
        if self.has(permission):
            return
        scope = f" sur ce serveur (#{self.server_id})" if self.server_id is not None else ""
        raise PermissionDenied(
            f"Action non autorisée{scope}.",
            cause=(
                f"Le rôle {self.role.value} ne dispose pas de la permission "
                f"« {permission.value} »" + (f" nécessaire pour {action}." if action else ".")
            ),
            remediation="Demander à un administrateur de vous accorder ce droit.",
            context={"permission": permission.value, "server_id": self.server_id},
        )


def build_context(
    user: User,
    *,
    server_id: int | None = None,
    override: ServerPermission | None = None,
) -> AccessContext:
    """Calcule les droits effectifs, surcharge par serveur comprise."""
    permissions = effective_permissions(
        user.role,
        granted=_parse(override.granted if override else None),
        revoked=_parse(override.revoked if override else None),
    )
    return AccessContext(
        user_id=user.id,
        username=user.username,
        role=user.role,
        permissions=permissions,
        server_id=server_id,
    )
