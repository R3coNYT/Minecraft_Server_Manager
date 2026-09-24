"""Schémas d'authentification et de comptes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from msm.core.permissions import Permission, Role, ServerRole


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class UserOut(BaseModel):
    """Représentation publique d'un compte — jamais l'empreinte du mot de passe."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str | None = None
    email: str | None = None
    role: Role
    is_active: bool
    #: Nom du dossier du compte sur le disque ; ne change jamais.
    storage_id: str
    last_login_at: datetime | None = None
    created_at: datetime
    #: Langue choisie ; ``None`` : celle du panneau.
    language: str | None = None
    banned_at: datetime | None = None
    ban_reason: str | None = None
    avatar_updated_at: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def avatar_url(self) -> str | None:
        """Adresse de l'avatar ; la date en paramètre force le rafraîchissement du cache."""
        if self.avatar_updated_at is None:
            return None
        return f"/api/v1/users/{self.id}/avatar?v={int(self.avatar_updated_at.timestamp())}"


class MeOut(UserOut):
    """Compte courant, enrichi de ses permissions effectives."""

    permissions: list[Permission] = Field(default_factory=list)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)
    role: Role = Role.USER
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=255)


class UserUpdateRequest(BaseModel):
    role: Role | None = None
    is_active: bool | None = None
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=255)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class CsrfOut(BaseModel):
    csrf_token: str


# --------------------------------------------------------------------------- #
#  Inscription et profil
# --------------------------------------------------------------------------- #
class RegistrationInfoOut(BaseModel):
    """Public : l'écran de connexion propose l'inscription si elle est possible."""

    mode: str


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)
    #: Acceptation des règles de l'instance : obligatoire.
    accept_terms: bool = False
    #: Jeton du lien d'invitation, quand l'inscription est « sur invitation ».
    invitation: str | None = Field(default=None, max_length=256)


class ProfileUpdateRequest(BaseModel):
    """Champs absents : inchangés. `language` à ``null`` : revenir à celle du panneau."""

    username: str | None = Field(default=None, min_length=1, max_length=64)
    language: str | None = Field(default=None, max_length=8)


class EmailChangeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    current_password: str = Field(min_length=1, max_length=1024)


class RegistrationSettings(BaseModel):
    mode: str


# --------------------------------------------------------------------------- #
#  Équipe : fiche d'un compte, bannissement, invitations
# --------------------------------------------------------------------------- #
class UsernameChangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    username: str
    changed_at: datetime


class ServerRefOut(BaseModel):
    id: int
    name: str
    #: Rôle du compte sur ce serveur, pour les serveurs partagés avec lui.
    role: ServerRole | None = None


class UserDetailOut(UserOut):
    username_history: list[UsernameChangeOut] = Field(default_factory=list)
    servers_owned: list[ServerRefOut] = Field(default_factory=list)
    servers_shared: list[ServerRefOut] = Field(default_factory=list)
    #: Limites propres à ce compte, par-dessus celles par défaut ; ``None`` : aucune.
    quota_overrides: dict[str, int | None] | None = None
    #: Limites effectives et consommation (voir `/auth/me/quota`).
    limits: Any = None


class AccountBanRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class InvitationCreateRequest(BaseModel):
    note: str | None = Field(default=None, max_length=128)
    days: int = Field(default=7, ge=1, le=30)


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    note: str | None = None
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None


class InvitationCreatedOut(InvitationOut):
    #: Montré une seule fois : la base n'en garde que l'empreinte.
    token: str
