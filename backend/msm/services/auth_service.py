"""Authentification : connexion, sessions, mots de passe.

Trois protections, chacune contre une attaque précise :

* **empreinte argon2id** — une fuite de la base ne livre pas les mots de passe ;
* **verrouillage temporaire** — rend la force brute en ligne inexploitable ;
* **vérification systématique, même pour un compte inexistant** — sinon la durée
  de la réponse permettrait d'énumérer les comptes valides.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

from msm.config import Settings
from msm.core.permissions import Role
from msm.db.models.audit import AuditAction, AuditResult
from msm.db.models.user import User, UserSession
from msm.db.repositories import AuditRepository, SessionRepository, UserRepository
from msm.exceptions import AuthenticationError, ConflictError, ValidationError
from msm.logging_conf import get_logger
from msm.security.password import (
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Empreinte factice, calculée une fois avec les paramètres réels.

    Elle sert à vérifier un mot de passe même quand le compte n'existe pas, pour
    que la réponse coûte le même temps. Une empreinte codée en dur ferait échouer
    l'analyse dès le premier caractère et rendrait la protection inopérante.
    """
    return hash_password("mot-de-passe-inexistant")


class AuthService:
    """Cas d'usage liés à l'identité des utilisateurs du panel."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._users = UserRepository(session)
        self._sessions = SessionRepository(session)
        self._audit = AuditRepository(session)

    # ------------------------------------------------------------------ #
    #  Connexion
    # ------------------------------------------------------------------ #
    async def authenticate(
        self,
        username: str,
        password: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[User, str]:
        """Vérifie les identifiants et ouvre une session.

        Renvoie ``(utilisateur, jeton en clair)``. Le jeton n'existe qu'ici et
        dans le cookie : la base n'en garde que l'empreinte.
        """
        user = await self._users.get_by_username(username)

        if user is None:
            # Vérification à vide : la réponse doit coûter le même temps.
            verify_password(_dummy_hash(), password)
            await self._record_failure(username, ip_address, "compte inexistant")
            raise self._invalid_credentials()

        if locked_for := self._locked_seconds(user):
            await self._record_failure(username, ip_address, "compte verrouillé", user=user)
            raise AuthenticationError(
                "Compte temporairement verrouillé.",
                cause=(
                    f"Trop de tentatives infructueuses ; réessai possible dans "
                    f"{locked_for // 60 + 1} minute(s)."
                ),
                remediation=(
                    "Patienter, ou demander à un administrateur de réinitialiser le compte."
                ),
            )

        if not user.is_active:
            await self._record_failure(username, ip_address, "compte désactivé", user=user)
            raise AuthenticationError(
                "Compte désactivé.",
                cause="Ce compte a été désactivé par un administrateur.",
                remediation="Contacter un administrateur du panel.",
            )

        if not verify_password(user.password_hash, password):
            user.failed_attempts += 1
            if user.failed_attempts >= self._settings.max_login_attempts:
                user.locked_until = datetime.now(UTC) + timedelta(
                    minutes=self._settings.login_lockout_minutes
                )
                logger.warning(
                    "account_locked", username=user.username, attempts=user.failed_attempts
                )
            await self._record_failure(username, ip_address, "mot de passe incorrect", user=user)
            raise self._invalid_credentials()

        # Succès : le compteur repart de zéro et l'empreinte est modernisée
        # si les paramètres recommandés d'argon2 ont évolué.
        user.failed_attempts = 0
        user.locked_until = None
        user.last_login_at = datetime.now(UTC)
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)

        _, token = await self._sessions.create(
            user_id=user.id,
            ttl_hours=self._settings.session_ttl_hours,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        self._audit.record(
            action=AuditAction.LOGIN,
            summary=f"Connexion de {user.username}.",
            actor_id=user.id,
            actor_username=user.username,
            actor_role=user.role.value,
            ip_address=ip_address,
        )
        logger.info("login_success", username=user.username, role=user.role.value)
        return user, token

    async def resolve_session(self, token: str) -> tuple[User, UserSession] | None:
        """Retrouve l'utilisateur d'un jeton de session, ou ``None``."""
        record = await self._sessions.get_valid(token)
        if record is None:
            return None
        user = record.user
        if user is None or not user.is_active:
            return None
        await self._sessions.touch(record)
        return user, record

    async def logout(self, token: str, *, ip_address: str | None = None) -> None:
        """Révoque la session. Sans effet si le jeton est déjà invalide."""
        record = await self._sessions.get_valid(token)
        if record is None:
            return
        await self._sessions.revoke(record)
        user = record.user
        self._audit.record(
            action=AuditAction.LOGOUT,
            summary=f"Déconnexion de {user.username}.",
            actor_id=user.id,
            actor_username=user.username,
            actor_role=user.role.value,
            ip_address=ip_address,
        )

    # ------------------------------------------------------------------ #
    #  Mots de passe et comptes
    # ------------------------------------------------------------------ #
    async def change_password(
        self,
        user: User,
        *,
        current_password: str,
        new_password: str,
        ip_address: str | None = None,
    ) -> None:
        """Change le mot de passe et invalide toutes les sessions existantes."""
        if not verify_password(user.password_hash, current_password):
            raise AuthenticationError(
                "Mot de passe actuel incorrect.",
                cause="La vérification du mot de passe actuel a échoué.",
                remediation="Ressaisir le mot de passe actuel.",
            )
        if current_password == new_password:
            raise ValidationError(
                "Nouveau mot de passe identique à l'ancien.",
                cause="Le mot de passe proposé est le mot de passe actuel.",
                remediation="Choisir un mot de passe différent.",
            )

        user.password_hash = hash_password(validate_password_strength(new_password))
        # Un changement de mot de passe doit déconnecter partout : c'est le geste
        # naturel après une suspicion de compromission.
        await self._sessions.revoke_all_for_user(user.id)

        self._audit.record(
            action=AuditAction.PASSWORD_CHANGED,
            summary=f"Mot de passe modifié pour {user.username}.",
            actor_id=user.id,
            actor_username=user.username,
            actor_role=user.role.value,
            ip_address=ip_address,
        )

    async def create_user(
        self,
        *,
        username: str,
        password: str,
        role: Role,
        display_name: str | None = None,
        email: str | None = None,
        actor: User | None = None,
        ip_address: str | None = None,
    ) -> User:
        """Crée un compte."""
        clean_username = username.strip()
        if not clean_username or len(clean_username) > 64:
            raise ValidationError(
                "Nom d'utilisateur invalide.",
                cause="Le nom doit contenir entre 1 et 64 caractères.",
                remediation="Choisir un nom d'utilisateur plus court.",
            )
        if await self._users.get_by_username(clean_username) is not None:
            raise ConflictError(
                "Ce nom d'utilisateur est déjà pris.",
                cause=f"Un compte « {clean_username} » existe déjà.",
                remediation="Choisir un autre nom d'utilisateur.",
            )

        user = await self._users.create(
            username=clean_username,
            password_hash=hash_password(validate_password_strength(password)),
            role=role,
            display_name=display_name,
            email=email,
        )

        self._audit.record(
            action=AuditAction.USER_CREATED,
            summary=f"Création du compte {user.username} ({role.value}).",
            actor_id=actor.id if actor else None,
            actor_username=actor.username if actor else "système",
            actor_role=actor.role.value if actor else None,
            ip_address=ip_address,
            target_type="user",
            target_id=str(user.id),
        )
        return user

    async def count_users(self) -> int:
        return await self._users.count()

    async def list_users(self) -> list[User]:
        return await self._users.list_all()

    async def get_user(self, user_id: int) -> User | None:
        return await self._users.get(user_id)

    async def revoke_all_sessions(self, user_id: int) -> None:
        await self._sessions.revoke_all_for_user(user_id)

    async def purge_expired_sessions(self) -> int:
        return await self._sessions.purge_expired()

    # ------------------------------------------------------------------ #
    def _locked_seconds(self, user: User) -> int:
        if user.locked_until is None:
            return 0
        # Le type UtcDateTime garantit une date avec fuseau, quel que soit le moteur.
        remaining = (user.locked_until - datetime.now(UTC)).total_seconds()
        return int(remaining) if remaining > 0 else 0

    async def _record_failure(
        self,
        username: str,
        ip_address: str | None,
        reason: str,
        *,
        user: User | None = None,
    ) -> None:
        """Consigne un échec de connexion **et valide la transaction**.

        L'appelant lève juste après ; sans validation explicite, la remontée de
        l'exception annulerait la transaction et donc le compteur d'échecs :
        le verrouillage anti-force-brute ne se déclencherait jamais, et aucun
        échec ne figurerait dans le journal d'audit.
        """
        self._audit.record(
            action=AuditAction.LOGIN_FAILED,
            summary=f"Échec de connexion pour « {username} » : {reason}.",
            actor_id=user.id if user else None,
            actor_username=username[:64],
            actor_role=user.role.value if user else None,
            ip_address=ip_address,
            result=AuditResult.DENIED,
        )
        await self._session.commit()
        logger.info("login_failed", username=username, reason=reason, ip=ip_address)

    @staticmethod
    def _invalid_credentials() -> AuthenticationError:
        """Message volontairement identique quel que soit le motif réel.

        Distinguer « compte inconnu » de « mot de passe erroné » livrerait à un
        attaquant la liste des comptes existants.
        """
        return AuthenticationError(
            "Identifiants incorrects.",
            cause="Le nom d'utilisateur ou le mot de passe ne correspond pas.",
            remediation="Vérifier les identifiants saisis.",
        )
