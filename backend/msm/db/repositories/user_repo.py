"""Accès aux utilisateurs et à leurs sessions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from msm.core.permissions import Role
from msm.db.models.user import User, UserSession
from msm.security.tokens import generate_token, hash_token


class UserRepository:
    """Requêtes sur les comptes du panel."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: int) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_username(self, username: str) -> User | None:
        """Recherche insensible à la casse : « Flavien » et « flavien » sont le même compte."""
        statement = select(User).where(User.username.ilike(username.strip()))
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_all(self) -> list[User]:
        statement = select(User).order_by(User.username)
        return list((await self._session.execute(statement)).scalars())

    async def count(self) -> int:
        statement = select(User.id)
        return len(list((await self._session.execute(statement)).scalars()))

    async def create(
        self,
        *,
        username: str,
        password_hash: str,
        role: Role,
        display_name: str | None = None,
        email: str | None = None,
    ) -> User:
        user = User(
            username=username.strip(),
            display_name=display_name,
            email=email,
            password_hash=password_hash,
            role=role,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def delete(self, user: User) -> None:
        await self._session.delete(user)


class SessionRepository:
    """Sessions authentifiées, adossées au cookie de session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: int,
        ttl_hours: int,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[UserSession, str]:
        """Crée une session et renvoie ``(session, jeton en clair)``.

        Le jeton en clair n'est renvoyé qu'ici, pour être posé dans le cookie :
        la base n'en garde que l'empreinte.
        """
        token = generate_token()
        now = datetime.now(UTC)
        record = UserSession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            token_hash=hash_token(token),
            created_at=now,
            expires_at=now + timedelta(hours=ttl_hours),
            last_seen_at=now,
            ip_address=ip_address,
            user_agent=(user_agent or "")[:512] or None,
        )
        self._session.add(record)
        await self._session.flush()
        return record, token

    async def get_valid(self, token: str) -> UserSession | None:
        """Session active correspondant au jeton, utilisateur chargé."""
        statement = (
            select(UserSession)
            .where(UserSession.token_hash == hash_token(token))
            .options(selectinload(UserSession.user))
        )
        record = (await self._session.execute(statement)).scalar_one_or_none()
        if record is None or record.revoked_at is not None:
            return None
        if record.expires_at <= datetime.now(UTC):
            return None
        return record

    async def touch(self, record: UserSession) -> None:
        record.last_seen_at = datetime.now(UTC)

    async def revoke(self, record: UserSession) -> None:
        record.revoked_at = datetime.now(UTC)

    async def revoke_all_for_user(self, user_id: int) -> None:
        """Déconnecte partout — après un changement de mot de passe, par exemple."""
        statement = (
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.execute(statement)

    async def purge_expired(self) -> int:
        """Supprime les sessions expirées ou révoquées. Renvoie le nombre effacé."""
        cutoff = datetime.now(UTC)
        statement = select(UserSession).where(UserSession.expires_at <= cutoff)
        expired = list((await self._session.execute(statement)).scalars())
        for record in expired:
            await self._session.delete(record)
        return len(expired)
