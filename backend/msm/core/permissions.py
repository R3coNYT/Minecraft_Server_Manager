"""Permissions atomiques et rôles.

Deux mécanismes se combinent :

1. le **rôle** de l'utilisateur donne un jeu de permissions par défaut ;
2. une **surcharge par serveur** (table ``server_permissions``) peut restreindre ou
   étendre ces droits sur un serveur précis.

Toute vérification porte sur le couple ``(permission, serveur)`` — jamais sur la
permission seule. Un modérateur peut ainsi administrer un serveur et n'avoir aucun
accès à un autre.
"""

from __future__ import annotations

from enum import Enum


class Permission(str, Enum):
    """Droit élémentaire vérifiable."""

    # --- Serveurs : consultation et configuration ---
    SERVER_VIEW = "server:view"
    SERVER_CREATE = "server:create"
    SERVER_EDIT = "server:edit"
    SERVER_DELETE = "server:delete"

    # --- Serveurs : cycle de vie ---
    SERVER_START = "server:start"
    SERVER_STOP = "server:stop"
    SERVER_RESTART = "server:restart"
    SERVER_KILL = "server:kill"

    # --- Console ---
    CONSOLE_READ = "console:read"
    CONSOLE_WRITE = "console:write"
    CONSOLE_DANGEROUS = "console:dangerous"

    # --- Joueurs ---
    PLAYER_VIEW = "player:view"
    PLAYER_KICK = "player:kick"
    PLAYER_BAN = "player:ban"
    PLAYER_OP = "player:op"
    PLAYER_KILL = "player:kill"
    PLAYER_GIVE = "player:give"
    PLAYER_TELEPORT = "player:teleport"

    # --- Fichiers (mods, plugins, datapacks) ---
    FILE_READ = "file:read"
    FILE_UPLOAD = "file:upload"
    FILE_DELETE = "file:delete"
    FILE_TOGGLE = "file:toggle"

    # --- Configurations ---
    CONFIG_READ = "config:read"
    CONFIG_WRITE = "config:write"
    PROPERTIES_WRITE = "properties:write"

    # --- Événements ---
    EVENT_RUN = "event:run"
    EVENT_RUN_DESTRUCTIVE = "event:run_destructive"
    EVENT_EDIT = "event:edit"

    # --- Administration ---
    AUDIT_VIEW = "audit:view"
    USER_MANAGE = "user:manage"
    SETTINGS_MANAGE = "settings:manage"

    # --- Sauvegardes (phase 5, déclarées dès maintenant) ---
    BACKUP_CREATE = "backup:create"
    BACKUP_RESTORE = "backup:restore"


class Role(str, Enum):
    """Rôle global d'un utilisateur."""

    ADMIN = "ADMIN"
    MODERATOR = "MODERATOR"
    VIEWER = "VIEWER"


VIEWER_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.SERVER_VIEW,
        Permission.CONSOLE_READ,
        Permission.PLAYER_VIEW,
        Permission.FILE_READ,
        Permission.CONFIG_READ,
    }
)

MODERATOR_PERMISSIONS: frozenset[Permission] = VIEWER_PERMISSIONS | {
    Permission.SERVER_START,
    Permission.SERVER_STOP,
    Permission.SERVER_RESTART,
    Permission.CONSOLE_WRITE,
    Permission.PLAYER_KICK,
    Permission.PLAYER_BAN,
    Permission.PLAYER_KILL,
    Permission.PLAYER_GIVE,
    Permission.PLAYER_TELEPORT,
    Permission.EVENT_RUN,
}

#: L'administrateur détient toutes les permissions, y compris celles ajoutées
#: ultérieurement — c'est volontairement dérivé, pas énuméré.
ADMIN_PERMISSIONS: frozenset[Permission] = frozenset(Permission)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: ADMIN_PERMISSIONS,
    Role.MODERATOR: MODERATOR_PERMISSIONS,
    Role.VIEWER: VIEWER_PERMISSIONS,
}


def permissions_for(role: Role) -> frozenset[Permission]:
    """Permissions par défaut associées à un rôle."""
    return ROLE_PERMISSIONS[role]


def effective_permissions(
    role: Role,
    *,
    granted: frozenset[Permission] | None = None,
    revoked: frozenset[Permission] | None = None,
) -> frozenset[Permission]:
    """Permissions effectives sur un serveur donné.

    Les révocations sont appliquées **après** les octrois : une permission à la
    fois accordée et révoquée est refusée. En sécurité, le refus l'emporte.
    """
    permissions = permissions_for(role)
    if granted:
        permissions = permissions | granted
    if revoked:
        permissions = permissions - revoked
    return permissions


def has_permission(
    role: Role,
    permission: Permission,
    *,
    granted: frozenset[Permission] | None = None,
    revoked: frozenset[Permission] | None = None,
) -> bool:
    """Teste une permission en tenant compte des surcharges par serveur."""
    return permission in effective_permissions(role, granted=granted, revoked=revoked)
