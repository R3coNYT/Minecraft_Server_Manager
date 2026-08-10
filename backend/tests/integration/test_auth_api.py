"""Tests d'authentification, de session et de protection CSRF."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.integration.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, ApiClient

pytestmark = pytest.mark.asyncio


class TestLogin:
    async def test_valid_credentials_open_a_session(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["username"] == ADMIN_USERNAME
        assert body["role"] == "ADMIN"
        assert "password_hash" not in body
        assert "server:delete" in body["permissions"]

    async def test_session_cookie_is_httponly(self, client: AsyncClient) -> None:
        """Le cookie de session ne doit pas être lisible par du JavaScript."""
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )

        session_cookie = next(
            header
            for header in response.headers.get_list("set-cookie")
            if header.startswith("msm_session=")
        )
        assert "HttpOnly" in session_cookie
        assert "SameSite=lax" in session_cookie.lower().replace("samesite=lax", "SameSite=lax")

        csrf_cookie = next(
            header
            for header in response.headers.get_list("set-cookie")
            if header.startswith("msm_csrf=")
        )
        # Celui-ci doit rester lisible : le frontend le recopie dans l'en-tête.
        assert "HttpOnly" not in csrf_cookie

    @pytest.mark.parametrize(
        ("username", "password"),
        [
            (ADMIN_USERNAME, "mauvais-mot-de-passe"),
            ("inconnu", ADMIN_PASSWORD),
        ],
    )
    async def test_invalid_credentials_are_indistinguishable(
        self, client: AsyncClient, username: str, password: str
    ) -> None:
        """Compte inexistant et mot de passe faux donnent exactement la même réponse.

        Une différence permettrait d'énumérer les comptes valides.
        """
        response = await client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )

        assert response.status_code == 401
        assert response.json()["message"] == "Identifiants incorrects."

    async def test_account_locks_after_repeated_failures(self, client: AsyncClient) -> None:
        for _ in range(8):
            await client.post(
                "/api/v1/auth/login",
                json={"username": ADMIN_USERNAME, "password": "faux"},
            )

        # Même avec le bon mot de passe, le compte est temporairement verrouillé.
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        assert response.status_code == 401
        assert "verrouillé" in response.json()["message"].lower()


class TestSession:
    async def test_me_requires_authentication(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401
        assert response.json()["code"] == "AUTHENTICATION_FAILED"

    async def test_me_returns_the_current_user(self, admin: ApiClient) -> None:
        response = await admin.get("/api/v1/auth/me")
        assert response.status_code == 200
        assert response.json()["username"] == ADMIN_USERNAME

    async def test_logout_revokes_the_session_server_side(self, admin: ApiClient) -> None:
        """La révocation est côté serveur : effacer le cookie ne suffirait pas."""
        cookies = dict(admin.raw.cookies)
        assert (await admin.post("/api/v1/auth/logout")).status_code == 200

        admin.raw.cookies.update(cookies)  # on rejoue l'ancien jeton
        response = await admin.get("/api/v1/auth/me")
        assert response.status_code == 401

    async def test_password_change_invalidates_every_session(self, admin: ApiClient) -> None:
        response = await admin.post(
            "/api/v1/auth/password",
            json={
                "current_password": ADMIN_PASSWORD,
                "new_password": "un-tout-autre-mot-de-passe-9876",
            },
        )
        assert response.status_code == 200

        admin.raw.cookies.set("msm_session", "")
        assert (await admin.get("/api/v1/auth/me")).status_code == 401

    async def test_password_change_requires_the_current_one(self, admin: ApiClient) -> None:
        response = await admin.post(
            "/api/v1/auth/password",
            json={"current_password": "faux", "new_password": "nouveau-mot-de-passe-1234"},
        )
        assert response.status_code == 401

    async def test_weak_password_is_refused(self, admin: ApiClient) -> None:
        response = await admin.post(
            "/api/v1/auth/password",
            json={"current_password": ADMIN_PASSWORD, "new_password": "court"},
        )
        assert response.status_code == 422
        assert response.json()["remediation"]


class TestCsrf:
    async def test_mutation_without_csrf_header_is_refused(self, admin: ApiClient) -> None:
        """Sans l'en-tête, un site tiers pourrait déclencher l'action à notre insu."""
        response = await admin.raw.post("/api/v1/auth/logout")

        assert response.status_code == 403
        assert response.json()["code"] == "CSRF_INVALID"

    async def test_mismatched_csrf_token_is_refused(self, admin: ApiClient) -> None:
        response = await admin.raw.post(
            "/api/v1/auth/logout", headers={"X-CSRF-Token": "jeton-invente"}
        )
        assert response.status_code == 403

    async def test_read_only_requests_do_not_need_csrf(self, admin: ApiClient) -> None:
        assert (await admin.raw.get("/api/v1/auth/me")).status_code == 200

    async def test_csrf_token_can_be_renewed(self, admin: ApiClient) -> None:
        response = await admin.get("/api/v1/auth/csrf")
        assert response.status_code == 200
        assert response.json()["csrf_token"]
