"""Connexion avec Google (OpenID Connect, flux « authorization code » + PKCE).

Déroulé :

1. ``/auth/google/start`` prépare une tentative — `state`, `nonce`, vérificateur
   PKCE — gardée en mémoire, et lie le navigateur par un cookie portant le
   `state`. Puis redirection vers Google.
2. Google renvoie sur ``/auth/google/callback`` avec un code. On vérifie que le
   `state` est bien celui de **ce** navigateur (sinon, quelqu'un pourrait nous
   connecter à son compte à notre insu), puis on échange le code contre les jetons,
   directement auprès de Google, avec le secret du client.
3. L'ID token reçu ainsi, en direct et en TLS, n'a pas besoin de vérification de
   signature (OpenID Connect Core, §3.1.3.7) : on contrôle émetteur, audience,
   expiration, nonce et adresse vérifiée.

Le compte MSM est lié à l'identifiant Google stable (`sub`), jamais à l'adresse
e-mail, qui peut changer. Une première connexion ne crée pas le compte d'emblée :
MSM demande d'abord de choisir un pseudo.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from msm.config import Settings
from msm.exceptions import AuthenticationError, ConfigurationError
from msm.i18n import tr
from msm.logging_conf import get_logger

logger = get_logger(__name__)

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - une adresse, pas un secret
ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
CALLBACK_PATH = "/api/v1/auth/google/callback"
#: Cookie qui lie une tentative à ce navigateur.
STATE_COOKIE = "msm_google_state"
#: Cookie qui désigne une inscription en attente de pseudo.
SIGNUP_COOKIE = "msm_google_signup"
#: Le temps de passer par l'écran de Google, ou de choisir son pseudo.
ATTEMPT_TTL_S = 600
SIGNUP_TTL_S = 900


@dataclass(slots=True)
class Attempt:
    #: `login` : se connecter (ou s'inscrire) ; `link` : lier Google à un compte.
    intent: str
    nonce: str
    verifier: str
    user_id: int | None = None
    invitation: str | None = None
    created: float = field(default_factory=time.monotonic)


@dataclass(slots=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str | None
    picture: str | None


@dataclass(slots=True)
class PendingSignup:
    identity: GoogleIdentity
    invitation: str | None
    created: float = field(default_factory=time.monotonic)


#: Tentatives et inscriptions en cours. En mémoire : un seul processus MSM, et
#: une tentative perdue au redémarrage se recommence en un clic.
_attempts: dict[str, Attempt] = {}
_signups: dict[str, PendingSignup] = {}


def _purge() -> None:
    now = time.monotonic()
    for key in [key for key, item in _attempts.items() if now - item.created > ATTEMPT_TTL_S]:
        _attempts.pop(key, None)
    for key in [key for key, item in _signups.items() if now - item.created > SIGNUP_TTL_S]:
        _signups.pop(key, None)


def reset() -> None:
    """Pour les tests."""
    _attempts.clear()
    _signups.clear()


def new_client() -> httpx.AsyncClient:
    """Client HTTP vers Google ; remplacé dans les tests."""
    return httpx.AsyncClient(timeout=15.0)


def enabled(settings: Settings) -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def redirect_uri(settings: Settings, request_base: str) -> str:
    """Adresse de retour déclarée chez Google : l'adresse publique, sinon celle de la requête."""
    base = (settings.public_url or request_base).rstrip("/")
    return f"{base}{CALLBACK_PATH}"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def begin(
    settings: Settings,
    *,
    intent: str,
    callback: str,
    user_id: int | None = None,
    invitation: str | None = None,
) -> tuple[str, str]:
    """Prépare une tentative. Renvoie ``(state, adresse de Google)``."""
    if not enabled(settings):
        raise ConfigurationError(
            tr("Sign-in with Google is not configured."),
            cause=tr("MSM_GOOGLE_CLIENT_ID and MSM_GOOGLE_CLIENT_SECRET are not set."),
            remediation=tr("An administrator must configure it (see docs/DEPLOY.md)."),
        )
    _purge()
    state = secrets.token_urlsafe(32)
    attempt = Attempt(
        intent=intent,
        nonce=secrets.token_urlsafe(24),
        verifier=secrets.token_urlsafe(48),
        user_id=user_id,
        invitation=invitation,
    )
    _attempts[state] = attempt
    challenge = _b64url(hashlib.sha256(attempt.verifier.encode("ascii")).digest())
    query = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": callback,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": attempt.nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
    )
    return state, f"{AUTHORIZE_URL}?{query}"


def _refused(cause: str) -> AuthenticationError:
    return AuthenticationError(
        tr("Sign-in with Google failed."),
        cause=cause,
        remediation=tr("Start again from the sign-in page."),
        code="GOOGLE_FAILED",
    )


def take_attempt(state: str | None, cookie_state: str | None) -> Attempt:
    """La tentative de **ce** navigateur, consommée : un `state` ne sert qu'une fois."""
    _purge()
    if not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
        raise _refused(tr("The answer does not belong to this browser."))
    attempt = _attempts.pop(state, None)
    if attempt is None:
        raise _refused(tr("The attempt expired or was already used."))
    return attempt


def _claims(id_token: str) -> dict[str, Any]:
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError) as exc:
        raise _refused(tr("Google sent an unreadable identity.")) from exc
    if not isinstance(claims, dict):
        raise _refused(tr("Google sent an unreadable identity."))
    return claims


async def finish(
    settings: Settings,
    attempt: Attempt,
    *,
    code: str,
    callback: str,
    client: httpx.AsyncClient | None = None,
) -> GoogleIdentity:
    """Échange le code contre l'identité Google, après toutes les vérifications."""
    owned = client is None
    http = client or new_client()
    try:
        response = await http.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": callback,
                "grant_type": "authorization_code",
                "code_verifier": attempt.verifier,
            },
        )
    except httpx.HTTPError as exc:
        raise _refused(tr("Google could not be reached.")) from exc
    finally:
        if owned:
            await http.aclose()

    if response.status_code != 200:
        logger.warning("google_token_refused", status=response.status_code)
        raise _refused(tr("Google refused the sign-in code."))
    id_token = response.json().get("id_token")
    if not isinstance(id_token, str):
        raise _refused(tr("Google sent no identity."))

    claims = _claims(id_token)
    if claims.get("iss") not in ISSUERS:
        raise _refused(tr("The identity does not come from Google."))
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if settings.google_client_id not in audiences:
        raise _refused(tr("The identity was issued for another application."))
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < time.time():
        raise _refused(tr("The identity has expired."))
    if not secrets.compare_digest(str(claims.get("nonce", "")), attempt.nonce):
        raise _refused(tr("The identity does not answer this attempt."))
    if not claims.get("email") or claims.get("email_verified") is not True:
        raise _refused(tr("Your Google address is not verified."))
    if not claims.get("sub"):
        raise _refused(tr("Google sent no account identifier."))

    return GoogleIdentity(
        sub=str(claims["sub"]),
        email=str(claims["email"]).strip().lower(),
        name=claims.get("name"),
        picture=claims.get("picture"),
    )


def hold_signup(identity: GoogleIdentity, invitation: str | None) -> str:
    """Met une inscription en attente du choix du pseudo. Renvoie son jeton."""
    _purge()
    token = secrets.token_urlsafe(32)
    _signups[token] = PendingSignup(identity=identity, invitation=invitation)
    return token


def pending_signup(token: str | None) -> PendingSignup:
    _purge()
    pending = _signups.get(token or "")
    if pending is None:
        raise _refused(tr("No sign-up with Google is in progress."))
    return pending


def drop_signup(token: str | None) -> None:
    _signups.pop(token or "", None)
