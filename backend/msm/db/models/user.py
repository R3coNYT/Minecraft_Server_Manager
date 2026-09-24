"""Utilisateurs et sessions du panel."""

from __future__ import annotations

import secrets
import string
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from msm.core.permissions import Role
from msm.db.base import Base, TimestampMixin
from msm.db.types import UtcDateTime

if TYPE_CHECKING:
    from msm.db.models.server import ServerMember


#: Alphabet des identifiants de stockage : sans ambiguïté dans un chemin.
_STORAGE_ALPHABET = string.ascii_lowercase + string.digits
STORAGE_ID_LENGTH = 10


def new_storage_id() -> str:
    """Identifiant court, aléatoire et immuable d'un compte (nom de son dossier)."""
    return "".join(secrets.choice(_STORAGE_ALPHABET) for _ in range(STORAGE_ID_LENGTH))


class User(Base, TimestampMixin):
    """Compte d'accès au panel.

    Le mot de passe n'est jamais stocké : seule son empreinte argon2id l'est.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    #: Nom du dossier du compte sur le disque. Contrairement au pseudo, il ne
    #: change jamais : renommer son compte ne déplace aucun fichier.
    storage_id: Mapped[str] = mapped_column(
        String(16), unique=True, nullable=False, default=new_storage_id
    )
    display_name: Mapped[str | None] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(255))

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(
        Enum(Role, native_enum=False, length=16), nullable=False, default=Role.USER
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: Compteur anti-force-brute, remis à zéro à chaque connexion réussie.
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UtcDateTime)

    #: Réservé à la double authentification (phase ultérieure).
    totp_secret: Mapped[str | None] = mapped_column(String(64))

    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    memberships: Mapped[list[ServerMember]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserSession(Base):
    """Session authentifiée, adossée à un cookie HttpOnly.

    Seule l'empreinte du jeton est conservée : une fuite de la base ne permet pas
    d'usurper une session en cours. La table permet aussi la révocation côté
    serveur — impossible avec un jeton autoporteur.
    """

    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="sessions")
