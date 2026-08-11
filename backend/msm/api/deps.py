"""Dépendances FastAPI : session, utilisateur courant, CSRF, permissions.

Le contrôle d'accès passe **par le système de dépendances**, jamais par du code
dispersé dans les routes. Une route qui oublierait la dépendance ne compilerait
pas son contexte d'accès et n'aurait donc aucun moyen d'agir : l'oubli se voit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from msm.config import Settings, get_settings
from msm.core.permissions import Permission
from msm.db.models.server import Server
from msm.db.models.user import User
from msm.db.repositories import ServerPermissionRepository
from msm.db.session import session_scope
from msm.exceptions import AuthenticationError, PermissionDenied
from msm.runtime.supervisor import Supervisor
from msm.security.rbac import AccessContext, build_context
from msm.security.tokens import tokens_equal
from msm.services.auth_service import AuthService
from msm.services.server_service import ServerService

CSRF_COOKIE_NAME = "msm_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"


# --------------------------------------------------------------------------- #
#  Infrastructure
# --------------------------------------------------------------------------- #
async def get_db() -> AsyncIterator[AsyncSession]:
    """Session de base de données, validée en fin de requête."""
    async with session_scope() as session:
        yield session


def get_app_settings(request: Request) -> Settings:
    """Réglages de **cette** application, pas ceux du cache global.

    L'application est construite avec ses réglages ; les dépendances doivent les
    honorer. Retomber sur `get_settings()` ferait qu'une instance configurée pour
    écrire ailleurs — une seconde instance, un test — irait tout de même lire et
    écrire dans les dossiers par défaut.
    """
    settings: Settings | None = getattr(request.app.state, "settings", None)
    return settings or get_settings()


def get_supervisor(request: Request) -> Supervisor:
    supervisor: Supervisor | None = getattr(request.app.state, "supervisor", None)
    if supervisor is None:  # pragma: no cover - impossible hors test mal configuré
        raise RuntimeError("Le superviseur n'est pas initialisé.")
    return supervisor


def client_ip(request: Request) -> str | None:
    """Adresse du client, journalisée dans l'audit.

    L'en-tête `X-Forwarded-For` n'est **pas** lu : sans configuration explicite du
    reverse proxy de confiance, il est falsifiable et rendrait l'audit trompeur.
    """
    return request.client.host if request.client else None


DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]
SupervisorDep = Annotated[Supervisor, Depends(get_supervisor)]
ClientIp = Annotated[str | None, Depends(client_ip)]


# --------------------------------------------------------------------------- #
#  Services
# --------------------------------------------------------------------------- #
def get_auth_service(session: DbSession, settings: AppSettings) -> AuthService:
    return AuthService(session, settings)


def get_server_service(
    session: DbSession, settings: AppSettings, supervisor: SupervisorDep
) -> ServerService:
    return ServerService(session, settings, supervisor)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
ServerServiceDep = Annotated[ServerService, Depends(get_server_service)]


# --------------------------------------------------------------------------- #
#  Authentification
# --------------------------------------------------------------------------- #
async def get_current_user(
    auth: AuthServiceDep,
    settings: AppSettings,
    msm_session: Annotated[str | None, Cookie(alias="msm_session")] = None,
) -> User:
    """Utilisateur authentifié, ou 401."""
    if not msm_session:
        raise AuthenticationError(
            "Authentification requise.",
            cause="Aucun cookie de session n'accompagne la requête.",
            remediation="Se connecter au panel.",
        )

    resolved = await auth.resolve_session(msm_session)
    if resolved is None:
        raise AuthenticationError(
            "Session expirée ou invalide.",
            cause="Le jeton de session n'est plus valide.",
            remediation="Se reconnecter au panel.",
        )
    user, _ = resolved
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_csrf(
    msm_csrf: Annotated[str | None, Cookie(alias=CSRF_COOKIE_NAME)] = None,
    header_token: Annotated[str | None, Header(alias=CSRF_HEADER_NAME)] = None,
) -> None:
    """Double soumission du jeton CSRF, exigée sur toute requête modifiante.

    Le cookie de session étant envoyé automatiquement par le navigateur, un site
    tiers pourrait déclencher une action à l'insu de l'utilisateur. Il ne peut en
    revanche pas **lire** le cookie CSRF pour le recopier dans l'en-tête.
    """
    if not msm_csrf or not header_token or not tokens_equal(msm_csrf, header_token):
        raise PermissionDenied(
            "Requête refusée : jeton anti-CSRF absent ou invalide.",
            cause="L'en-tête X-CSRF-Token ne correspond pas au cookie de session.",
            remediation="Recharger la page pour obtenir un nouveau jeton.",
            code="CSRF_INVALID",
        )


CsrfProtected = Depends(require_csrf)


# --------------------------------------------------------------------------- #
#  Autorisation
# --------------------------------------------------------------------------- #
async def get_global_context(user: CurrentUser) -> AccessContext:
    """Droits de l'utilisateur hors périmètre serveur."""
    return build_context(user)


GlobalContext = Annotated[AccessContext, Depends(get_global_context)]


async def get_server_and_context(
    server_id: int,
    user: CurrentUser,
    session: DbSession,
    service: ServerServiceDep,
) -> tuple[Server, AccessContext]:
    """Charge un serveur **et** les droits de l'utilisateur sur ce serveur."""
    server = await service.get_server(server_id)
    override = await ServerPermissionRepository(session).get(user.id, server_id)
    context = build_context(user, server_id=server_id, override=override)
    # Ne pas révéler l'existence d'un serveur auquel l'utilisateur n'a pas accès.
    context.require(Permission.SERVER_VIEW, action="consulter ce serveur")
    return server, context


ServerAccess = Annotated[tuple[Server, AccessContext], Depends(get_server_and_context)]


def require_permission(permission: Permission, *, action: str | None = None):
    """Dépendance exigeant une permission **globale**."""

    async def _dependency(context: GlobalContext) -> AccessContext:
        context.require(permission, action=action)
        return context

    return _dependency


def require_server_permission(permission: Permission, *, action: str | None = None):
    """Dépendance exigeant une permission **sur le serveur de l'URL**."""

    async def _dependency(access: ServerAccess) -> tuple[Server, AccessContext]:
        server, context = access
        context.require(permission, action=action)
        return server, context

    return _dependency
