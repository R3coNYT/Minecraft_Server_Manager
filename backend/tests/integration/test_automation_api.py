"""Tests bout en bout des tâches programmées, notifications et téléchargements."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.integration.conftest import ApiClient, fake_server_payload

pytestmark = pytest.mark.asyncio

DAILY = {"trigger": "DAILY", "hour": 4, "minute": 0, "timezone": "Europe/Paris"}
WEBHOOK = "https://discord.com/api/webhooks/1/jeton-de-test"


async def _create_server(admin: ApiClient, directory: Path, name: str = "survie") -> dict:
    created = await admin.post("/api/v1/servers", json=fake_server_payload(name, directory))
    assert created.status_code == 201, created.text
    return created.json()


class TestSchedules:
    async def test_create_computes_the_next_occurrence(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_server(admin, fake_server_dir)

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/schedules",
            json={"name": "Sauvegarde nocturne", "action": "BACKUP", "rule": DAILY},
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["summary"] == "Chaque jour à 04:00 (Europe/Paris)"
        assert body["next_run_at"] is not None
        assert body["last_status"] == "NEVER"

    async def test_invalid_rule_is_refused_with_an_action(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_server(admin, fake_server_dir)

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/schedules",
            json={
                "name": "Trop souvent",
                "action": "BACKUP",
                "rule": {"trigger": "INTERVAL", "interval_minutes": 1},
            },
        )

        assert response.status_code == 422
        assert response.json()["remediation"]

    async def test_event_schedule_requires_an_existing_event(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        """Découvrir à 4 h du matin que l'événement n'existe pas serait trop tard."""
        server = await _create_server(admin, fake_server_dir)

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/schedules",
            json={
                "name": "Tournoi",
                "action": "EVENT",
                "rule": DAILY,
                "payload": {"event_id": 999},
            },
        )

        assert response.status_code == 404

    async def test_disabling_clears_the_next_occurrence(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_server(admin, fake_server_dir)
        created = (
            await admin.post(
                f"/api/v1/servers/{server['id']}/schedules",
                json={"name": "Sauvegarde", "action": "BACKUP", "rule": DAILY},
            )
        ).json()

        response = await admin.put(
            f"/api/v1/servers/{server['id']}/schedules/{created['id']}",
            json={"enabled": False},
        )

        assert response.json()["next_run_at"] is None

    async def test_delete(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create_server(admin, fake_server_dir)
        created = (
            await admin.post(
                f"/api/v1/servers/{server['id']}/schedules",
                json={"name": "Sauvegarde", "action": "BACKUP", "rule": DAILY},
            )
        ).json()

        response = await admin.delete(f"/api/v1/servers/{server['id']}/schedules/{created['id']}")

        assert response.status_code == 200
        assert (await admin.get(f"/api/v1/servers/{server['id']}/schedules")).json() == []


class TestScheduleExecution:
    async def test_manual_run_executes_the_action(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_server(admin, fake_server_dir)
        await admin.post(f"/api/v1/servers/{server['id']}/start")
        created = (
            await admin.post(
                f"/api/v1/servers/{server['id']}/schedules",
                json={
                    "name": "Annonce",
                    "action": "COMMAND",
                    "rule": DAILY,
                    "payload": {"command": "say bonsoir"},
                },
            )
        ).json()

        response = await admin.post(f"/api/v1/servers/{server['id']}/schedules/{created['id']}/run")

        assert response.status_code == 200, response.text
        assert response.json()["last_status"] == "SUCCESS"

        logs = (
            await admin.get(f"/api/v1/servers/{server['id']}/logs", params={"limit": 200})
        ).json()["lines"]
        assert any("bonsoir" in line["text"] for line in logs)

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_restart_of_a_stopped_server_is_skipped_not_failed(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        """Un serveur déjà arrêté n'est pas une panne : c'est un non-événement."""
        server = await _create_server(admin, fake_server_dir)
        created = (
            await admin.post(
                f"/api/v1/servers/{server['id']}/schedules",
                json={"name": "Redémarrage", "action": "RESTART", "rule": DAILY},
            )
        ).json()

        response = await admin.post(f"/api/v1/servers/{server['id']}/schedules/{created['id']}/run")

        assert response.json()["last_status"] == "SKIPPED"

    async def test_due_task_runs_on_the_next_tick(
        self, admin: ApiClient, fake_server_dir: Path, app
    ) -> None:
        from msm.db.models.schedule import Schedule
        from msm.db.session import session_scope
        from msm.services.schedule_service import Scheduler

        server = await _create_server(admin, fake_server_dir)
        await admin.post(f"/api/v1/servers/{server['id']}/start")
        created = (
            await admin.post(
                f"/api/v1/servers/{server['id']}/schedules",
                json={
                    "name": "Annonce",
                    "action": "COMMAND",
                    "rule": {"trigger": "INTERVAL", "interval_minutes": 60},
                    "payload": {"command": "say programmé"},
                },
            )
        ).json()

        # La tâche devient due il y a une minute.
        async with session_scope() as session:
            schedule = await session.get(Schedule, created["id"])
            schedule.next_run_at = datetime.now(UTC) - timedelta(minutes=1)

        scheduler = Scheduler(app.state.supervisor, app.state.settings)
        assert await scheduler.tick() == 1

        logs = (
            await admin.get(f"/api/v1/servers/{server['id']}/logs", params={"limit": 200})
        ).json()["lines"]
        assert any("programmé" in line["text"] for line in logs)

        after = (await admin.get(f"/api/v1/servers/{server['id']}/schedules")).json()[0]
        assert after["last_status"] == "SUCCESS"
        # La prochaine occurrence est repoussée : la tâche ne boucle pas.
        assert datetime.fromisoformat(after["next_run_at"]) > datetime.now(UTC)

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_a_long_outage_marks_the_run_missed(
        self, admin: ApiClient, fake_server_dir: Path, app
    ) -> None:
        """Rejouer une sauvegarde nocturne en pleine journée ne rend service à personne."""
        from msm.db.models.schedule import Schedule
        from msm.db.session import session_scope
        from msm.services.schedule_service import Scheduler

        server = await _create_server(admin, fake_server_dir)
        created = (
            await admin.post(
                f"/api/v1/servers/{server['id']}/schedules",
                json={"name": "Sauvegarde", "action": "BACKUP", "rule": DAILY},
            )
        ).json()

        async with session_scope() as session:
            schedule = await session.get(Schedule, created["id"])
            schedule.next_run_at = datetime.now(UTC) - timedelta(hours=12)

        scheduler = Scheduler(app.state.supervisor, app.state.settings)
        assert await scheduler.tick() == 0

        after = (await admin.get(f"/api/v1/servers/{server['id']}/schedules")).json()[0]
        assert after["last_status"] == "MISSED"
        assert after["last_error"]
        assert datetime.fromisoformat(after["next_run_at"]) > datetime.now(UTC)

    async def test_a_task_loses_its_powers_with_its_author(
        self, admin: ApiClient, moderator: ApiClient, fake_server_dir: Path, app
    ) -> None:
        """Une tâche n'est pas un moyen de conserver des droits perdus depuis."""
        from msm.db.models.schedule import Schedule
        from msm.db.models.user import User
        from msm.db.session import session_scope
        from msm.services.schedule_service import run_schedule

        server = await _create_server(admin, fake_server_dir)
        await admin.post(f"/api/v1/servers/{server['id']}/start")
        created = (
            await moderator.post(
                f"/api/v1/servers/{server['id']}/schedules",
                json={
                    "name": "Annonce",
                    "action": "COMMAND",
                    "rule": DAILY,
                    "payload": {"command": "say bonsoir"},
                },
            )
        ).json()
        assert created.get("id"), created

        # Le compte du modérateur est désactivé après coup.
        users = (await admin.get("/api/v1/users")).json()
        moderator_id = next(user["id"] for user in users if user["username"] == "moderateur")
        async with session_scope() as session:
            user = await session.get(User, moderator_id)
            user.is_active = False

        status = await run_schedule(
            created["id"], supervisor=app.state.supervisor, settings=app.state.settings
        )

        assert status.value == "FAILED"
        async with session_scope() as session:
            schedule = await session.get(Schedule, created["id"])
            assert "désactivé" in (schedule.last_error or "")

        await admin.post(f"/api/v1/servers/{server['id']}/stop")


class TestSchedulePermissions:
    async def test_a_viewer_cannot_program_anything(
        self, admin: ApiClient, viewer: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_server(admin, fake_server_dir)

        response = await viewer.post(
            f"/api/v1/servers/{server['id']}/schedules",
            json={"name": "Sauvegarde", "action": "BACKUP", "rule": DAILY},
        )

        assert response.status_code == 403
        assert response.json()["remediation"]

    async def test_the_action_permission_is_required_at_creation(
        self, admin: ApiClient, moderator: ApiClient, fake_server_dir: Path
    ) -> None:
        """Programmer une sauvegarde sans droit de sauvegarder échouerait chaque nuit."""
        server = await _create_server(admin, fake_server_dir)

        response = await moderator.post(
            f"/api/v1/servers/{server['id']}/schedules",
            json={"name": "Sauvegarde", "action": "BACKUP", "rule": DAILY},
        )

        assert response.status_code == 403


class TestNotifications:
    async def test_settings_start_disabled(self, admin: ApiClient) -> None:
        response = await admin.get("/api/v1/notifications")

        assert response.status_code == 200
        body = response.json()
        assert body["enabled"] is False
        assert body["webhook_configured"] is False

    async def test_webhook_is_stored_but_never_returned(self, admin: ApiClient) -> None:
        """Qui détient l'adresse peut écrire dans le salon : elle ne ressort pas."""
        response = await admin.put(
            "/api/v1/notifications", json={"webhook_url": WEBHOOK, "enabled": True}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["webhook_configured"] is True
        assert body["enabled"] is True
        assert WEBHOOK not in response.text
        assert body["webhook_hint"].endswith("e-test")

    async def test_a_foreign_address_is_refused(self, admin: ApiClient) -> None:
        response = await admin.put(
            "/api/v1/notifications", json={"webhook_url": "https://evil.example/collecte"}
        )

        assert response.status_code == 422
        assert response.json()["remediation"]

    async def test_cannot_enable_without_a_webhook(self, admin: ApiClient) -> None:
        response = await admin.put("/api/v1/notifications", json={"enabled": True})

        assert response.status_code == 422
        assert "webhook" in response.json()["remediation"].lower()

    async def test_unknown_event_is_refused(self, admin: ApiClient) -> None:
        response = await admin.put("/api/v1/notifications", json={"events": ["server_exploded"]})

        assert response.status_code == 422

    async def test_clearing_the_webhook_disables_notifications(self, admin: ApiClient) -> None:
        await admin.put("/api/v1/notifications", json={"webhook_url": WEBHOOK, "enabled": True})

        response = await admin.put("/api/v1/notifications", json={"clear_webhook": True})

        body = response.json()
        assert body["webhook_configured"] is False
        assert body["enabled"] is False

    async def test_a_moderator_cannot_read_or_change_them(self, moderator: ApiClient) -> None:
        assert (await moderator.get("/api/v1/notifications")).status_code == 403
        assert (
            await moderator.put("/api/v1/notifications", json={"enabled": False})
        ).status_code == 403

    async def test_events_are_listed_with_their_labels(self, admin: ApiClient) -> None:
        response = await admin.get("/api/v1/notifications/events")

        keys = {item["key"] for item in response.json()}
        assert "server_crashed" in keys
        assert all(item["label"] for item in response.json())


class TestDownloads:
    async def test_sources_are_listed(self, admin: ApiClient) -> None:
        response = await admin.get("/api/v1/downloads/sources")

        assert response.status_code == 200
        assert {item["key"] for item in response.json()} == {"vanilla", "paper", "purpur"}

    async def test_unknown_source_is_refused_without_touching_the_network(
        self, admin: ApiClient
    ) -> None:
        response = await admin.get("/api/v1/downloads/forge/versions")

        assert response.status_code == 422
        assert "vanilla" in response.json()["remediation"]

    async def test_a_running_server_cannot_change_version(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_server(admin, fake_server_dir)
        await admin.post(f"/api/v1/servers/{server['id']}/start")

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/install",
            json={"source": "vanilla", "version": "1.21.1"},
        )

        assert response.status_code == 409
        assert response.json()["remediation"]

        await admin.post(f"/api/v1/servers/{server['id']}/stop")
        await asyncio.sleep(0)
