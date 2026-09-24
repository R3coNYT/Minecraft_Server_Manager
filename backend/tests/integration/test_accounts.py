"""Comptes : inscription, invitations, profil, avatar, langue, bannissement.

Ouverture au public, étape 2 (`docs/PLAN_OUVERTURE_PUBLIC.md`).
"""

from __future__ import annotations

import io
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image

from msm.db.repositories import UserRepository
from msm.db.session import session_scope
from msm.services.account_service import reset_registration_limiter
from msm.services.server_service import ServerService
from tests.conftest import wait_for
from tests.integration.conftest import ADMIN_PASSWORD, FAKE_SERVER, ApiClient, share

pytestmark = pytest.mark.asyncio

PASSWORD = "un-mot-de-passe-solide"


@pytest.fixture(autouse=True)
def _fresh_limiter() -> Iterator[None]:
    reset_registration_limiter()
    yield
    reset_registration_limiter()


@asynccontextmanager
async def anonymous(app: FastAPI) -> AsyncIterator[ApiClient]:
    """Un visiteur sans compte, avec ses propres cookies."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield ApiClient(http)


async def _set_mode(admin: ApiClient, mode: str) -> None:
    response = await admin.put("/api/v1/settings/registration", json={"mode": mode})
    assert response.status_code == 200, response.text


def _signup(username: str = "joueur", email: str = "joueur@example.com", **extra: Any) -> dict:
    return {
        "email": email,
        "username": username,
        "password": PASSWORD,
        "accept_terms": True,
        **extra,
    }


async def _user_id(admin: ApiClient, username: str) -> int:
    users = (await admin.get("/api/v1/users")).json()
    return next(user["id"] for user in users if user["username"] == username)


def _png(width: int = 600, height: int = 300) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


class TestRegistration:
    async def test_registration_is_closed_until_an_admin_opens_it(self, app: FastAPI) -> None:
        async with anonymous(app) as visitor:
            assert (await visitor.get("/api/v1/auth/registration")).json() == {"mode": "closed"}
            refused = await visitor.post("/api/v1/auth/register", json=_signup())
            assert refused.status_code == 403
            assert refused.json()["code"] == "REGISTRATION_CLOSED"

    async def test_an_open_registration_creates_a_user_and_signs_them_in(
        self, app: FastAPI, admin: ApiClient
    ) -> None:
        await _set_mode(admin, "open")
        async with anonymous(app) as visitor:
            created = await visitor.post(
                "/api/v1/auth/register", json=_signup(email="Joueur@Example.com")
            )
            assert created.status_code == 201, created.text
            body = created.json()
            assert body["role"] == "USER"
            assert body["email"] == "joueur@example.com"
            assert body["permissions"] == ["server:create"]

            me = await visitor.get("/api/v1/auth/me")
            assert me.status_code == 200
            assert me.json()["username"] == "joueur"

    async def test_the_rules_must_be_accepted(self, app: FastAPI, admin: ApiClient) -> None:
        await _set_mode(admin, "open")
        async with anonymous(app) as visitor:
            response = await visitor.post("/api/v1/auth/register", json=_signup(accept_terms=False))
        assert response.status_code == 422

    @pytest.mark.parametrize(
        ("username", "status"),
        [("ab", 422), ("un pseudo", 422), ("Admin", 422), ("flavien", 409)],
    )
    async def test_usernames_follow_the_rules(
        self, app: FastAPI, admin: ApiClient, username: str, status: int
    ) -> None:
        await _set_mode(admin, "open")
        async with anonymous(app) as visitor:
            response = await visitor.post("/api/v1/auth/register", json=_signup(username))
        assert response.status_code == status, response.text

    async def test_an_email_serves_a_single_account(self, app: FastAPI, admin: ApiClient) -> None:
        await _set_mode(admin, "open")
        async with anonymous(app) as first, anonymous(app) as second:
            assert (await first.post("/api/v1/auth/register", json=_signup())).status_code == 201
            again = await second.post(
                "/api/v1/auth/register", json=_signup("autre", email="JOUEUR@example.com")
            )
            invalid = await second.post(
                "/api/v1/auth/register", json=_signup("autre", email="pas-une-adresse")
            )
        assert again.status_code == 409
        assert invalid.status_code == 422

    async def test_robots_are_slowed_down(self, app: FastAPI, admin: ApiClient) -> None:
        await _set_mode(admin, "open")
        statuses = []
        for index in range(6):
            async with anonymous(app) as visitor:
                response = await visitor.post(
                    "/api/v1/auth/register",
                    json=_signup(f"robot{index}", email=f"robot{index}@example.com"),
                )
                statuses.append(response.status_code)
        assert statuses == [201] * 5 + [429]


class TestInvitations:
    async def test_an_invitation_opens_a_single_account(
        self, app: FastAPI, admin: ApiClient
    ) -> None:
        await _set_mode(admin, "invite")
        created = await admin.post("/api/v1/users/invitations", json={"note": "Alex", "days": 3})
        assert created.status_code == 201, created.text
        token = created.json()["token"]

        async with anonymous(app) as visitor:
            without = await visitor.post("/api/v1/auth/register", json=_signup())
            assert without.status_code == 403
            used = await visitor.post("/api/v1/auth/register", json=_signup(invitation=token))
            assert used.status_code == 201, used.text
        async with anonymous(app) as other:
            reused = await other.post(
                "/api/v1/auth/register",
                json=_signup("autre", email="autre@example.com", invitation=token),
            )
            assert reused.status_code == 403
            assert reused.json()["code"] == "INVITATION_INVALID"

        listed = (await admin.get("/api/v1/users/invitations")).json()
        assert listed[0]["used_at"] is not None
        assert "token" not in listed[0]

    async def test_only_admins_invite(self, moderator: ApiClient) -> None:
        assert (await moderator.post("/api/v1/users/invitations", json={})).status_code == 403


class TestProfile:
    async def test_a_rename_keeps_the_old_name_for_its_holder(
        self, app: FastAPI, admin: ApiClient, viewer: ApiClient
    ) -> None:
        renamed = await viewer.put("/api/v1/auth/me", json={"username": "nouveau"})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["username"] == "nouveau"

        user_id = await _user_id(admin, "nouveau")
        details = (await admin.get(f"/api/v1/users/{user_id}")).json()
        assert [item["username"] for item in details["username_history"]] == ["lecteur"]

        # Personne d'autre ne prend l'ancien pseudo…
        taken = await admin.put("/api/v1/auth/me", json={"username": "lecteur"})
        assert taken.status_code == 409
        # …mais son titulaire peut y revenir.
        back = await viewer.put("/api/v1/auth/me", json={"username": "lecteur"})
        assert back.status_code == 200, back.text

    async def test_each_account_chooses_its_language(self, viewer: ApiClient) -> None:
        english = await viewer.get("/api/v1/servers/9999")
        assert english.json()["message"] == "Server not found."

        assert (await viewer.put("/api/v1/auth/me", json={"language": "fr"})).json()[
            "language"
        ] == "fr"
        french = await viewer.get("/api/v1/servers/9999")
        assert french.json()["message"] == "Serveur introuvable."

        await viewer.put("/api/v1/auth/me", json={"language": None})
        assert (await viewer.get("/api/v1/servers/9999")).json()["message"] == "Server not found."

    async def test_changing_the_email_needs_the_password(self, viewer: ApiClient) -> None:
        wrong = await viewer.put(
            "/api/v1/auth/me/email", json={"email": "moi@example.com", "current_password": "x"}
        )
        assert wrong.status_code == 422
        right = await viewer.put(
            "/api/v1/auth/me/email",
            json={"email": "Moi@Example.com", "current_password": ADMIN_PASSWORD},
        )
        assert right.status_code == 200, right.text
        assert right.json()["email"] == "moi@example.com"


class TestAvatar:
    async def test_an_avatar_is_reencoded_as_a_square(
        self, admin: ApiClient, viewer: ApiClient
    ) -> None:
        uploaded = await viewer.put(
            "/api/v1/auth/me/avatar", files={"file": ("moi.png", _png(), "image/png")}
        )
        assert uploaded.status_code == 200, uploaded.text
        url = uploaded.json()["avatar_url"]
        assert url

        # Visible des autres comptes, sous forme de WebP carré.
        served = await admin.get(url)
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/webp"
        with Image.open(io.BytesIO(served.content)) as image:
            assert image.format == "WEBP"
            assert image.size == (256, 256)

    async def test_anything_but_an_image_is_refused(self, viewer: ApiClient) -> None:
        response = await viewer.put(
            "/api/v1/auth/me/avatar",
            files={"file": ("moi.png", b"<script>alert(1)</script>", "image/png")},
        )
        assert response.status_code == 422

    async def test_removing_the_avatar(self, viewer: ApiClient) -> None:
        await viewer.put("/api/v1/auth/me/avatar", files={"file": ("a.png", _png(), "image/png")})
        me = (await viewer.get("/api/v1/auth/me")).json()

        removed = await viewer.delete("/api/v1/auth/me/avatar")
        assert removed.json()["avatar_url"] is None
        assert (await viewer.get(me["avatar_url"])).status_code == 404


async def _server_of(app: FastAPI, username: str, directory: Path) -> int:
    async with session_scope() as session:
        user = await UserRepository(session).get_by_username(username)
        assert user is not None
        server = await ServerService(
            session, app.state.settings, app.state.supervisor
        ).create_server(
            name="sien",
            directory=str(directory),
            launcher_key="custom",
            settings_overrides={
                "custom_argv": [sys.executable, str(FAKE_SERVER)],
                "stop_timeout_s": 5,
                "kill_timeout_s": 3,
            },
            actor=user,
        )
        return server.id


class TestBans:
    async def test_a_banned_account_is_signed_out_and_told_why(
        self, app: FastAPI, moderator: ApiClient, viewer: ApiClient, admin: ApiClient
    ) -> None:
        user_id = await _user_id(admin, "lecteur")
        banned = await moderator.post(
            f"/api/v1/users/{user_id}/ban", json={"reason": "Triche sur le serveur"}
        )
        assert banned.status_code == 200, banned.text
        assert banned.json()["ban_reason"] == "Triche sur le serveur"

        assert (await viewer.get("/api/v1/auth/me")).status_code == 401
        async with anonymous(app) as visitor:
            login = await visitor.login("lecteur", ADMIN_PASSWORD)
            assert login.status_code == 401
            assert login.json()["code"] == "ACCOUNT_BANNED"
            assert login.json()["cause"] == "Triche sur le serveur"

            assert (await moderator.post(f"/api/v1/users/{user_id}/unban")).status_code == 200
            assert (await visitor.login("lecteur", ADMIN_PASSWORD)).status_code == 200

    async def test_a_banned_owners_servers_stop_and_stay_stopped(
        self,
        app: FastAPI,
        admin: ApiClient,
        viewer: ApiClient,
        moderator: ApiClient,
        tmp_path: Path,
    ) -> None:
        directory = tmp_path / "sien"
        directory.mkdir()
        server_id = await _server_of(app, "lecteur", directory)
        await share(viewer, server_id, "moderateur", "ADMIN")
        assert (await viewer.post(f"/api/v1/servers/{server_id}/start")).status_code == 200
        runtime = app.state.supervisor.get(server_id)
        assert await wait_for(lambda: runtime.state.is_running, timeout=10.0)

        user_id = await _user_id(admin, "lecteur")
        await admin.post(f"/api/v1/users/{user_id}/ban", json={"reason": "Abus"})

        assert await wait_for(lambda: not runtime.state.is_running, timeout=20.0)
        refused = await moderator.post(f"/api/v1/servers/{server_id}/start")
        assert refused.status_code == 403
        assert refused.json()["code"] == "OWNER_BANNED"

    async def test_moderators_only_ban_users(self, admin: ApiClient, moderator: ApiClient) -> None:
        admin_id = await _user_id(admin, "flavien")
        moderator_id = await _user_id(admin, "moderateur")

        assert (
            await moderator.post(f"/api/v1/users/{admin_id}/ban", json={"reason": "x"})
        ).status_code == 403
        assert (
            await admin.post(f"/api/v1/users/{admin_id}/ban", json={"reason": "x"})
        ).status_code == 422
        assert (
            await admin.post(f"/api/v1/users/{moderator_id}/ban", json={"reason": "x"})
        ).status_code == 200

    async def test_a_banned_account_cannot_be_invited_to_a_server(
        self, app: FastAPI, admin: ApiClient, tmp_path: Path
    ) -> None:
        response = await admin.post(
            "/api/v1/servers",
            json={
                "name": "mien",
                "directory": str(tmp_path),
                "launcher_key": "custom",
                "settings": {"custom_argv": [sys.executable, str(FAKE_SERVER)]},
            },
        )
        server_id = response.json()["id"]
        user_id = await _user_id(admin, "lecteur")
        await admin.post(f"/api/v1/users/{user_id}/ban", json={"reason": "Abus"})

        shared = await admin.put(
            f"/api/v1/servers/{server_id}/members", json={"username": "lecteur", "role": "VIEWER"}
        )
        assert shared.status_code == 404


class TestAccountDetails:
    async def test_the_team_sees_owned_and_shared_servers(
        self, app: FastAPI, admin: ApiClient, moderator: ApiClient, tmp_path: Path
    ) -> None:
        directory = tmp_path / "sien"
        directory.mkdir()
        await _server_of(app, "lecteur", directory)
        (tmp_path / "autre").mkdir()
        response = await admin.post(
            "/api/v1/servers",
            json={
                "name": "partage",
                "directory": str(tmp_path / "autre"),
                "launcher_key": "custom",
                "settings": {"custom_argv": [sys.executable, str(FAKE_SERVER)]},
            },
        )
        assert response.status_code == 201, response.text
        await share(admin, response.json()["id"], "lecteur", "ADMIN")

        user_id = await _user_id(admin, "lecteur")
        details = (await moderator.get(f"/api/v1/users/{user_id}")).json()
        assert [server["name"] for server in details["servers_owned"]] == ["sien"]
        assert [(s["name"], s["role"]) for s in details["servers_shared"]] == [("partage", "ADMIN")]
