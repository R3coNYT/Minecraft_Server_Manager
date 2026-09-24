"""Permissions atomiques, rôles globaux et rôles sur un serveur.

Deux rôles se combinent :

1. le **rôle global** de l'utilisateur (admin, modérateur, user) donne ses droits
   sur MSM lui-même — audit, comptes, réglages — et un droit de regard limité sur
   les serveurs des autres ;
2. son **rôle sur un serveur** (propriétaire, admin du serveur, membre) donne ses
   droits sur ce serveur-là.

Toute vérification porte sur le couple ``(permission, serveur)`` — jamais sur la
permission seule. Un user peut ainsi administrer son serveur et ne rien voir de
celui d'un autre ; un admin de MSM, lui, voit tout mais ne modifie pas les
serveurs des autres.
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
    #: Enregistrer un dossier existant de la machine (et l'analyser).
    SERVER_REGISTER = "server:register"
    #: Commande de lancement libre, chemin de Java, script, arguments JVM, environnement.
    SERVER_LAUNCH = "server:launch"
    #: Démarrer le serveur en même temps que MSM.
    SERVER_AUTOSTART = "server:autostart"
    #: Partager le serveur : ajouter, modifier et retirer des membres.
    SERVER_MEMBERS = "server:members"

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

    # --- Sauvegardes ---
    BACKUP_CREATE = "backup:create"
    BACKUP_RESTORE = "backup:restore"

    # --- Administration de MSM ---
    AUDIT_VIEW = "audit:view"
    #: Consulter les comptes (liste, fiches).
    USER_VIEW = "user:view"
    #: Bannir et débannir des comptes de rôle user.
    USER_BAN = "user:ban"
    #: Créer, modifier et supprimer des comptes, changer les rôles.
    USER_MANAGE = "user:manage"
    SETTINGS_MANAGE = "settings:manage"
    #: Ressources de la machine (processeur, mémoire, disque).
    SYSTEM_VIEW = "system:view"


class Role(str, Enum):
    """Rôle global d'un utilisateur, sur tout MSM."""

    ADMIN = "ADMIN"
    MODERATOR = "MODERATOR"
    USER = "USER"


class ServerRole(str, Enum):
    """Rôle d'un utilisateur sur un serveur donné."""

    #: Celui qui a créé le serveur. Un seul par serveur, porté par `servers.owner_id`.
    OWNER = "OWNER"
    #: Administre le serveur, sans pouvoir le supprimer ni le partager.
    ADMIN = "ADMIN"
    #: Consulte la vue d'ensemble, sans agir.
    VIEWER = "VIEWER"


#: Tout ce qu'il faut pour faire vivre un serveur au quotidien.
OPERATE_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.SERVER_VIEW,
        Permission.SERVER_EDIT,
        Permission.SERVER_START,
        Permission.SERVER_STOP,
        Permission.SERVER_RESTART,
        Permission.SERVER_KILL,
        Permission.CONSOLE_READ,
        Permission.CONSOLE_WRITE,
        Permission.CONSOLE_DANGEROUS,
        Permission.PLAYER_VIEW,
        Permission.PLAYER_KICK,
        Permission.PLAYER_BAN,
        Permission.PLAYER_OP,
        Permission.PLAYER_KILL,
        Permission.PLAYER_GIVE,
        Permission.PLAYER_TELEPORT,
        Permission.FILE_READ,
        Permission.FILE_UPLOAD,
        Permission.FILE_DELETE,
        Permission.FILE_TOGGLE,
        Permission.CONFIG_READ,
        Permission.CONFIG_WRITE,
        Permission.PROPERTIES_WRITE,
        Permission.EVENT_RUN,
        Permission.EVENT_RUN_DESTRUCTIVE,
        Permission.EVENT_EDIT,
        Permission.BACKUP_CREATE,
        Permission.BACKUP_RESTORE,
    }
)

#: Droits apportés par le rôle sur un serveur.
SERVER_ROLE_PERMISSIONS: dict[ServerRole, frozenset[Permission]] = {
    ServerRole.OWNER: OPERATE_PERMISSIONS | {Permission.SERVER_DELETE, Permission.SERVER_MEMBERS},
    ServerRole.ADMIN: OPERATE_PERMISSIONS,
    ServerRole.VIEWER: frozenset({Permission.SERVER_VIEW}),
}

#: Droit de regard du rôle global sur **n'importe quel** serveur, membre ou non.
#: Un admin de MSM voit tout, arrête, supprime et règle le démarrage avec MSM,
#: mais ne modifie pas le serveur d'un autre ; un modérateur voit et arrête.
OVERSIGHT_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(
        {
            Permission.SERVER_VIEW,
            Permission.SERVER_STOP,
            Permission.SERVER_KILL,
            Permission.SERVER_DELETE,
            Permission.SERVER_AUTOSTART,
            Permission.SERVER_LAUNCH,
            Permission.SERVER_REGISTER,
        }
    ),
    Role.MODERATOR: frozenset(
        {Permission.SERVER_VIEW, Permission.SERVER_STOP, Permission.SERVER_KILL}
    ),
    Role.USER: frozenset(),
}

#: Droits sur MSM lui-même, hors de tout serveur.
#: L'administrateur détient toutes les permissions, y compris celles ajoutées
#: ultérieurement — c'est volontairement dérivé, pas énuméré.
GLOBAL_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.MODERATOR: frozenset(
        {
            Permission.SERVER_CREATE,
            Permission.AUDIT_VIEW,
            Permission.USER_VIEW,
            Permission.USER_BAN,
        }
    ),
    Role.USER: frozenset({Permission.SERVER_CREATE}),
}


def global_permissions(role: Role) -> frozenset[Permission]:
    """Droits sur MSM lui-même : audit, comptes, réglages, création de serveurs."""
    return GLOBAL_PERMISSIONS[role]


def server_permissions(role: Role, server_role: ServerRole | None) -> frozenset[Permission]:
    """Droits effectifs sur un serveur : regard du rôle global + rôle sur le serveur.

    Les deux s'additionnent. Un admin de MSM qui est aussi admin d'un serveur a
    donc tous les droits d'un admin du serveur, plus son droit de regard.
    """
    permissions = OVERSIGHT_PERMISSIONS[role]
    if server_role is not None:
        permissions = permissions | SERVER_ROLE_PERMISSIONS[server_role]
    return permissions


def sees_every_server(role: Role) -> bool:
    """Les admins et les modérateurs voient tous les serveurs, membres ou non."""
    return Permission.SERVER_VIEW in OVERSIGHT_PERMISSIONS[role]
