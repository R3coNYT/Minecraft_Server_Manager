"""Schémas des joueurs et des actions de modération."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlayerOut(BaseModel):
    """Un joueur, connecté ou connu de ce serveur."""

    username: str
    uuid: str | None = None
    online: bool
    is_op: bool
    is_banned: bool
    is_whitelisted: bool
    op_level: int | None = None
    ban_reason: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    total_sessions: int = 0
    #: Toujours nul : Minecraft n'expose pas le ping par joueur. Le champ existe
    #: pour qu'un fournisseur RCON ou un plugin puisse le renseigner plus tard.
    ping_ms: int | None = None


class PlayerActionOut(BaseModel):
    """Résultat d'une action de modération."""

    username: str
    #: Commande réellement envoyée à la console, telle qu'elle sera auditée.
    command: str


class KickRequest(BaseModel):
    reason: str = Field(default="", max_length=200)


class BanRequest(BaseModel):
    reason: str = Field(default="", max_length=200)


class GiveRequest(BaseModel):
    item: str = Field(min_length=1, max_length=128)
    count: int = Field(default=1, ge=1, le=6400)


class TeleportRequest(BaseModel):
    #: Pseudo ou sélecteur (`@p`, `@s`…) désignant la destination.
    destination: str = Field(min_length=1, max_length=64)
