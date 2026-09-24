"""Connexion avec Google, bout à bout, contre un faux Google.

Le faux Google répond à l'échange du code par un ID token dont chaque test règle
les revendications : c'est là que se jouent les contrôles (émetteur, audience,
expiration, nonce, adresse vérifiée).
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from msm.config import Settings
from msm.services import google_service
from msm.services.account_service import reset_registration_limiter
from tests.integration.conftest import ADMIN_PASSWORD, ApiClient

pytestmark = pytest.mark.asyncio

CLIENT_ID = "client-123.apps.googleusercontent.com"


@pytest.fixture
def api_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        secret_key="cle-de-test-suffisamment-longue-pour-la-validation-0123456789",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        cors_origins=[],
        public_url="http://test",
        google_client_id=CLIENT_ID,
        google_client_secret="secret",
    )


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    google_service.reset()
    reset_registration_limiter()
    yield
    google_service.reset()
    reset_registration_limiter()


def _token(claims: dict[str, Any]) -> str:
    def part(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


@pytest.fixture
def fake_google(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Les revendications du prochain ID token ; `nonce` est repris de la tentative."""
    state: dict[str, Any] = {"claims": {}, "nonce": None, "status": 200}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            form = parse_qs(request.content.decode())
            assert form["client_secret"] == ["secret"]
            assert form["code_verifier"][0]
            claims = {
                "iss": "https://accounts.google.com",
                "aud": CLIENT_ID,
                "exp": time.time() + 300,
                "nonce": state["nonce"],
                "sub": "google-sub-1",
                "email": "Alex@Gmail.com",
                "email_verified": True,
                "name": "Alex Martin",
                **state["claims"],
            }
            return httpx.Response(state["status"], json={"id_token": _token(claims)})
        return httpx.Response(404)

    monkeypatch.setattr(
        google_service,
        "new_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return state


@asynccontextmanager
async def browser(app: FastAPI) -> AsyncIterator[ApiClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield ApiClient(http)


async def _through_google(
    client: ApiClient, fake: dict[str, Any], *, params: dict[str, str] | None = None
) -> str:
    """Démarre, « passe chez Google », revient. Renvoie la page où MSM redirige."""
    start = await client.get("/api/v1/auth/google/start", params=params or {})
    assert start.status_code == 302, start.text
    query = parse_qs(urlparse(start.headers["location"]).query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["http://test/api/v1/auth/google/callback"]
    fake["nonce"] = query["nonce"][0]

    back = await client.get(
        "/api/v1/auth/google/callback", params={"code": "code-xyz", "state": query["state"][0]}
    )
    assert back.status_code == 302, back.text
    return back.headers["location"]


async def _open(admin: ApiClient, mode: str = "open") -> None:
    assert (
        await admin.put("/api/v1/settings/registration", json={"mode": mode})
    ).status_code == 200


class TestSignUp:
    async def test_the_button_is_offered_once_configured(self, app: FastAPI) -> None:
        async with browser(app) as visitor:
            info = (await visitor.get("/api/v1/auth/registration")).json()
        assert info["google"] is True

    async def test_a_first_sign_in_asks_for_a_username_then_creates_the_account(
        self, app: FastAPI, admin: ApiClient, fake_google: dict[str, Any]
    ) -> None:
        await _open(admin)
        async with browser(app) as visitor:
            assert await _through_google(visitor, fake_google) == "/register/google"

            pending = (await visitor.get("/api/v1/auth/google/signup")).json()
            assert pending["email"] == "alex@gmail.com"
            assert pending["suggested_username"] == "Alex_Martin"

            created = await visitor.post(
                "/api/v1/auth/google/signup", json={"username": "Alex", "accept_terms": True}
            )
            assert created.status_code == 200, created.text
            me = created.json()
            assert me["username"] == "Alex"
            assert me["role"] == "USER"
            assert me["google_linked"] is True
            assert me["has_password"] is False
            assert (await visitor.get("/api/v1/auth/me")).status_code == 200

        # La fois suivante, Google suffit : plus de pseudo à choisir.
        async with browser(app) as again:
            assert await _through_google(again, fake_google) == "/"
            assert (await again.get("/api/v1/auth/me")).json()["username"] == "Alex"

        # Et sans mot de passe, le formulaire classique ne l'ouvre pas.
        async with browser(app) as other:
            assert (await other.login("Alex", "!")).status_code == 401

    async def test_closed_registration_turns_newcomers_away(
        self, app: FastAPI, fake_google: dict[str, Any]
    ) -> None:
        async with browser(app) as visitor:
            assert await _through_google(visitor, fake_google) == "/login?google=closed"

    async def test_an_invitation_goes_through_google(
        self, app: FastAPI, admin: ApiClient, fake_google: dict[str, Any]
    ) -> None:
        await _open(admin, "invite")
        token = (await admin.post("/api/v1/users/invitations", json={})).json()["token"]
        async with browser(app) as stranger:
            assert await _through_google(stranger, fake_google) == "/login?google=invite"
        async with browser(app) as invited:
            landing = await _through_google(invited, fake_google, params={"invite": token})
            assert landing == "/register/google"
            created = await invited.post(
                "/api/v1/auth/google/signup", json={"username": "Invite", "accept_terms": True}
            )
            assert created.status_code == 200, created.text

    async def test_an_address_already_used_is_not_taken_over(
        self, app: FastAPI, admin: ApiClient, fake_google: dict[str, Any]
    ) -> None:
        """L'adresse seule ne prouve rien : pas de liaison automatique."""
        await _open(admin)
        users = (await admin.get("/api/v1/users")).json()
        lecteur = next(user for user in users if user["username"] == "lecteur")
        await admin.put(f"/api/v1/users/{lecteur['id']}", json={"email": "alex@gmail.com"})
        async with browser(app) as visitor:
            assert await _through_google(visitor, fake_google) == "/login?google=email_taken"


class TestChecks:
    @pytest.mark.parametrize(
        "claims",
        [
            {"aud": "another-app"},
            {"iss": "https://evil.example"},
            {"exp": 1},
            {"nonce": "rejoue"},
            {"email_verified": False},
        ],
    )
    async def test_a_doubtful_identity_is_refused(
        self, app: FastAPI, admin: ApiClient, fake_google: dict[str, Any], claims: dict[str, Any]
    ) -> None:
        await _open(admin)
        fake_google["claims"] = claims
        async with browser(app) as visitor:
            assert await _through_google(visitor, fake_google) == "/login?google=failed"

    async def test_an_answer_for_another_browser_is_refused(
        self, app: FastAPI, admin: ApiClient, fake_google: dict[str, Any]
    ) -> None:
        """Le `state` doit revenir dans le navigateur qui a commencé."""
        await _open(admin)
        async with browser(app) as victim, browser(app) as attacker:
            start = await attacker.get("/api/v1/auth/google/start")
            state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
            back = await victim.get(
                "/api/v1/auth/google/callback", params={"code": "code", "state": state}
            )
        assert back.headers["location"] == "/login?google=failed"

    async def test_a_banned_account_stays_out(
        self, app: FastAPI, admin: ApiClient, fake_google: dict[str, Any]
    ) -> None:
        await _open(admin)
        async with browser(app) as visitor:
            await _through_google(visitor, fake_google)
            me = (
                await visitor.post(
                    "/api/v1/auth/google/signup", json={"username": "Alex", "accept_terms": True}
                )
            ).json()
        await admin.post(f"/api/v1/users/{me['id']}/ban", json={"reason": "Abus"})
        async with browser(app) as again:
            assert await _through_google(again, fake_google) == "/login?google=banned"


class TestLinking:
    async def test_link_then_unlink_from_the_profile(
        self, viewer: ApiClient, fake_google: dict[str, Any]
    ) -> None:
        landing = await _through_google(viewer, fake_google, params={"intent": "link"})
        assert landing == "/profile?google=linked"
        assert (await viewer.get("/api/v1/auth/me")).json()["google_linked"] is True

        unlinked = await viewer.post("/api/v1/auth/google/unlink")
        assert unlinked.status_code == 200, unlinked.text
        assert unlinked.json()["google_linked"] is False

    async def test_a_google_only_account_sets_a_password_before_unlinking(
        self, app: FastAPI, admin: ApiClient, fake_google: dict[str, Any]
    ) -> None:
        await _open(admin)
        async with browser(app) as visitor:
            await _through_google(visitor, fake_google)
            await visitor.post(
                "/api/v1/auth/google/signup", json={"username": "Alex", "accept_terms": True}
            )
            refused = await visitor.post("/api/v1/auth/google/unlink")
            assert refused.status_code == 422

            # Son adresse se change sans mot de passe à confirmer.
            moved = await visitor.put("/api/v1/auth/me/email", json={"email": "neuf@example.com"})
            assert moved.status_code == 200, moved.text
            assert moved.json()["email"] == "neuf@example.com"

            # Pas d'ancien mot de passe à fournir : il n'y en a pas.
            changed = await visitor.post(
                "/api/v1/auth/password", json={"new_password": ADMIN_PASSWORD}
            )
            assert changed.status_code == 200, changed.text
        async with browser(app) as back:
            assert (await back.login("Alex", ADMIN_PASSWORD)).status_code == 200
            assert (await back.post("/api/v1/auth/google/unlink")).status_code == 200

    async def test_a_google_account_links_to_a_single_msm_account(
        self, app: FastAPI, admin: ApiClient, viewer: ApiClient, fake_google: dict[str, Any]
    ) -> None:
        assert await _through_google(viewer, fake_google, params={"intent": "link"}) == (
            "/profile?google=linked"
        )
        assert await _through_google(admin, fake_google, params={"intent": "link"}) == (
            "/profile?google=taken"
        )
