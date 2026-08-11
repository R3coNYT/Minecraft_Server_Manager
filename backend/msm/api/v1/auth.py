"""Connexion, déconnexion et compte courant.

Le jeton de session vit dans un cookie **HttpOnly** : le JavaScript de la page ne
peut pas le lire, donc une faille XSS ne suffit pas à voler la session — ce qui
serait le cas avec un jeton stocké dans le `localStorage`.

Un second cookie, lisible celui-là, porte le jeton anti-CSRF : le navigateur du
visiteur d'un site tiers enverra bien le cookie de session, mais ce site ne peut
pas lire le cookie CSRF pour le recopier dans l'en-tête attendu.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Response

from msm.api.deps import (
    CSRF_COOKIE_NAME,
    AppSettings,
    AuthServiceDep,
    ClientIp,
    CsrfProtected,
    CurrentUser,
    DbSession,
    GlobalContext,
)
from msm.api.schemas import CsrfOut, LoginRequest, MeOut, PasswordChangeRequest, UserOut
from msm.config import Settings
from msm.db.models.user import User
from msm.security.rbac import AccessContext, build_context
from msm.security.tokens import generate_token

router = APIRouter(prefix="/auth", tags=["authentification"])


def _set_session_cookies(response: Response, token: str, settings: Settings) -> str:
    """Pose le cookie de session et un nouveau jeton CSRF. Renvoie ce dernier."""
    max_age = settings.session_ttl_hours * 3600
    csrf_token = generate_token()

    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        max_age=max_age,
        httponly=False,  # doit être lisible par le frontend pour être renvoyé
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    return csrf_token


def _me(user: User, context: AccessContext) -> MeOut:
    """Assemble le profil courant et ses permissions effectives."""
    return MeOut(
        **UserOut.model_validate(user).model_dump(),
        permissions=sorted(context.permissions, key=lambda permission: permission.value),
    )


def _clear_session_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


@router.post("/login", response_model=MeOut, summary="Ouvrir une session")
async def login(
    payload: LoginRequest,
    response: Response,
    auth: AuthServiceDep,
    settings: AppSettings,
    ip: ClientIp,
    session: DbSession,
) -> MeOut:
    """Authentifie l'utilisateur et pose les cookies de session."""
    user, token = await auth.authenticate(
        payload.username,
        payload.password,
        ip_address=ip,
        user_agent=None,
    )
    # La session doit exister en base avant que le cookie ne parte au navigateur.
    await session.flush()
    _set_session_cookies(response, token, settings)
    return _me(user, build_context(user))


@router.post("/logout", summary="Fermer la session", dependencies=[CsrfProtected])
async def logout(
    response: Response,
    auth: AuthServiceDep,
    settings: AppSettings,
    ip: ClientIp,
    msm_session: Annotated[str | None, Cookie(alias="msm_session")] = None,
) -> dict[str, str]:
    """Révoque la session côté serveur et efface les cookies."""
    if msm_session:
        await auth.logout(msm_session, ip_address=ip)
    _clear_session_cookies(response, settings)
    return {"status": "déconnecté"}


@router.get("/me", response_model=MeOut, summary="Compte courant")
async def me(user: CurrentUser, context: GlobalContext) -> MeOut:
    """Profil de l'utilisateur connecté et ses permissions effectives."""
    return _me(user, context)


@router.get("/csrf", response_model=CsrfOut, summary="Renouveler le jeton anti-CSRF")
async def csrf(response: Response, settings: AppSettings, user: CurrentUser) -> CsrfOut:
    """Fournit un jeton anti-CSRF frais, par exemple après un rechargement."""
    token = generate_token()
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    return CsrfOut(csrf_token=token)


@router.post("/password", summary="Changer son mot de passe", dependencies=[CsrfProtected])
async def change_password(
    payload: PasswordChangeRequest,
    response: Response,
    user: CurrentUser,
    auth: AuthServiceDep,
    settings: AppSettings,
    ip: ClientIp,
) -> dict[str, str]:
    """Change le mot de passe et déconnecte toutes les sessions, celle-ci comprise."""
    await auth.change_password(
        user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        ip_address=ip,
    )
    _clear_session_cookies(response, settings)
    return {"status": "mot de passe modifié", "detail": "Toutes les sessions ont été fermées."}
