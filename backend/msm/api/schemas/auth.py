"""Schémas d'authentification et de comptes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from msm.core.permissions import Permission, Role


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
    last_login_at: datetime | None = None
    created_at: datetime


class MeOut(UserOut):
    """Compte courant, enrichi de ses permissions effectives."""

    permissions: list[Permission] = Field(default_factory=list)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)
    role: Role = Role.VIEWER
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
