"""Tests bout en bout : cycle de vie, console et commandes sensibles.

Ces tests lancent de vrais processus via l'API HTTP complète — authentification,
CSRF, permissions et audit compris.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.integration.conftest import ApiClient, fake_server_payload

pytestmark = pytest.mark.asyncio


async def _create_and_start(admin: ApiClient, directory: Path, name: str = "survie") -> dict:
    created = await admin.post("/api/v1/servers", json=fake_server_payload(name, directory))
    assert created.status_code == 201, created.text
    server = created.json()

    started = await admin.post(f"/api/v1/servers/{server['id']}/start")
    assert started.status_code == 200, started.text
    assert await _wait_state(admin, server["id"], "ONLINE")
    return server


async def _wait_state(admin: ApiClient, server_id: int, state: str, timeout: float = 20.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        response = await admin.get(f"/api/v1/servers/{server_id}/status")
        if response.json().get("state") == state:
            return True
        await asyncio.sleep(0.05)
    return False


async def _wait_log(admin: ApiClient, server_id: int, needle: str, timeout: float = 20.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        response = await admin.get(f"/api/v1/servers/{server_id}/logs", params={"limit": 200})
        if any(needle in line["text"] for line in response.json()["lines"]):
            return True
        await asyncio.sleep(0.05)
    return False


class TestLifecycle:
    async def test_start_then_stop(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        response = await admin.post(f"/api/v1/servers/{server['id']}/stop")

        assert response.status_code == 200
        body = response.json()
        assert body["stage"] == "command", "L'arrêt devrait aboutir par la commande `stop`"
        assert body["forced"] is False
        assert await _wait_state(admin, server["id"], "OFFLINE")

    async def test_starting_twice_is_refused(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        response = await admin.post(f"/api/v1/servers/{server['id']}/start")

        assert response.status_code == 409
        assert response.json()["code"] == "SERVER_ALREADY_RUNNING"

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_configuration_cannot_change_while_running(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        response = await admin.put(
            f"/api/v1/servers/{server['id']}", json={"settings": {"memory_max_mb": 2048}}
        )

        assert response.status_code == 409
        assert "Arrêter le serveur" in response.json()["remediation"]

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_stopping_one_server_leaves_the_other_running(
        self, admin: ApiClient, tmp_path: Path
    ) -> None:
        """Garantie d'isolation, vérifiée cette fois à travers toute la pile HTTP."""
        first_dir = tmp_path / "srv-a"
        second_dir = tmp_path / "srv-b"
        first_dir.mkdir()
        second_dir.mkdir()

        first = await _create_and_start(admin, first_dir, name="alpha")
        second = await _create_and_start(admin, second_dir, name="beta")

        await admin.post(f"/api/v1/servers/{first['id']}/stop")
        assert await _wait_state(admin, first["id"], "OFFLINE")

        status = (await admin.get(f"/api/v1/servers/{second['id']}/status")).json()
        assert status["state"] == "ONLINE"
        assert status["pid"] is not None

        await admin.post(f"/api/v1/servers/{second['id']}/stop")


class TestConsole:
    async def test_command_reaches_the_server(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/command", json={"command": "say bonjour"}
        )

        assert response.status_code == 200
        assert response.json()["danger"] == "SAFE"
        assert await _wait_log(admin, server["id"], "[Server] bonjour")

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_logs_can_be_resumed_without_gap(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        first = (await admin.get(f"/api/v1/servers/{server['id']}/logs")).json()
        cursor = first["last_seq"]

        await admin.post(f"/api/v1/servers/{server['id']}/command", json={"command": "say suite"})
        assert await _wait_log(admin, server["id"], "[Server] suite")

        resumed = (
            await admin.get(f"/api/v1/servers/{server['id']}/logs", params={"since": cursor})
        ).json()

        assert resumed["lines"], "La reprise doit renvoyer les lignes manquantes"
        assert all(line["seq"] > cursor for line in resumed["lines"])

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_log_search(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create_and_start(admin, fake_server_dir)
        await admin.post(
            f"/api/v1/servers/{server['id']}/command", json={"command": "say aiguille"}
        )
        assert await _wait_log(admin, server["id"], "aiguille")

        response = await admin.get(
            f"/api/v1/servers/{server['id']}/logs", params={"search": "aiguille"}
        )

        assert response.status_code == 200
        assert response.json()["lines"]

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_command_injection_is_refused(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/command",
            json={"command": "say bonjour\nop attaquant"},
        )

        assert response.status_code == 400
        assert response.json()["code"] == "UNSAFE_COMMAND"

        await admin.post(f"/api/v1/servers/{server['id']}/stop")


class TestDangerousCommands:
    async def test_sensitive_command_requires_confirmation(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/command", json={"command": "op Flavien"}
        )

        assert response.status_code == 428
        assert response.json()["code"] == "CONFIRMATION_REQUIRED"
        assert "administrateur" in response.json()["cause"]

        confirmed = await admin.post(
            f"/api/v1/servers/{server['id']}/command",
            json={"command": "op Flavien", "confirm": True},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["danger"] == "SENSITIVE"

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_moderator_cannot_run_sensitive_commands(
        self, admin: ApiClient, moderator: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        allowed = await moderator.post(
            f"/api/v1/servers/{server['id']}/command", json={"command": "say bonjour"}
        )
        assert allowed.status_code == 200

        refused = await moderator.post(
            f"/api/v1/servers/{server['id']}/command",
            json={"command": "op Flavien", "confirm": True},
        )
        assert refused.status_code == 403

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_refused_command_is_audited(
        self, admin: ApiClient, moderator: ApiClient, fake_server_dir: Path
    ) -> None:
        """Une tentative refusée doit laisser une trace : c'est tout l'intérêt de l'audit."""
        server = await _create_and_start(admin, fake_server_dir)
        await moderator.post(
            f"/api/v1/servers/{server['id']}/command",
            json={"command": "stop", "confirm": True},
        )

        entries = (await admin.get("/api/v1/audit", params={"action": "console.command"})).json()[
            "entries"
        ]

        denied = [entry for entry in entries if entry["result"] == "DENIED"]
        assert denied, "Le refus doit apparaître dans le journal d'audit"
        assert denied[0]["actor_username"] == "moderateur"

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_inspect_describes_a_command_without_running_it(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/command/inspect", json={"command": "kill @a"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["danger"] == "DESTRUCTIVE"
        assert body["requires_strong_confirmation"] is True
        assert body["explanation"]

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_harmless_message_mentioning_stop_is_not_flagged(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/command",
            json={"command": "say attention je vais stop le serveur"},
        )

        assert response.status_code == 200
        assert response.json()["danger"] == "SAFE"

        await admin.post(f"/api/v1/servers/{server['id']}/stop")
