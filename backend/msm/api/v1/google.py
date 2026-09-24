"""Connexion avec Google : démarrage, retour de Google, choix du pseudo, liaison.

Le démarrage et le retour sont des **navigations** du navigateur, pas des appels
d'API : ils répondent par des redirections, et toute erreur renvoie vers l'écran
de connexion (ou le profil) avec un code que l'interface traduit.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Cookie, Request, Response
from fastapi.responses import RedirectResponse

from msm.api.deps import (
    AppSettings,
    AuthServiceDep,
    ClientIp,
    CsrfProtected,
    CurrentUser,
    DbSession,
)
from msm.api.schemas import GoogleSignupOut, GoogleSignupRequest, MeOut
from msm.api.v1.auth import _me, _set_session_cookies
from msm.config import Settings
from msm.db.models.audit import AuditAction
from msm.db.repositories import AuditRepository, UserRepository
from msm.exceptions import ConflictError, MsmError, ValidationError
from msm.i18n import tr
from msm.logging_conf import get_logger
from msm.security.rbac import build_context
from msm.services import google_service as google
from msm.services.account_service import AccountService
from msm.services.avatar_service import MAX_UPLOAD_BYTES, save_avatar

logger = get_logger(__name__)

router = APIRouter(prefix="/auth/google", tags=["authentication"])

#: Les cookies de la tentative ne servent qu'aux routes de ce routeur.
_COOKIE_PATH = "/api/v1/auth/google"


def _redirect(path: str, *, clear: tuple[str, ...] = (google.STATE_COOKIE,)) -> RedirectResponse:
    response = RedirectResponse(path, status_code=302)
    for name in clear:
        response.delete_cookie(name, path=_COOKIE_PATH)
    return response


def _short_cookie(response: Response, name: str, value: str, settings: Settings, ttl: int) -> None:
    response.set_cookie(
        name,
        value,
        max_age=ttl,
        httponly=True,
        secure=settings.session_cookie_secure,
        # « Lax » : le cookie accompagne le retour de Google (navigation de premier
        # niveau), mais pas une requête qu'un autre site ferait en douce.
        samesite="lax",
        path=_COOKIE_PATH,
    )


def _callback(settings: Settings, request: Request) -> str:
    return google.redirect_uri(settings, str(request.base_url))


def suggest_username(email: str, name: str | None) -> str:
    """Un pseudo proposé d'après le nom ou l'adresse, dans les règles des pseudos."""
    source = name or email.split("@")[0]
    candidate = re.sub(r"[^A-Za-z0-9_-]+", "_", source).strip("_-")[:24]
    return candidate if len(candidate) >= 3 else (candidate + "_mc")[:24]


@router.get("/start", summary="Sign in with Google")
async def start(
    request: Request,
    settings: AppSettings,
    auth: AuthServiceDep,
    intent: Literal["login", "link"] = "login",
    invite: str | None = None,
    msm_session: Annotated[str | None, Cookie(alias="msm_session")] = None,
) -> RedirectResponse:
    """Envoie vers Google. `link` lie Google au compte connecté (depuis le profil)."""
    user_id = None
    if intent == "link":
        resolved = await auth.resolve_session(msm_session) if msm_session else None
        if resolved is None:
            return _redirect("/login")
        user_id = resolved[0].id
    try:
        state, url = google.begin(
            settings,
            intent=intent,
            callback=_callback(settings, request),
            user_id=user_id,
            invitation=invite,
        )
    except MsmError:
        return _redirect("/login?google=unavailable")
    response = RedirectResponse(url, status_code=302)
    _short_cookie(response, google.STATE_COOKIE, state, settings, google.ATTEMPT_TTL_S)
    return response


@router.get("/callback", summary="Return from Google")
async def callback(
    request: Request,
    session: DbSession,
    settings: AppSettings,
    auth: AuthServiceDep,
    ip: ClientIp,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    state_cookie: Annotated[str | None, Cookie(alias=google.STATE_COOKIE)] = None,
) -> RedirectResponse:
    if error:
        # L'utilisateur a annulé sur l'écran de Google.
        return _redirect("/login?google=cancelled")
    try:
        attempt = google.take_attempt(state, state_cookie)
        identity = await google.finish(
            settings, attempt, code=code or "", callback=_callback(settings, request)
        )
    except MsmError as exc:
        logger.warning("google_sign_in_failed", cause=exc.cause)
        return _redirect("/login?google=failed")

    users = UserRepository(session)
    linked = await users.get_by_google_sub(identity.sub)

    # --- Liaison depuis le profil ---------------------------------------------
    if attempt.intent == "link":
        user = await users.get(attempt.user_id) if attempt.user_id else None
        if user is None:
            return _redirect("/login")
        if linked is not None and linked.id != user.id:
            return _redirect("/profile?google=taken")
        user.google_sub = identity.sub
        AuditRepository(session).record(
            action=AuditAction.GOOGLE_LINKED,
            summary=tr("{username} linked a Google account.", username=user.username),
            actor_id=user.id,
            actor_username=user.username,
            actor_role=user.role.value,
            ip_address=ip,
            target_type="user",
            target_id=str(user.id),
        )
        return _redirect("/profile?google=linked")

    # --- Connexion d'un compte déjà lié ------------------------------------------
    if linked is not None:
        if linked.is_banned:
            return _redirect("/login?google=banned")
        if not linked.is_active:
            return _redirect("/login?google=disabled")
        token = await auth.open_session(linked, ip_address=ip, method="Google")
        await session.flush()
        response = _redirect("/")
        _set_session_cookies(response, token, settings)
        return response

    # --- Première connexion : un compte à créer, après le choix du pseudo --------
    accounts = AccountService(session, settings)
    try:
        await accounts.ensure_email_free(identity.email)
    except ConflictError:
        # Pas de liaison automatique à un compte existant : la preuve serait
        # seulement l'adresse. Son titulaire se connecte, puis lie Google.
        return _redirect("/login?google=email_taken")
    try:
        await accounts.ensure_can_register(attempt.invitation)
    except MsmError as exc:
        reason = "closed" if exc.code == "REGISTRATION_CLOSED" else "invite"
        return _redirect(f"/login?google={reason}")

    pending = google.hold_signup(identity, attempt.invitation)
    response = _redirect("/register/google")
    _short_cookie(response, google.SIGNUP_COOKIE, pending, settings, google.SIGNUP_TTL_S)
    return response


@router.get("/signup", response_model=GoogleSignupOut, summary="Pending sign-up with Google")
async def signup_info(
    signup_cookie: Annotated[str | None, Cookie(alias=google.SIGNUP_COOKIE)] = None,
) -> GoogleSignupOut:
    identity = google.pending_signup(signup_cookie).identity
    return GoogleSignupOut(
        email=identity.email,
        name=identity.name,
        picture=identity.picture,
        suggested_username=suggest_username(identity.email, identity.name),
    )


async def _import_avatar(settings: Settings, user: object, url: str | None) -> None:
    """La photo Google comme premier avatar — un bonus : un échec ne gêne rien."""
    if not url or not url.startswith("https://"):
        return
    try:
        async with google.new_client() as http:
            response = await http.get(url)
        if response.status_code == 200 and len(response.content) <= MAX_UPLOAD_BYTES:
            await save_avatar(settings, user, response.content)  # type: ignore[arg-type]
    except (httpx.HTTPError, MsmError) as exc:
        logger.info("google_avatar_skipped", error=str(exc))


@router.post("/signup", response_model=MeOut, summary="Finish the sign-up with Google")
async def signup(
    payload: GoogleSignupRequest,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    ip: ClientIp,
    signup_cookie: Annotated[str | None, Cookie(alias=google.SIGNUP_COOKIE)] = None,
) -> MeOut:
    """Crée le compte avec le pseudo choisi, et l'ouvre aussitôt."""
    pending = google.pending_signup(signup_cookie)
    if not payload.accept_terms:
        raise ValidationError(
            tr("The rules must be accepted."),
            cause=tr("Creating an account means accepting the rules of this panel."),
            remediation=tr("Tick the box to accept them."),
        )
    users = UserRepository(session)
    if await users.get_by_google_sub(pending.identity.sub) is not None:
        raise ConflictError(
            tr("This Google account is already used."),
            cause=tr("An MSM account is already linked to it."),
            remediation=tr("Sign in with Google instead."),
        )
    user, token = await AccountService(session, settings).register(
        email=pending.identity.email,
        username=payload.username,
        password=None,
        invitation_token=pending.invitation,
        ip_address=ip,
        google_sub=pending.identity.sub,
    )
    google.drop_signup(signup_cookie)
    await _import_avatar(settings, user, pending.identity.picture)
    await session.flush()
    _set_session_cookies(response, token, settings)
    response.delete_cookie(google.SIGNUP_COOKIE, path=_COOKIE_PATH)
    return _me(user, build_context(user))


@router.post("/unlink", response_model=MeOut, summary="Unlink Google", dependencies=[CsrfProtected])
async def unlink(user: CurrentUser, session: DbSession, ip: ClientIp) -> MeOut:
    """Délie Google — seulement si le compte a un mot de passe, sans quoi il serait perdu."""
    if not user.has_password:
        raise ValidationError(
            tr("Set a password first."),
            cause=tr("Without Google, this account would have no way to sign in."),
            remediation=tr("Choose a password in your profile, then unlink Google."),
        )
    user.google_sub = None
    AuditRepository(session).record(
        action=AuditAction.GOOGLE_UNLINKED,
        summary=tr("{username} unlinked their Google account.", username=user.username),
        actor_id=user.id,
        actor_username=user.username,
        actor_role=user.role.value,
        ip_address=ip,
        target_type="user",
        target_id=str(user.id),
    )
    await session.flush()
    return _me(user, build_context(user))
