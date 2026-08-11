"""Tests du CRUD des serveurs, de la détection et des permissions par rôle."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.integration.conftest import ApiClient, fake_server_payload

pytestmark = pytest.mark.asyncio


async def _create(admin: ApiClient, directory: Path, name: str = "survie") -> dict:
    response = await admin.post("/api/v1/servers", json=fake_server_payload(name, directory))
    assert response.status_code == 201, response.text
    return response.json()


class TestCreation:
    async def test_create_and_list(self, admin: ApiClient, fake_server_dir: Path) -> None:
        created = await _create(admin, fake_server_dir)

        assert created["name"] == "survie"
        assert created["slug"] == "survie"
        assert created["status"]["state"] == "OFFLINE"
        assert "console" in created["capabilities"]

        listing = await admin.get("/api/v1/servers")
        assert listing.status_code == 200
        assert len(listing.json()) == 1

    async def test_slug_is_derived_and_unique(
        self, admin: ApiClient, fake_server_dir: Path, tmp_path: Path
    ) -> None:
        first = await _create(admin, fake_server_dir, name="Serveur Modé")
        assert first["slug"] == "serveur-mode"

        second_dir = tmp_path / "autre"
        second_dir.mkdir()
        response = await admin.post(
            "/api/v1/servers", json=fake_server_payload("Serveur  Modé", second_dir)
        )
        assert response.status_code == 201
        assert response.json()["slug"] == "serveur-mode-2"

    async def test_duplicate_name_is_refused(
        self, admin: ApiClient, fake_server_dir: Path, tmp_path: Path
    ) -> None:
        await _create(admin, fake_server_dir)
        other = tmp_path / "autre"
        other.mkdir()

        response = await admin.post("/api/v1/servers", json=fake_server_payload("survie", other))
        assert response.status_code == 409
        assert response.json()["remediation"]

    async def test_duplicate_directory_is_refused(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        await _create(admin, fake_server_dir)
        response = await admin.post(
            "/api/v1/servers", json=fake_server_payload("autre-nom", fake_server_dir)
        )
        assert response.status_code == 409

    async def test_relative_directory_is_refused(self, admin: ApiClient) -> None:
        payload = fake_server_payload("relatif", Path("serveurs/survie"))
        response = await admin.post("/api/v1/servers", json=payload)

        assert response.status_code == 422
        assert "relatif" in response.json()["cause"]

    async def test_missing_directory_is_refused(self, admin: ApiClient, tmp_path: Path) -> None:
        response = await admin.post(
            "/api/v1/servers", json=fake_server_payload("absent", tmp_path / "nexiste-pas")
        )
        assert response.status_code == 422
        assert response.json()["remediation"]

    async def test_invalid_launcher_configuration_is_refused(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        """Un serveur dont le démarrage échouerait n'est pas accepté à la création."""
        response = await admin.post(
            "/api/v1/servers",
            json={
                "name": "sans-jar",
                "directory": str(fake_server_dir),
                "launcher_key": "jar",
                "settings": {"jar_path": "introuvable.jar"},
            },
        )
        assert response.status_code == 400
        assert response.json()["remediation"]


class TestDetection:
    async def test_detects_a_jar_and_its_capabilities(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        (fake_server_dir / "paper-1.20.1-196.jar").write_bytes(b"x" * (6 * 1024 * 1024))
        (fake_server_dir / "plugins").mkdir()
        (fake_server_dir / "server.properties").write_text("server-port=25566\n", encoding="utf-8")

        response = await admin.post(
            "/api/v1/servers/detect", json={"directory": str(fake_server_dir)}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["server_type"] == "PAPER"
        assert body["minecraft_version"] == "1.20.1"
        assert body["launcher_key"] == "jar"
        assert body["jar_path"] == "paper-1.20.1-196.jar"
        assert "plugins" in body["capabilities"]
        assert body["port"] == 25566

    async def test_empty_directory_is_reported_not_rejected(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        response = await admin.post(
            "/api/v1/servers/detect", json={"directory": str(fake_server_dir)}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["launcher_key"] is None
        assert body["notes"], "Un dossier vide doit produire une explication"

    async def test_viewer_cannot_detect(self, viewer: ApiClient, fake_server_dir: Path) -> None:
        response = await viewer.post(
            "/api/v1/servers/detect", json={"directory": str(fake_server_dir)}
        )
        assert response.status_code == 403


class TestUpdateAndDelete:
    async def test_update_settings(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create(admin, fake_server_dir)

        response = await admin.put(
            f"/api/v1/servers/{server['id']}",
            json={"description": "Serveur principal", "settings": {"memory_max_mb": 8192}},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["description"] == "Serveur principal"
        assert body["settings"]["memory_max_mb"] == 8192

    async def test_delete_keeps_files_on_disk(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        marker = fake_server_dir / "world.dat"
        marker.write_text("données précieuses", encoding="utf-8")
        server = await _create(admin, fake_server_dir)

        response = await admin.delete(f"/api/v1/servers/{server['id']}")

        assert response.status_code == 200
        assert marker.exists(), "La suppression du panel ne doit jamais toucher aux fichiers"
        assert (await admin.get(f"/api/v1/servers/{server['id']}")).status_code == 404


class TestPermissions:
    async def test_viewer_can_read_but_not_create(
        self, admin: ApiClient, viewer: ApiClient, fake_server_dir: Path
    ) -> None:
        await _create(admin, fake_server_dir)

        assert (await viewer.get("/api/v1/servers")).status_code == 200

        response = await viewer.post(
            "/api/v1/servers", json=fake_server_payload("interdit", fake_server_dir)
        )
        assert response.status_code == 403
        assert response.json()["remediation"]

    async def test_moderator_cannot_delete_a_server(
        self, admin: ApiClient, moderator: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create(admin, fake_server_dir)

        response = await moderator.delete(f"/api/v1/servers/{server['id']}")
        assert response.status_code == 403

    async def test_viewer_cannot_start_a_server(
        self, admin: ApiClient, viewer: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create(admin, fake_server_dir)

        response = await viewer.post(f"/api/v1/servers/{server['id']}/start")
        assert response.status_code == 403

    async def test_moderator_cannot_force_kill(
        self, admin: ApiClient, moderator: ApiClient, fake_server_dir: Path
    ) -> None:
        """L'arrêt forcé ne sauvegarde pas le monde : réservé aux administrateurs."""
        server = await _create(admin, fake_server_dir)

        response = await moderator.post(f"/api/v1/servers/{server['id']}/kill")
        assert response.status_code == 403

    async def test_unauthenticated_access_is_refused(self, client) -> None:
        # Client volontairement sans session : `admin` partagerait ses cookies.
        assert (await client.get("/api/v1/servers")).status_code == 401


class TestDashboard:
    async def test_dashboard_aggregates_servers_and_host(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        await _create(admin, fake_server_dir)

        response = await admin.get("/api/v1/servers/dashboard")

        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["servers_total"] == 1
        assert body["summary"]["servers_online"] == 0
        assert body["system"]["memory_total_mb"] > 0
        assert len(body["servers"]) == 1


class TestUserManagement:
    async def test_admin_can_create_a_user(self, admin: ApiClient) -> None:
        response = await admin.post(
            "/api/v1/users",
            json={
                "username": "nouveau",
                "password": "un-mot-de-passe-solide-42",
                "role": "MODERATOR",
            },
        )
        assert response.status_code == 201
        assert response.json()["role"] == "MODERATOR"

    async def test_moderator_cannot_manage_users(self, moderator: ApiClient) -> None:
        assert (await moderator.get("/api/v1/users")).status_code == 403

    async def test_admin_cannot_lock_themselves_out(self, admin: ApiClient) -> None:
        """Se désactiver soi-même pourrait laisser le panel sans administrateur."""
        me = (await admin.get("/api/v1/auth/me")).json()

        response = await admin.put(f"/api/v1/users/{me['id']}", json={"is_active": False})

        assert response.status_code == 422
        assert response.json()["remediation"]


class TestAudit:
    async def test_actions_are_recorded_with_their_author(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        await _create(admin, fake_server_dir)

        response = await admin.get("/api/v1/audit")

        assert response.status_code == 200
        entries = response.json()["entries"]
        actions = {entry["action"] for entry in entries}
        assert "auth.login" in actions
        assert "server.created" in actions
        assert all(entry["actor_username"] for entry in entries)

    async def test_failed_login_is_recorded(self, admin: ApiClient, client) -> None:
        await client.post("/api/v1/auth/login", json={"username": "flavien", "password": "faux"})

        response = await admin.get("/api/v1/audit", params={"action": "auth.login_failed"})

        entries = response.json()["entries"]
        assert entries, "Un échec de connexion doit laisser une trace"
        assert entries[0]["result"] == "DENIED"

    async def test_viewer_cannot_read_the_audit_log(self, viewer: ApiClient) -> None:
        assert (await viewer.get("/api/v1/audit")).status_code == 403


async def test_launchers_are_listed(admin: ApiClient) -> None:
    response = await admin.get("/api/v1/system/launchers")

    assert response.status_code == 200
    described = {item["key"]: item for item in response.json()}
    assert described["jar"]["unavailable_reason"] is None
    if sys.platform != "win32":
        assert described["batch"]["unavailable_reason"]
