"""Tests bout en bout des événements, avec un vrai serveur."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar

import pytest

from tests.integration.conftest import ApiClient, fake_server_payload

pytestmark = pytest.mark.asyncio


async def _create_and_start(admin: ApiClient, directory: Path, name: str = "survie") -> dict:
    created = await admin.post("/api/v1/servers", json=fake_server_payload(name, directory))
    assert created.status_code == 201, created.text
    server = created.json()

    started = await admin.post(f"/api/v1/servers/{server['id']}/start")
    assert started.status_code == 200, started.text
    assert await _wait(admin, server["id"], lambda s: s["state"] == "ONLINE")
    return server


async def _wait(admin: ApiClient, server_id: int, predicate, timeout: float = 20.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        response = await admin.get(f"/api/v1/servers/{server_id}/status")
        if predicate(response.json()):
            return True
        await asyncio.sleep(0.05)
    return False


async def _wait_log(admin: ApiClient, server_id: int, needle: str, timeout: float = 20.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        response = await admin.get(f"/api/v1/servers/{server_id}/logs", params={"limit": 300})
        if any(needle in line["text"] for line in response.json()["lines"]):
            return True
        await asyncio.sleep(0.05)
    return False


class TestCatalogue:
    async def test_actions_are_listed_with_their_fields(self, admin: ApiClient) -> None:
        """Le frontend construit ses formulaires à partir de cette réponse."""
        response = await admin.get("/api/v1/events/actions")

        assert response.status_code == 200
        catalogue = {item["key"]: item for item in response.json()}
        assert {"say", "title", "give", "kill", "delay"} <= set(catalogue)
        assert catalogue["kill"]["danger"] == "DESTRUCTIVE"
        assert any(field["name"] == "count" for field in catalogue["give"]["fields"])


class TestQuickActions:
    async def test_say_reaches_the_server(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/events/quick",
            json={"action": "say", "params": {"message": "Bonjour à tous"}},
        )

        assert response.status_code == 200
        assert response.json()["commands"] == ["say Bonjour à tous"]
        assert await _wait_log(admin, server["id"], "[Server] Bonjour à tous")

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_title_sends_three_commands(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/events/quick",
            json={
                "action": "title",
                "params": {"title": "TOURNOI", "subtitle": "Que le meilleur gagne"},
            },
        )

        assert response.status_code == 200
        commands = response.json()["commands"]
        assert len(commands) == 3
        assert commands[0].startswith("title @a times")

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_give_to_everyone(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/events/quick",
            json={"action": "give", "params": {"item": "diamond", "count": 5}},
        )

        assert response.json()["commands"] == ["give @a diamond 5"]

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_kill_everyone_needs_confirmation(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        refused = await admin.post(
            f"/api/v1/servers/{server['id']}/events/quick",
            json={"action": "kill", "params": {"target": "@a"}},
        )

        assert refused.status_code == 428
        assert refused.json()["code"] == "CONFIRMATION_REQUIRED"
        # Une action immédiate n'est pas « un événement » : la confirmation doit
        # décrire ce que l'utilisateur s'apprête réellement à déclencher.
        assert refused.json()["cause"] == "Cette action est irréversible : Tuer @a."

        confirmed = await admin.post(
            f"/api/v1/servers/{server['id']}/events/quick",
            json={"action": "kill", "params": {"target": "@a"}, "confirm": True},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["commands"] == ["kill @a"]

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_stopped_server_is_refused_explicitly(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        created = await admin.post(
            "/api/v1/servers", json=fake_server_payload("arrete", fake_server_dir)
        )
        server_id = created.json()["id"]

        response = await admin.post(
            f"/api/v1/servers/{server_id}/events/quick",
            json={"action": "say", "params": {"message": "personne"}},
        )

        assert response.status_code == 409
        assert response.json()["remediation"]

    async def test_invalid_parameters_are_refused(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/events/quick",
            json={"action": "give", "params": {"item": "diamond ; stop", "count": 1}},
        )

        assert response.status_code == 422
        assert response.json()["remediation"]

        await admin.post(f"/api/v1/servers/{server['id']}/stop")


class TestEventDefinitions:
    TOURNOI: ClassVar[dict] = {
        "name": "Tournoi",
        "description": "Ouverture du tournoi hebdomadaire",
        "steps": [
            {"action": "say", "params": {"message": "Le tournoi commence !"}},
            {"action": "title", "params": {"title": "TOURNOI"}},
            {"action": "delay", "params": {"seconds": 1}},
            {"action": "give", "params": {"item": "diamond_sword", "count": 1}},
        ],
    }

    async def test_create_and_list(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        created = await admin.post(f"/api/v1/servers/{server['id']}/events", json=self.TOURNOI)

        assert created.status_code == 201, created.text
        body = created.json()
        assert body["name"] == "Tournoi"
        assert len(body["steps"]) == 4
        assert body["danger"] == "SAFE"
        # Le résumé est calculé côté serveur, pour rester cohérent avec l'audit.
        assert "Le tournoi commence" in body["steps"][0]["summary"]

        listing = await admin.get(f"/api/v1/servers/{server['id']}/events")
        assert len(listing.json()) == 1

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_invalid_step_is_refused_at_creation(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        """Découvrir l'erreur en plein déroulement serait le pire moment."""
        server = await _create_and_start(admin, fake_server_dir)

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/events",
            json={
                "name": "Cassé",
                "steps": [
                    {"action": "say", "params": {"message": "ok"}},
                    {"action": "give", "params": {"item": "diamond", "count": 0}},
                ],
            },
        )

        assert response.status_code == 422
        assert "Étape 2" in response.json()["message"]

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_destructive_event_is_flagged(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        created = await admin.post(
            f"/api/v1/servers/{server['id']}/events",
            json={
                "name": "Purge",
                "steps": [
                    {"action": "say", "params": {"message": "Attention"}},
                    {"action": "kill", "params": {"target": "@a"}},
                ],
            },
        )

        assert created.json()["danger"] == "DESTRUCTIVE"

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_update_and_delete(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create_and_start(admin, fake_server_dir)
        event_id = (
            await admin.post(f"/api/v1/servers/{server['id']}/events", json=self.TOURNOI)
        ).json()["id"]

        updated = await admin.put(
            f"/api/v1/servers/{server['id']}/events/{event_id}",
            json={"name": "Grand tournoi"},
        )
        assert updated.json()["name"] == "Grand tournoi"

        deleted = await admin.delete(f"/api/v1/servers/{server['id']}/events/{event_id}")
        assert deleted.status_code == 200
        assert (await admin.get(f"/api/v1/servers/{server['id']}/events")).json() == []

        await admin.post(f"/api/v1/servers/{server['id']}/stop")


class TestRuns:
    SEQUENCE: ClassVar[dict] = {
        "name": "Séquence",
        "steps": [
            {"action": "say", "params": {"message": "étape une"}},
            {"action": "delay", "params": {"seconds": 1}},
            {"action": "say", "params": {"message": "étape deux"}},
        ],
    }

    async def test_run_executes_every_step(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create_and_start(admin, fake_server_dir)
        event_id = (
            await admin.post(f"/api/v1/servers/{server['id']}/events", json=self.SEQUENCE)
        ).json()["id"]

        started = await admin.post(f"/api/v1/servers/{server['id']}/events/{event_id}/run", json={})

        assert started.status_code == 200
        assert started.json()["status"] == "RUNNING"
        assert started.json()["total_steps"] == 3

        # Les deux messages arrivent, séparés par l'attente d'une seconde.
        assert await _wait_log(admin, server["id"], "[Server] étape une")
        assert await _wait_log(admin, server["id"], "[Server] étape deux")

        runs = None
        for _ in range(100):
            runs = (await admin.get(f"/api/v1/servers/{server['id']}/events/runs")).json()
            if runs and runs[0]["status"] != "RUNNING":
                break
            await asyncio.sleep(0.1)

        assert runs and runs[0]["status"] == "COMPLETED"
        assert runs[0]["current_step"] == 3

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_run_can_be_cancelled_during_a_delay(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        """Un événement long doit rester interruptible."""
        server = await _create_and_start(admin, fake_server_dir)
        event_id = (
            await admin.post(
                f"/api/v1/servers/{server['id']}/events",
                json={
                    "name": "Longue attente",
                    "steps": [
                        {"action": "say", "params": {"message": "début"}},
                        {"action": "delay", "params": {"seconds": 3600}},
                        {"action": "say", "params": {"message": "jamais atteint"}},
                    ],
                },
            )
        ).json()["id"]

        run_id = (
            await admin.post(f"/api/v1/servers/{server['id']}/events/{event_id}/run", json={})
        ).json()["id"]
        assert await _wait_log(admin, server["id"], "[Server] début")

        cancelled = await admin.post(f"/api/v1/servers/{server['id']}/events/runs/{run_id}/cancel")

        assert cancelled.json()["cancelled"] is True

        status = None
        for _ in range(100):
            runs = (await admin.get(f"/api/v1/servers/{server['id']}/events/runs")).json()
            status = next((run["status"] for run in runs if run["id"] == run_id), None)
            if status and status != "RUNNING":
                break
            await asyncio.sleep(0.1)

        assert status == "CANCELLED"
        # La dernière étape n'a jamais été exécutée.
        logs = (
            await admin.get(f"/api/v1/servers/{server['id']}/logs", params={"limit": 300})
        ).json()["lines"]
        assert not any("jamais atteint" in line["text"] for line in logs)

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_destructive_event_needs_confirmation_to_run(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)
        event_id = (
            await admin.post(
                f"/api/v1/servers/{server['id']}/events",
                json={
                    "name": "Purge",
                    "steps": [{"action": "kill", "params": {"target": "@a"}}],
                },
            )
        ).json()["id"]

        refused = await admin.post(f"/api/v1/servers/{server['id']}/events/{event_id}/run", json={})
        assert refused.status_code == 428
        assert "Cet événement contient" in refused.json()["cause"]

        confirmed = await admin.post(
            f"/api/v1/servers/{server['id']}/events/{event_id}/run", json={"confirm": True}
        )
        assert confirmed.status_code == 200

        await admin.post(f"/api/v1/servers/{server['id']}/stop")


class TestPermissions:
    async def test_moderator_can_run_but_not_edit(
        self, admin: ApiClient, moderator: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        allowed = await moderator.post(
            f"/api/v1/servers/{server['id']}/events/quick",
            json={"action": "say", "params": {"message": "bonjour"}},
        )
        assert allowed.status_code == 200

        refused = await moderator.post(
            f"/api/v1/servers/{server['id']}/events",
            json={"name": "Interdit", "steps": [{"action": "say", "params": {"message": "x"}}]},
        )
        assert refused.status_code == 403

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_moderator_cannot_run_destructive_actions(
        self, admin: ApiClient, moderator: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        response = await moderator.post(
            f"/api/v1/servers/{server['id']}/events/quick",
            json={"action": "kill", "params": {"target": "@a"}, "confirm": True},
        )

        assert response.status_code == 403
        assert response.json()["remediation"]

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_viewer_cannot_trigger_anything(
        self, admin: ApiClient, viewer: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_and_start(admin, fake_server_dir)

        response = await viewer.post(
            f"/api/v1/servers/{server['id']}/events/quick",
            json={"action": "say", "params": {"message": "bonjour"}},
        )

        assert response.status_code == 403

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_actions_are_audited(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create_and_start(admin, fake_server_dir)
        await admin.post(
            f"/api/v1/servers/{server['id']}/events/quick",
            json={"action": "say", "params": {"message": "trace"}},
        )

        entries = (await admin.get("/api/v1/audit", params={"action": "event.run"})).json()[
            "entries"
        ]

        assert entries
        assert "trace" in entries[0]["summary"]
        assert entries[0]["actor_username"] == "flavien"

        await admin.post(f"/api/v1/servers/{server['id']}/stop")
