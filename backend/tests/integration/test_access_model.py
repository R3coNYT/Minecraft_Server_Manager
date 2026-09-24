"""Le modèle d'accès de l'ouverture au public : rôles, propriétaires, membres.

Chaque test pose un cas de la matrice « qui peut faire quoi » du plan
(`docs/PLAN_OUVERTURE_PUBLIC.md`) et vérifie qu'il tient **côté API** : masquer
un bouton ne protège rien, c'est le serveur qui refuse.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from msm.db.repositories import UserRepository
from msm.db.session import session_scope
from msm.services.server_service import ServerService
from tests.integration.conftest import FAKE_SERVER, ApiClient, fake_server_payload, share

pytestmark = pytest.mark.asyncio


async def _admin_server(admin: ApiClient, directory: Path, name: str = "admin-srv") -> dict:
    response = await admin.post("/api/v1/servers", json=fake_server_payload(name, directory))
    assert response.status_code == 201, response.text
    return response.json()


async def _owned_by(app: FastAPI, username: str, directory: Path, name: str) -> int:
    """Crée un serveur **appartenant à** `username`, comme le ferait sa création.

    La route d'enregistrement d'un dossier existant est réservée aux admins ; un
    user crée ses serveurs par l'assistant, dont le téléchargement n'a pas sa place
    ici. On passe donc par le service, avec le compte comme auteur.
    """
    async with session_scope() as session:
        user = await UserRepository(session).get_by_username(username)
        assert user is not None
        server = await ServerService(
            session, app.state.settings, app.state.supervisor
        ).create_server(
            name=name,
            directory=str(directory),
            launcher_key="custom",
            settings_overrides={
                "custom_argv": [sys.executable, str(FAKE_SERVER)],
                "stop_timeout_s": 5,
                "kill_timeout_s": 3,
                "start_timeout_s": 30,
            },
            actor=user,
        )
        return server.id


def _names(response: Any) -> list[str]:
    assert response.status_code == 200, response.text
    return [server["name"] for server in response.json()]


@pytest.fixture
def dirs(tmp_path: Path) -> Any:
    counter = iter(range(1000))

    def make() -> Path:
        directory = tmp_path / f"srv-{next(counter)}"
        directory.mkdir()
        return directory

    return make


class TestVisibility:
    async def test_a_user_does_not_see_other_peoples_servers(
        self, admin: ApiClient, viewer: ApiClient, dirs: Any
    ) -> None:
        server = await _admin_server(admin, dirs())

        assert _names(await viewer.get("/api/v1/servers")) == []
        dashboard = (await viewer.get("/api/v1/servers/dashboard")).json()
        assert dashboard["servers"] == []
        # Invisible = introuvable : son existence ne fuite pas.
        assert (await viewer.get(f"/api/v1/servers/{server['id']}")).status_code == 404
        assert (await viewer.post(f"/api/v1/servers/{server['id']}/start")).status_code == 404

    async def test_the_machine_stays_hidden_from_users_and_moderators(
        self, admin: ApiClient, viewer: ApiClient, moderator: ApiClient
    ) -> None:
        assert (await admin.get("/api/v1/servers/dashboard")).json()["system"] is not None
        assert (await viewer.get("/api/v1/servers/dashboard")).json()["system"] is None
        assert (await moderator.get("/api/v1/servers/dashboard")).json()["system"] is None

    async def test_staff_sees_everything_with_their_own_servers_first(
        self, app: FastAPI, admin: ApiClient, moderator: ApiClient, dirs: Any
    ) -> None:
        await _owned_by(app, "lecteur", dirs(), "b-lecteur")
        await _owned_by(app, "moderateur", dirs(), "c-modo")
        await _admin_server(admin, dirs(), "z-admin")

        assert _names(await admin.get("/api/v1/servers")) == ["z-admin", "b-lecteur", "c-modo"]
        assert _names(await moderator.get("/api/v1/servers")) == [
            "c-modo",
            "z-admin",
            "b-lecteur",
        ]

    async def test_names_are_unique_per_owner_only(
        self, app: FastAPI, admin: ApiClient, dirs: Any
    ) -> None:
        await _admin_server(admin, dirs(), "Survie")
        await _owned_by(app, "lecteur", dirs(), "Survie")  # ne lève pas

        duplicate = await admin.post("/api/v1/servers", json=fake_server_payload("survie", dirs()))
        assert duplicate.status_code == 409


class TestSharing:
    async def test_a_viewer_member_only_sees_the_overview(
        self, admin: ApiClient, viewer: ApiClient, dirs: Any
    ) -> None:
        server = await _admin_server(admin, dirs())
        await share(admin, server["id"], "lecteur", "VIEWER")

        listed = (await viewer.get("/api/v1/servers")).json()
        assert [item["shared"] for item in listed] == [True]
        assert listed[0]["access"] == "VIEWER"
        assert listed[0]["owner_username"] == "flavien"
        assert listed[0]["permissions"] == ["server:view"]

        server_id = server["id"]
        assert (await viewer.get(f"/api/v1/servers/{server_id}")).status_code == 200
        assert (await viewer.get(f"/api/v1/servers/{server_id}/logs")).status_code == 403
        assert (await viewer.post(f"/api/v1/servers/{server_id}/start")).status_code == 403
        assert (await viewer.get(f"/api/v1/servers/{server_id}/schedules")).status_code == 403

    async def test_a_server_admin_runs_it_but_neither_deletes_nor_shares_it(
        self, admin: ApiClient, viewer: ApiClient, dirs: Any
    ) -> None:
        server = await _admin_server(admin, dirs())
        await share(admin, server["id"], "lecteur", "ADMIN")
        server_id = server["id"]

        start = await viewer.post(f"/api/v1/servers/{server_id}/start")
        assert start.status_code == 200, start.text
        stop = await viewer.post(f"/api/v1/servers/{server_id}/stop")
        assert stop.status_code == 200, stop.text

        assert (await viewer.get(f"/api/v1/servers/{server_id}/members")).status_code == 403
        assert (await viewer.delete(f"/api/v1/servers/{server_id}")).status_code == 403

    async def test_unsharing_removes_access(
        self, admin: ApiClient, viewer: ApiClient, dirs: Any
    ) -> None:
        server = await _admin_server(admin, dirs())
        await share(admin, server["id"], "lecteur", "VIEWER")
        members = (await admin.get(f"/api/v1/servers/{server['id']}/members")).json()
        assert [(member["username"], member["role"]) for member in members] == [
            ("lecteur", "VIEWER")
        ]

        removed = await admin.delete(
            f"/api/v1/servers/{server['id']}/members/{members[0]['user_id']}"
        )
        assert removed.status_code == 200, removed.text
        assert (await viewer.get(f"/api/v1/servers/{server['id']}")).status_code == 404

    async def test_changing_a_members_role_replaces_it(
        self, admin: ApiClient, viewer: ApiClient, dirs: Any
    ) -> None:
        server = await _admin_server(admin, dirs())
        await share(admin, server["id"], "lecteur", "VIEWER")
        await share(admin, server["id"], "lecteur", "ADMIN")

        members = (await admin.get(f"/api/v1/servers/{server['id']}/members")).json()
        assert [member["role"] for member in members] == ["ADMIN"]

    async def test_sharing_with_an_unknown_account_or_the_owner_is_refused(
        self, admin: ApiClient, dirs: Any
    ) -> None:
        server = await _admin_server(admin, dirs())
        url = f"/api/v1/servers/{server['id']}/members"

        unknown = await admin.put(url, json={"username": "personne", "role": "VIEWER"})
        assert unknown.status_code == 404
        owner = await admin.put(url, json={"username": "flavien", "role": "ADMIN"})
        assert owner.status_code == 422
        owner_role = await admin.put(url, json={"username": "lecteur", "role": "OWNER"})
        assert owner_role.status_code == 422


class TestOwnerRights:
    async def test_the_owner_edits_but_not_what_runs_on_the_machine(
        self, app: FastAPI, viewer: ApiClient, dirs: Any
    ) -> None:
        server_id = await _owned_by(app, "lecteur", dirs(), "mien")
        url = f"/api/v1/servers/{server_id}"

        memory = await viewer.put(url, json={"settings": {"memory_max_mb": 2048}})
        assert memory.status_code == 200, memory.text

        for forbidden in (
            {"settings": {"java_path": "/tmp/java"}},
            {"settings": {"jvm_args": ["-XX:+Evil"]}},
            {"launcher_key": "jar"},
            {"directory": str(dirs())},
            {"settings": {"autostart_on_boot": True}},
        ):
            refused = await viewer.put(url, json=forbidden)
            assert refused.status_code == 403, (forbidden, refused.text)

    async def test_a_full_form_with_unchanged_launch_settings_is_accepted(
        self, app: FastAPI, viewer: ApiClient, dirs: Any
    ) -> None:
        """Renvoyer les réglages de lancement à l'identique n'exige aucun droit."""
        server_id = await _owned_by(app, "lecteur", dirs(), "mien")
        current = (await viewer.get(f"/api/v1/servers/{server_id}")).json()

        response = await viewer.put(
            f"/api/v1/servers/{server_id}",
            json={
                "launcher_key": current["launcher_key"],
                "settings": {
                    "custom_argv": current["settings"]["custom_argv"],
                    "jvm_args": current["settings"]["jvm_args"],
                    "memory_max_mb": 3072,
                },
            },
        )
        assert response.status_code == 200, response.text

    async def test_the_owner_deletes_their_server(
        self, app: FastAPI, viewer: ApiClient, dirs: Any
    ) -> None:
        server_id = await _owned_by(app, "lecteur", dirs(), "mien")
        assert (await viewer.delete(f"/api/v1/servers/{server_id}")).status_code == 200

    async def test_users_cannot_register_existing_folders(
        self, viewer: ApiClient, dirs: Any
    ) -> None:
        directory = dirs()
        created = await viewer.post("/api/v1/servers", json=fake_server_payload("x", directory))
        assert created.status_code == 403
        detected = await viewer.post("/api/v1/servers/detect", json={"directory": str(directory)})
        assert detected.status_code == 403


class TestStaffOnOtherPeoplesServers:
    async def test_an_msm_admin_oversees_without_editing(
        self, app: FastAPI, admin: ApiClient, dirs: Any
    ) -> None:
        server_id = await _owned_by(app, "lecteur", dirs(), "sien")
        url = f"/api/v1/servers/{server_id}"

        seen = (await admin.get(url)).json()
        assert seen["access"] is None
        assert seen["shared"] is False
        assert {"server:stop", "server:kill", "server:delete", "server:autostart"} <= set(
            seen["permissions"]
        )

        assert (await admin.put(url, json={"settings": {"memory_max_mb": 2048}})).status_code == 403
        assert (await admin.get(f"{url}/logs")).status_code == 403
        assert (await admin.post(f"{url}/start")).status_code == 403

        autostart = await admin.put(url, json={"settings": {"autostart_on_boot": True}})
        assert autostart.status_code == 200, autostart.text
        assert autostart.json()["settings"]["autostart_on_boot"] is True

        assert (await admin.delete(url)).status_code == 200

    async def test_a_moderator_can_stop_any_server_but_not_run_it(
        self, app: FastAPI, moderator: ApiClient, viewer: ApiClient, dirs: Any
    ) -> None:
        server_id = await _owned_by(app, "lecteur", dirs(), "sien")
        url = f"/api/v1/servers/{server_id}"
        start = await viewer.post(f"{url}/start")
        assert start.status_code == 200, start.text

        assert (await moderator.post(f"{url}/restart")).status_code == 403
        assert (await moderator.get(f"{url}/logs")).status_code == 403
        assert (await moderator.delete(url)).status_code == 403
        stop = await moderator.post(f"{url}/stop")
        assert stop.status_code == 200, stop.text


class TestAccounts:
    async def test_moderators_read_accounts_admins_manage_them(
        self, admin: ApiClient, moderator: ApiClient, viewer: ApiClient
    ) -> None:
        listing = await moderator.get("/api/v1/users")
        assert listing.status_code == 200
        # Les admins d'abord, puis les modérateurs, puis les users.
        assert [user["role"] for user in listing.json()] == ["ADMIN", "MODERATOR", "USER"]
        assert all(len(user["storage_id"]) == 10 for user in listing.json())

        refused = await moderator.post(
            "/api/v1/users", json={"username": "x", "password": "p" * 12, "role": "USER"}
        )
        assert refused.status_code == 403
        assert (await viewer.get("/api/v1/users")).status_code == 403
        assert (await viewer.get("/api/v1/audit")).status_code == 403
        assert (await moderator.get("/api/v1/audit")).status_code == 200

    async def test_an_account_that_owns_servers_cannot_be_deleted(
        self, app: FastAPI, admin: ApiClient, dirs: Any
    ) -> None:
        await _owned_by(app, "lecteur", dirs(), "sien")
        users = (await admin.get("/api/v1/users")).json()
        lecteur = next(user for user in users if user["username"] == "lecteur")

        response = await admin.delete(f"/api/v1/users/{lecteur['id']}")
        assert response.status_code == 409
        assert "sien" in response.json()["cause"]


class TestMachine:
    async def test_system_stats(self, admin: ApiClient) -> None:
        response = await admin.get("/api/v1/system/stats")

        assert response.status_code == 200
        payload = response.json()
        assert payload["memory_total_mb"] > 0
        assert payload["cpu_count"] >= 1

    async def test_the_machine_is_hidden_from_anonymous_visitors_and_users(
        self, client: AsyncClient, viewer: ApiClient, moderator: ApiClient
    ) -> None:
        """Les ressources de la machine ne regardent que ses administrateurs."""
        assert (await client.get("/api/v1/system/stats")).status_code == 401
        assert (await viewer.get("/api/v1/system/stats")).status_code == 403
        assert (await moderator.get("/api/v1/system/stats")).status_code == 403
        assert (await client.get("/api/v1/system/launchers")).status_code == 401

    async def test_launchers_are_listed_with_availability(self, admin: ApiClient) -> None:
        response = await admin.get("/api/v1/system/launchers")

        assert response.status_code == 200
        keys = {item["key"] for item in response.json()}
        assert {"jar", "shell", "batch", "custom"} <= keys
