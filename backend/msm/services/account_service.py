"""Comptes : inscription, invitations, profil, bannissement.

L'authentification elle-même (connexion, sessions, mot de passe) reste dans
:mod:`msm.services.auth_service` ; ici vit tout ce qui fait d'un compte celui
d'une personne — son pseudo, son adresse, sa langue — et ce que l'équipe de MSM
peut faire contre un compte qui abuse.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from msm.config import Settings
from msm.core.permissions import Permission, Role, ServerRole
from msm.db.models.audit import AuditAction
from msm.db.models.misc import AppSetting
from msm.db.models.server import Server, ServerMember
from msm.db.models.user import NO_PASSWORD, Invitation, User, UsernameChange
from msm.db.repositories import AuditRepository, SessionRepository, UserRepository
from msm.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDenied,
    TooManyRequests,
    ValidationError,
)
from msm.i18n import SUPPORTED_LANGUAGES, tr
from msm.logging_conf import get_logger
from msm.runtime.supervisor import Supervisor
from msm.security.password import hash_password, validate_password_strength, verify_password
from msm.security.ratelimit import SlidingWindowLimiter
from msm.security.rbac import AccessContext
from msm.security.tokens import generate_token, hash_token
from msm.security.usernames import validate_username

logger = get_logger(__name__)

#: Clé du mode d'inscription dans `app_settings`.
REGISTRATION_KEY = "auth.registration"
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
INVITATION_DAYS_MAX = 30

#: Un seul limiteur pour tout le processus : c'est lui qui voit toutes les requêtes.
_registrations: SlidingWindowLimiter | None = None
#: Arrêts de serveurs lancés en tâche de fond après un bannissement.
_background: set[asyncio.Task[None]] = set()


class RegistrationMode(str, Enum):
    #: Seul un admin crée des comptes.
    CLOSED = "closed"
    #: Inscription avec un lien d'invitation à usage unique.
    INVITE = "invite"
    #: N'importe qui peut s'inscrire.
    OPEN = "open"


def registration_limiter(settings: Settings) -> SlidingWindowLimiter:
    global _registrations
    if _registrations is None:
        _registrations = SlidingWindowLimiter(
            limit=settings.registration_limit_per_hour, window_s=3600
        )
    return _registrations


def reset_registration_limiter() -> None:
    """Pour les tests : oublie les inscriptions déjà comptées."""
    global _registrations
    _registrations = None


def normalise_email(raw: str) -> str:
    email = raw.strip().lower()
    if len(email) > 255 or not _EMAIL.fullmatch(email):
        raise ValidationError(
            tr("Invalid e-mail address."),
            cause=tr("“{email}” is not an e-mail address.", email=raw.strip()),
            remediation=tr("Enter an address like name@example.com."),
        )
    return email


@dataclass(frozen=True, slots=True)
class CreatedInvitation:
    invitation: Invitation
    #: Le jeton en clair n'existe qu'ici : la base n'en garde que l'empreinte.
    token: str


class AccountService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._users = UserRepository(session)
        self._sessions = SessionRepository(session)
        self._audit = AuditRepository(session)

    # ------------------------------------------------------------------ #
    #  Mode d'inscription
    # ------------------------------------------------------------------ #
    async def registration_mode(self) -> RegistrationMode:
        row = await self._session.get(AppSetting, REGISTRATION_KEY)
        value = row.value.get("mode") if row is not None and isinstance(row.value, dict) else None
        try:
            return RegistrationMode(value)
        except ValueError:
            # Fermée tant qu'un admin n'a rien décidé : l'ouverture est un choix.
            return RegistrationMode.CLOSED

    async def set_registration_mode(
        self, mode: RegistrationMode, *, context: AccessContext, ip_address: str | None = None
    ) -> RegistrationMode:
        context.require(Permission.SETTINGS_MANAGE, action=tr("change the settings"))
        row = await self._session.get(AppSetting, REGISTRATION_KEY)
        if row is None:
            self._session.add(AppSetting(key=REGISTRATION_KEY, value={"mode": mode.value}))
        else:
            row.value = {"mode": mode.value}
        self._audit.record(
            action=AuditAction.SETTINGS_UPDATED,
            summary=tr("Registration set to “{mode}”.", mode=mode.value),
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            target_type="settings",
            payload={"registration": mode.value},
        )
        return mode

    # ------------------------------------------------------------------ #
    #  Invitations
    # ------------------------------------------------------------------ #
    async def create_invitation(
        self,
        *,
        note: str | None,
        days: int,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> CreatedInvitation:
        context.require(Permission.USER_MANAGE, action=tr("invite someone"))
        if not 1 <= days <= INVITATION_DAYS_MAX:
            raise ValidationError(
                tr("Invalid duration."),
                cause=tr("An invitation lasts between 1 and {max} days.", max=INVITATION_DAYS_MAX),
                remediation=tr("Choose a shorter duration."),
            )
        token = generate_token()
        now = datetime.now(UTC)
        invitation = Invitation(
            token_hash=hash_token(token),
            note=(note or "").strip()[:128] or None,
            created_by=context.user_id,
            created_at=now,
            expires_at=now + timedelta(days=days),
        )
        self._session.add(invitation)
        await self._session.flush()
        self._audit.record(
            action=AuditAction.INVITATION_CREATED,
            summary=tr("Invitation created ({days} days).", days=days),
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            target_type="invitation",
            target_id=str(invitation.id),
            payload={"note": invitation.note},
        )
        return CreatedInvitation(invitation, token)

    async def list_invitations(self, *, context: AccessContext) -> list[Invitation]:
        context.require(Permission.USER_MANAGE, action=tr("invite someone"))
        statement = select(Invitation).order_by(Invitation.created_at.desc())
        return list((await self._session.execute(statement)).scalars())

    async def revoke_invitation(self, invitation_id: int, *, context: AccessContext) -> None:
        context.require(Permission.USER_MANAGE, action=tr("invite someone"))
        invitation = await self._session.get(Invitation, invitation_id)
        if invitation is None:
            raise NotFoundError(
                tr("Invitation not found."),
                cause=tr("No invitation has the identifier {id}.", id=invitation_id),
                remediation=tr("Refresh the invitation list."),
            )
        await self._session.delete(invitation)

    async def _usable_invitation(self, token: str | None) -> Invitation:
        invitation = None
        if token:
            statement = select(Invitation).where(Invitation.token_hash == hash_token(token))
            invitation = (await self._session.execute(statement)).scalar_one_or_none()
        if (
            invitation is None
            or invitation.used_at is not None
            or invitation.expires_at <= datetime.now(UTC)
        ):
            raise PermissionDenied(
                tr("Invalid invitation."),
                cause=tr("This invitation link is unknown, already used or expired."),
                remediation=tr("Ask an administrator for a new invitation."),
                code="INVITATION_INVALID",
            )
        return invitation

    # ------------------------------------------------------------------ #
    #  Inscription
    # ------------------------------------------------------------------ #
    async def ensure_can_register(self, invitation_token: str | None) -> RegistrationMode:
        """Refuse d'emblée une inscription impossible (fermée, invitation invalide)."""
        mode = await self.registration_mode()
        if mode is RegistrationMode.CLOSED:
            raise PermissionDenied(
                tr("Registration is closed."),
                cause=tr("Accounts on this panel are created by its administrators."),
                remediation=tr("Ask an administrator for an account or an invitation."),
                code="REGISTRATION_CLOSED",
            )
        if mode is RegistrationMode.INVITE:
            await self._usable_invitation(invitation_token)
        return mode

    async def register(
        self,
        *,
        email: str,
        username: str,
        password: str | None,
        invitation_token: str | None = None,
        ip_address: str | None = None,
        google_sub: str | None = None,
    ) -> tuple[User, str]:
        """Crée un compte user et ouvre sa session. Renvoie ``(compte, jeton)``.

        Sans mot de passe, le compte vient de Google (`google_sub`) : il ne se
        connecte que par Google, jusqu'à ce qu'il se définisse un mot de passe.
        """
        mode = await self.ensure_can_register(invitation_token)

        limiter = registration_limiter(self._settings)
        key = ip_address or "unknown"
        if not limiter.allow(key):
            raise TooManyRequests(
                tr("Too many registrations."),
                cause=tr("Several accounts were just created from this address."),
                remediation=tr(
                    "Try again in {minutes} minute(s).",
                    minutes=limiter.retry_after(key) // 60 + 1,
                ),
            )

        invitation = (
            await self._usable_invitation(invitation_token)
            if mode is RegistrationMode.INVITE
            else None
        )
        clean_username = validate_username(username)
        await self._ensure_username_free(clean_username)
        clean_email = normalise_email(email)
        await self.ensure_email_free(clean_email)
        password_hash = (
            hash_password(validate_password_strength(password))
            if password is not None
            else NO_PASSWORD
        )

        user = await self._users.create(
            username=clean_username,
            password_hash=password_hash,
            role=Role.USER,
            email=clean_email,
        )
        user.google_sub = google_sub
        if invitation is not None:
            invitation.used_at = datetime.now(UTC)
            invitation.used_by = user.id

        user.last_login_at = datetime.now(UTC)
        _, token = await self._sessions.create(
            user_id=user.id,
            ttl_hours=self._settings.session_ttl_hours,
            ip_address=ip_address,
            user_agent=None,
        )
        self._audit.record(
            action=AuditAction.USER_REGISTERED,
            summary=tr("{username} created an account.", username=user.username),
            actor_id=user.id,
            actor_username=user.username,
            actor_role=user.role.value,
            ip_address=ip_address,
            target_type="user",
            target_id=str(user.id),
            payload={
                "invitation": invitation.id if invitation else None,
                "method": "google" if google_sub else "password",
            },
        )
        logger.info("user_registered", user_id=user.id, username=user.username)
        return user, token

    # ------------------------------------------------------------------ #
    #  Profil
    # ------------------------------------------------------------------ #
    async def rename(self, user: User, new_username: str, *, ip_address: str | None = None) -> User:
        """Change le pseudo. L'ancien reste réservé à ce compte ; aucun fichier ne bouge."""
        clean = validate_username(new_username)
        if clean == user.username:
            return user
        await self._ensure_username_free(clean, allow_user_id=user.id)

        previous = user.username
        self._session.add(
            UsernameChange(user_id=user.id, username=previous, changed_at=datetime.now(UTC))
        )
        user.username = clean
        await self._session.flush()
        self._audit.record(
            action=AuditAction.USER_RENAMED,
            summary=tr("{previous} is now called {username}.", previous=previous, username=clean),
            actor_id=user.id,
            actor_username=clean,
            actor_role=user.role.value,
            ip_address=ip_address,
            target_type="user",
            target_id=str(user.id),
            payload={"previous": previous, "username": clean},
        )
        return user

    async def change_email(
        self, user: User, email: str, *, current_password: str, ip_address: str | None = None
    ) -> User:
        """Change l'adresse, contre le mot de passe actuel : c'est elle qui récupère le compte.

        Un compte créé avec Google n'a pas de mot de passe à confirmer.
        """
        if user.has_password and not verify_password(user.password_hash, current_password):
            raise ValidationError(
                tr("Wrong password."),
                cause=tr("The current password does not match."),
                remediation=tr("Enter your current password to confirm."),
            )
        clean = normalise_email(email)
        if clean == user.email:
            return user
        await self.ensure_email_free(clean, allow_user_id=user.id)
        user.email = clean
        self._audit.record(
            action=AuditAction.USER_UPDATED,
            summary=tr("{username} changed their e-mail address.", username=user.username),
            actor_id=user.id,
            actor_username=user.username,
            actor_role=user.role.value,
            ip_address=ip_address,
            target_type="user",
            target_id=str(user.id),
            payload={"changes": ["email"]},
        )
        return user

    @staticmethod
    def set_language(user: User, language: str | None) -> User:
        """Langue du compte ; ``None`` : celle du panneau."""
        if language is not None and language not in SUPPORTED_LANGUAGES:
            raise ValidationError(
                tr("Unsupported language."),
                cause=tr("“{language}” is not an available language.", language=language),
                remediation=tr("Choose one of: {choices}.", choices=", ".join(SUPPORTED_LANGUAGES)),
            )
        user.language = language
        return user

    # ------------------------------------------------------------------ #
    #  Bannissement
    # ------------------------------------------------------------------ #
    async def ban(
        self,
        target: User,
        *,
        reason: str,
        context: AccessContext,
        supervisor: Supervisor,
        ip_address: str | None = None,
    ) -> User:
        """Bannit un compte : sessions fermées, connexion refusée, serveurs arrêtés."""
        context.require(Permission.USER_BAN, action=tr("ban an account"))
        await self._check_can_sanction(target, context)
        clean_reason = reason.strip()
        if not clean_reason:
            raise ValidationError(
                tr("Missing reason."),
                cause=tr("A ban is shown to the banned account: it needs a reason."),
                remediation=tr("Explain why the account is banned."),
            )
        if target.role is Role.ADMIN and await self._other_admins(target) == 0:
            raise ConflictError(
                tr("This is the last administrator."),
                cause=tr("Banning them would leave the panel without an administrator."),
                remediation=tr("Promote another account to administrator first."),
            )

        target.banned_at = datetime.now(UTC)
        target.banned_by = context.user_id
        target.ban_reason = clean_reason[:1000]
        await self._sessions.revoke_all_for_user(target.id)

        stopped = await self._stop_servers_of(target, supervisor, actor=context.username)
        self._audit.record(
            action=AuditAction.USER_BANNED,
            summary=tr(
                "{username} banned: {reason}", username=target.username, reason=clean_reason
            ),
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            target_type="user",
            target_id=str(target.id),
            payload={"reason": clean_reason, "servers_stopped": stopped},
        )
        logger.warning("user_banned", user_id=target.id, servers_stopped=stopped)
        return target

    async def unban(
        self, target: User, *, context: AccessContext, ip_address: str | None = None
    ) -> User:
        """Rend l'accès. Les serveurs ne redémarrent pas d'eux-mêmes."""
        context.require(Permission.USER_BAN, action=tr("ban an account"))
        await self._check_can_sanction(target, context)
        target.banned_at = None
        target.banned_by = None
        target.ban_reason = None
        self._audit.record(
            action=AuditAction.USER_UNBANNED,
            summary=tr("{username} unbanned.", username=target.username),
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            target_type="user",
            target_id=str(target.id),
        )
        return target

    async def _check_can_sanction(self, target: User, context: AccessContext) -> None:
        if target.id == context.user_id:
            raise ValidationError(
                tr("You cannot ban yourself."),
                cause=tr("You would immediately lose all access."),
                remediation=tr("Ask another administrator to do this."),
            )
        # Un modérateur sanctionne les users, pas l'équipe de MSM.
        if target.role is not Role.USER and not context.has(Permission.USER_MANAGE):
            raise PermissionDenied(
                tr("Action not allowed."),
                cause=tr("Only an administrator can ban an administrator or a moderator."),
                remediation=tr("Ask an administrator to do this."),
            )

    async def _other_admins(self, target: User) -> int:
        statement = select(func.count(User.id)).where(
            User.role == Role.ADMIN,
            User.id != target.id,
            User.is_active.is_(True),
            User.banned_at.is_(None),
        )
        return int((await self._session.execute(statement)).scalar() or 0)

    async def _stop_servers_of(self, user: User, supervisor: Supervisor, *, actor: str) -> int:
        """Arrête proprement les serveurs en marche du compte, en tâche de fond.

        Un arrêt peut prendre une minute (sauvegarde du monde) : la réponse n'a pas
        à l'attendre. Le blocage des démarrages, lui, est immédiat (voir
        `LifecycleService.start`).
        """
        owned = select(Server.id).where(Server.owner_id == user.id)
        running = [
            runtime
            for server_id in (await self._session.execute(owned)).scalars()
            if (runtime := supervisor.find(server_id)) is not None and runtime.state.is_running
        ]
        for runtime in running:
            task = asyncio.create_task(
                self._stop(runtime, actor), name=f"msm-ban-stop-{runtime.id}"
            )
            _background.add(task)
            task.add_done_callback(_background.discard)
        return len(running)

    @staticmethod
    async def _stop(runtime: Any, actor: str) -> None:
        try:
            await runtime.stop(actor=actor)
        except Exception as exc:  # pragma: no cover - déjà arrêté entre-temps
            logger.warning("ban_stop_failed", server_id=runtime.id, error=str(exc))

    # ------------------------------------------------------------------ #
    #  Consultation par l'équipe
    # ------------------------------------------------------------------ #
    async def username_history(self, user_id: int) -> list[UsernameChange]:
        statement = (
            select(UsernameChange)
            .where(UsernameChange.user_id == user_id)
            .order_by(UsernameChange.changed_at.desc())
        )
        return list((await self._session.execute(statement)).scalars())

    async def servers_of(
        self, user_id: int
    ) -> tuple[list[Server], list[tuple[Server, ServerRole]]]:
        """Serveurs possédés par le compte, et ceux partagés avec lui (avec son rôle)."""
        owned = select(Server).where(Server.owner_id == user_id).order_by(Server.name)
        shared = (
            select(Server, ServerMember.role)
            .join(ServerMember, ServerMember.server_id == Server.id)
            .where(ServerMember.user_id == user_id)
            .order_by(Server.name)
        )
        return (
            list((await self._session.execute(owned)).scalars()),
            [(row[0], row[1]) for row in (await self._session.execute(shared)).all()],
        )

    # ------------------------------------------------------------------ #
    #  Unicité
    # ------------------------------------------------------------------ #
    async def _ensure_username_free(
        self, username: str, *, allow_user_id: int | None = None
    ) -> None:
        existing = await self._users.get_by_username(username)
        if existing is not None and existing.id != allow_user_id:
            raise ConflictError(
                tr("This username is already taken."),
                cause=tr("An account “{username}” already exists.", username=username),
                remediation=tr("Choose another username."),
            )
        # Un pseudo quitté reste à son ancien titulaire : pas d'usurpation.
        statement = select(UsernameChange.user_id).where(UsernameChange.username.ilike(username))
        holders = set((await self._session.execute(statement)).scalars())
        if holders - {allow_user_id}:
            raise ConflictError(
                tr("This username is already taken."),
                cause=tr("“{username}” was recently used by another account.", username=username),
                remediation=tr("Choose another username."),
            )

    async def ensure_email_free(self, email: str, *, allow_user_id: int | None = None) -> None:
        statement = select(User.id).where(func.lower(User.email) == email)
        holders = set((await self._session.execute(statement)).scalars())
        if holders - {allow_user_id}:
            raise ConflictError(
                tr("This e-mail address is already used."),
                cause=tr("Another account already uses {email}.", email=email),
                remediation=tr("Sign in with that account, or use another address."),
            )
