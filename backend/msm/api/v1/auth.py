"""Connexion, déconnexion et compte courant.

Le jeton de session vit dans un cookie **HttpOnly** : le JavaScript de la page ne
peut pas le lire, donc une faille XSS ne suffit pas à voler la session — ce qui
serait le cas avec un jeton stocké dans le `localStorage`.

Un second cookie, lisible celui-là, porte le jeton anti-CSRF : le navigateur du
visiteur d'un site tiers enverra bien le cookie de session, mais ce site ne peut
pas lire le cookie CSRF pour le recopier dans l'en-tête attendu.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, File, Response, UploadFile, status

from msm.api.deps import (
    CSRF_COOKIE_NAME,
    AppSettings,
    AuthServiceDep,
    ClientIp,
    CsrfProtected,
    CurrentUser,
    DbSession,
    GlobalContext,
    SupervisorDep,
)
from msm.api.schemas import (
    CsrfOut,
    EmailChangeRequest,
    LoginRequest,
    MeOut,
    PasswordChangeRequest,
    ProfileUpdateRequest,
    RegisterRequest,
    RegistrationInfoOut,
    UserOut,
)
from msm.api.schemas.hosting import MyQuotaOut, QuotaModel, UsageOut
from msm.config import Settings
from msm.db.models.user import User
from msm.exceptions import ValidationError
from msm.i18n import tr
from msm.security.rbac import AccessContext, build_context
from msm.security.tokens import generate_token
from msm.services.account_service import AccountService
from msm.services.avatar_service import MAX_UPLOAD_BYTES, delete_avatar, save_avatar
from msm.services.google_service import enabled as google_enabled
from msm.services.hosting_service import HostingService

router = APIRouter(prefix="/auth", tags=["authentication"])


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


@router.post("/login", response_model=MeOut, summary="Sign in")
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


@router.post("/logout", summary="Sign out", dependencies=[CsrfProtected])
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
    return {"status": "signed_out"}


@router.get("/me", response_model=MeOut, summary="Current account")
async def me(user: CurrentUser, context: GlobalContext) -> MeOut:
    """Profil de l'utilisateur connecté et ses permissions effectives."""
    return _me(user, context)


@router.get("/csrf", response_model=CsrfOut, summary="Renew the anti-CSRF token")
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


@router.post("/password", summary="Change own password", dependencies=[CsrfProtected])
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
    return {"status": "password_changed", "detail": tr("All sessions have been closed.")}


# --------------------------------------------------------------------------- #
#  Inscription
# --------------------------------------------------------------------------- #
@router.get("/registration", response_model=RegistrationInfoOut, summary="Can I sign up?")
async def registration_info(session: DbSession, settings: AppSettings) -> RegistrationInfoOut:
    """Public : l'écran de connexion n'affiche « créer un compte » que si c'est possible."""
    mode = await AccountService(session, settings).registration_mode()
    return RegistrationInfoOut(mode=mode.value, google=google_enabled(settings))


@router.post(
    "/register",
    response_model=MeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
async def register(
    payload: RegisterRequest,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    ip: ClientIp,
) -> MeOut:
    """Crée un compte user et l'ouvre aussitôt, comme une connexion."""
    if not payload.accept_terms:
        raise ValidationError(
            tr("The rules must be accepted."),
            cause=tr("Creating an account means accepting the rules of this panel."),
            remediation=tr("Tick the box to accept them."),
        )
    user, token = await AccountService(session, settings).register(
        email=payload.email,
        username=payload.username,
        password=payload.password,
        invitation_token=payload.invitation,
        ip_address=ip,
    )
    await session.flush()
    _set_session_cookies(response, token, settings)
    return _me(user, build_context(user))


# --------------------------------------------------------------------------- #
#  Profil
# --------------------------------------------------------------------------- #
@router.put("/me", response_model=MeOut, summary="Update my profile", dependencies=[CsrfProtected])
async def update_profile(
    payload: ProfileUpdateRequest,
    user: CurrentUser,
    context: GlobalContext,
    session: DbSession,
    settings: AppSettings,
    ip: ClientIp,
) -> MeOut:
    """Pseudo et langue. Un champ absent reste inchangé."""
    accounts = AccountService(session, settings)
    if payload.username is not None:
        await accounts.rename(user, payload.username, ip_address=ip)
    if "language" in payload.model_fields_set:
        accounts.set_language(user, payload.language)
    await session.flush()
    return _me(user, build_context(user))


@router.put(
    "/me/email", response_model=MeOut, summary="Change my e-mail", dependencies=[CsrfProtected]
)
async def change_email(
    payload: EmailChangeRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    ip: ClientIp,
) -> MeOut:
    await AccountService(session, settings).change_email(
        user, payload.email, current_password=payload.current_password, ip_address=ip
    )
    await session.flush()
    return _me(user, build_context(user))


@router.put(
    "/me/avatar", response_model=MeOut, summary="Upload my avatar", dependencies=[CsrfProtected]
)
async def upload_avatar(
    file: Annotated[UploadFile, File(description="PNG, JPEG or WebP image, 2 MB at most")],
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MeOut:
    # Lecture bornée : un fichier énorme n'a pas à être chargé en entier pour être refusé.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    await save_avatar(settings, user, data)
    await session.flush()
    return _me(user, build_context(user))


@router.delete(
    "/me/avatar", response_model=MeOut, summary="Remove my avatar", dependencies=[CsrfProtected]
)
async def remove_avatar(user: CurrentUser, session: DbSession, settings: AppSettings) -> MeOut:
    delete_avatar(settings, user)
    await session.flush()
    return _me(user, build_context(user))


@router.get("/me/quota", response_model=MyQuotaOut, summary="My limits and usage")
async def my_quota(
    user: CurrentUser, session: DbSession, settings: AppSettings, supervisor: SupervisorDep
) -> MyQuotaOut:
    """Ce que le compte peut encore créer ou démarrer, et ce qu'il occupe déjà."""
    return await quota_report(user, HostingService(session, settings), supervisor)


async def quota_report(user: User, hosting: HostingService, supervisor: Any) -> MyQuotaOut:
    quota = await hosting.quota_for(user)
    usage = await hosting.usage_of(user, supervisor)
    return MyQuotaOut(
        quota=QuotaModel(**asdict(quota)) if quota is not None else None,
        usage=UsageOut(
            servers=usage.servers,
            memory_online_mb=usage.memory_online_mb,
            disk_mb=round(usage.disk_mb, 1),
        ),
    )
