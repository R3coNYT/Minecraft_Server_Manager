"""Tests bout en bout du suivi et de la modération des joueurs."""

from __future__ import annotations

import asyncio
import json
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
    assert await _wait(admin, lambda status: status["state"] == "ONLINE", server["id"])
    return server


async def _wait(admin: ApiClient, predicate, server_id: int, timeout: float = 20.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        response = await admin.get(f"/api/v1/servers/{server_id}/status")
        if predicate(response.json()):
            return True
        await asyncio.sleep(0.05)
    return False


async def _wait_players(admin: ApiClient, server_id: int, predicate, timeout: float = 20.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        players = (await admin.get(f"/api/v1/servers/{server_id}/players")).json()
        if predicate(players):
            return players
        await asyncio.sleep(0.05)
    return None


class TestPlayerTracking:
    async def test_join_is_detected_with_its_uuid(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        """L'UUID est annoncé *avant* la connexion : il doit être rattaché au joueur."""
        server = await _create_and_start(admin, fake_server_dir)

        await admin.post(
            f"/api/v1/servers/{server['id']}/command", json={"command": "join Flavien"}
        )

        players = await _wait_players(
            admin, server["id"], lambda players: any(p["online"] for p in players)
        )
        assert players is not None, "Le joueur connecté n'est jamais apparu"

        flavien = next(p for p in players if p["username"] == "Flavien")
        assert flavien["online"] is True
        assert flavien["uuid"] == "069a79f4-44e9-4726-a5be-fca90e38aaf5"
        # Minecraft n'expose pas le ping par joueur : le champ reste nul.
        assert flavien["ping_ms"] is None

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_leave_keeps_the_player_in_history(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)
        await admin.post(
            f"/api/v1/servers/{server['id']}/command", json={"command": "join Flavien"}
        )
        assert await _wait_players(
            admin, server["id"], lambda players: any(p["online"] for p in players)
        )

        await admin.post(
            f"/api/v1/servers/{server['id']}/command", json={"command": "leave Flavien"}
        )

        players = await _wait_players(
            admin, server["id"], lambda players: players and not any(p["online"] for p in players)
        )
        assert players is not None
        flavien = next(p for p in players if p["username"] == "Flavien")
        assert flavien["online"] is False
        assert flavien["total_sessions"] >= 1
        assert flavien["first_seen"] is not None

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_statuses_come_from_the_server_files(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        """Opérateur, bannissement et liste blanche viennent des fichiers JSON."""
        (fake_server_dir / "ops.json").write_text(
            json.dumps([{"name": "Flavien", "uuid": "069a79f4-...", "level": 4}]),
            encoding="utf-8",
        )
        (fake_server_dir / "banned-players.json").write_text(
            json.dumps([{"name": "Tricheur", "reason": "mods interdits"}]), encoding="utf-8"
        )
        (fake_server_dir / "usercache.json").write_text(
            json.dumps([{"name": "Tricheur", "uuid": "11111111-2222-3333-4444-555555555555"}]),
            encoding="utf-8",
        )
        server = await _create_and_start(admin, fake_server_dir)

        await admin.post(
            f"/api/v1/servers/{server['id']}/command", json={"command": "join Flavien"}
        )
        players = await _wait_players(
            admin, server["id"], lambda players: any(p["online"] for p in players)
        )
        assert players is not None

        flavien = next(p for p in players if p["username"] == "Flavien")
        assert flavien["is_op"] is True
        assert flavien["op_level"] == 4

        # Un joueur banni sans être jamais passé par MSM doit tout de même
        # apparaître : sinon on ne pourrait pas le débannir depuis l'interface.
        tricheur = next(p for p in players if p["username"] == "Tricheur")
        assert tricheur["is_banned"] is True
        assert tricheur["online"] is False
        assert tricheur["ban_reason"] == "mods interdits"
        assert tricheur["uuid"] == "11111111-2222-3333-4444-555555555555"

        await admin.post(f"/api/v1/servers/{server['id']}/stop")


class TestModeration:
    async def test_give_builds_the_expected_command(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/players/Flavien/give",
            json={"item": "diamond", "count": 64},
        )

        assert response.status_code == 200
        assert response.json()["command"] == "give Flavien diamond 64"

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_kick_with_reason(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/players/Flavien/kick",
            json={"reason": "comportement inapproprié"},
        )

        assert response.status_code == 200
        assert response.json()["command"] == "kick Flavien comportement inapproprié"

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_actions_require_a_running_server(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        """Expulser d'un serveur éteint n'a aucun sens : le refus doit être explicite."""
        created = await admin.post(
            "/api/v1/servers", json=fake_server_payload("arrete", fake_server_dir)
        )
        server_id = created.json()["id"]

        response = await admin.post(f"/api/v1/servers/{server_id}/players/Flavien/kick", json={})

        assert response.status_code == 409
        assert response.json()["code"] == "SERVER_NOT_RUNNING"
        assert response.json()["remediation"]

    async def test_invalid_username_is_refused_by_the_route(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        """La validation du pseudo se fait avant d'atteindre la console."""
        server = await _create_and_start(admin, fake_server_dir)

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/players/Flavien;stop/kick", json={}
        )

        assert response.status_code in (404, 422)

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_give_rejects_a_hostile_item(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/players/Flavien/give",
            json={"item": "diamond ; stop", "count": 1},
        )

        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"

        await admin.post(f"/api/v1/servers/{server['id']}/stop")


class TestPermissions:
    async def test_moderator_can_kick_but_not_op(
        self, admin: ApiClient, moderator: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        allowed = await moderator.post(
            f"/api/v1/servers/{server['id']}/players/Flavien/kick", json={}
        )
        assert allowed.status_code == 200

        refused = await moderator.post(f"/api/v1/servers/{server['id']}/players/Flavien/op")
        assert refused.status_code == 403
        assert refused.json()["remediation"]

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_viewer_can_only_read(
        self, admin: ApiClient, viewer: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        assert (await viewer.get(f"/api/v1/servers/{server['id']}/players")).status_code == 200
        assert (
            await viewer.post(f"/api/v1/servers/{server['id']}/players/Flavien/kick", json={})
        ).status_code == 403

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_actions_are_audited_with_their_target(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)
        await admin.post(
            f"/api/v1/servers/{server['id']}/players/Flavien/give",
            json={"item": "diamond", "count": 5},
        )

        entries = (await admin.get("/api/v1/audit", params={"action": "player.give"})).json()[
            "entries"
        ]

        assert entries, "L'action doit apparaître dans le journal d'audit"
        assert entries[0]["target_id"] == "Flavien"
        assert entries[0]["payload"]["command"] == "give Flavien diamond 5"

        await admin.post(f"/api/v1/servers/{server['id']}/stop")


class TestSkins:
    async def test_unknown_player_has_no_skin(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        """Sans UUID connu, aucune requête externe n'est tentée."""
        server = await _create_and_start(admin, fake_server_dir)

        response = await admin.get(f"/api/v1/servers/{server['id']}/players/Inconnu/skin.png")

        assert response.status_code == 404

        await admin.post(f"/api/v1/servers/{server['id']}/stop")
