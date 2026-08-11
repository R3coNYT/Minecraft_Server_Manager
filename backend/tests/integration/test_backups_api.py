"""Tests bout en bout des sauvegardes et de l'historique des ressources."""

from __future__ import annotations

import asyncio
import tarfile
from io import BytesIO
from pathlib import Path

import pytest

from tests.integration.conftest import ApiClient, fake_server_payload

pytestmark = pytest.mark.asyncio


def _populate(directory: Path) -> None:
    """Donne au dossier de quoi être sauvegardé : un monde et des réglages."""
    (directory / "world" / "region").mkdir(parents=True, exist_ok=True)
    (directory / "world" / "level.dat").write_bytes(b"niveau-origine")
    (directory / "world" / "region" / "r.0.0.mca").write_bytes(b"region" * 50)
    (directory / "server.properties").write_text("level-name=world\nmotd=Salut\n", encoding="utf-8")
    (directory / "mods").mkdir(exist_ok=True)
    (directory / "mods" / "jei.jar").write_bytes(b"x" * 4096)


async def _create_server(admin: ApiClient, directory: Path, name: str = "survie") -> dict:
    _populate(directory)
    created = await admin.post("/api/v1/servers", json=fake_server_payload(name, directory))
    assert created.status_code == 201, created.text
    return created.json()


async def _wait_backup(admin: ApiClient, server_id: int, backup_id: int, timeout: float = 30.0):
    """Attend la fin d'une sauvegarde lancée en tâche de fond."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        listing = (await admin.get(f"/api/v1/servers/{server_id}/backups")).json()
        backup = next((item for item in listing if item["id"] == backup_id), None)
        if backup and backup["status"] not in ("PENDING", "RUNNING"):
            return backup
        await asyncio.sleep(0.05)
    raise AssertionError("La sauvegarde ne s'est pas terminée à temps.")


async def _backup_now(admin: ApiClient, server_id: int) -> dict:
    started = await admin.post(f"/api/v1/servers/{server_id}/backups")
    assert started.status_code == 202, started.text
    return await _wait_backup(admin, server_id, started.json()["id"])


class TestCreate:
    async def test_backup_of_a_stopped_server(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_server(admin, fake_server_dir)

        backup = await _backup_now(admin, server["id"])

        assert backup["status"] == "COMPLETED"
        assert backup["size_bytes"] > 0
        assert backup["available"] is True

    async def test_manifest_lists_worlds_and_mods(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        """Les mods ne sont pas dans l'archive : ils doivent y être décrits."""
        server = await _create_server(admin, fake_server_dir)
        backup = await _backup_now(admin, server["id"])

        manifest = (
            await admin.get(f"/api/v1/servers/{server['id']}/backups/{backup['id']}/manifest")
        ).json()

        assert manifest["content"]["worlds"] == ["world"]
        assert [mod["name"] for mod in manifest["mods"]] == ["jei.jar"]

    async def test_download_returns_a_readable_archive(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_server(admin, fake_server_dir)
        backup = await _backup_now(admin, server["id"])

        response = await admin.get(
            f"/api/v1/servers/{server['id']}/backups/{backup['id']}/download"
        )

        assert response.status_code == 200
        with tarfile.open(fileobj=BytesIO(response.content), mode="r:gz") as archive:
            names = archive.getnames()
        assert "world/level.dat" in names
        assert "msm-manifest.json" in names

    async def test_empty_directory_is_refused_with_a_reason(
        self, admin: ApiClient, tmp_path: Path
    ) -> None:
        """Une sauvegarde vide donnerait une fausse impression de sécurité."""
        empty = tmp_path / "vide"
        empty.mkdir()
        created = await admin.post("/api/v1/servers", json=fake_server_payload("vide", empty))
        server_id = created.json()["id"]

        started = await admin.post(f"/api/v1/servers/{server_id}/backups")
        backup = await _wait_backup(admin, server_id, started.json()["id"])

        assert backup["status"] == "FAILED"
        assert "monde" in (backup["error"] or "")


class TestHotBackup:
    async def test_running_server_suspends_its_writes(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_server(admin, fake_server_dir)
        await admin.post(f"/api/v1/servers/{server['id']}/start")

        backup = await _backup_now(admin, server["id"])

        assert backup["status"] == "COMPLETED"
        logs = (
            await admin.get(f"/api/v1/servers/{server['id']}/logs", params={"limit": 300})
        ).json()["lines"]
        texts = [line["text"] for line in logs]
        assert any("Automatic saving is now disabled" in text for text in texts)
        # L'écriture est bien rétablie : sans cela, le serveur perdrait tout au
        # prochain plantage.
        assert any("Automatic saving is now enabled" in text for text in texts)

        await admin.post(f"/api/v1/servers/{server['id']}/stop")

    async def test_silent_server_makes_the_backup_fail_rather_than_lie(
        self, admin: ApiClient, tmp_path: Path
    ) -> None:
        directory = tmp_path / "muet"
        directory.mkdir()
        _populate(directory)
        created = await admin.post(
            "/api/v1/servers", json=fake_server_payload("muet", directory, "--ignore-save")
        )
        server_id = created.json()["id"]
        await admin.post(f"/api/v1/servers/{server_id}/start")

        started = await admin.post(f"/api/v1/servers/{server_id}/backups")
        backup = await _wait_backup(admin, server_id, started.json()["id"], timeout=90.0)

        assert backup["status"] == "FAILED"
        assert "confirmé" in (backup["error"] or "")

        await admin.post(f"/api/v1/servers/{server_id}/stop")


class TestRestore:
    async def test_restore_brings_the_world_back(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_server(admin, fake_server_dir)
        backup = await _backup_now(admin, server["id"])

        # Le monde est modifié après la sauvegarde.
        (fake_server_dir / "world" / "level.dat").write_bytes(b"niveau-casse")
        (fake_server_dir / "world" / "region" / "r.9.9.mca").write_bytes(b"region-recente")

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/backups/{backup['id']}/restore",
            json={"confirm": True},
        )

        assert response.status_code == 200, response.text
        assert (fake_server_dir / "world" / "level.dat").read_bytes() == b"niveau-origine"
        # Le monde est remplacé, pas fusionné : une région postérieure disparaît.
        assert not (fake_server_dir / "world" / "region" / "r.9.9.mca").exists()

    async def test_restore_requires_confirmation(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_server(admin, fake_server_dir)
        backup = await _backup_now(admin, server["id"])

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/backups/{backup['id']}/restore", json={}
        )

        assert response.status_code == 428
        assert response.json()["remediation"]

    async def test_restore_takes_a_safety_backup_first(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        """Une erreur de manipulation ne doit pas être définitive."""
        server = await _create_server(admin, fake_server_dir)
        backup = await _backup_now(admin, server["id"])
        (fake_server_dir / "world" / "level.dat").write_bytes(b"niveau-a-preserver")

        await admin.post(
            f"/api/v1/servers/{server['id']}/backups/{backup['id']}/restore",
            json={"confirm": True},
        )

        listing = (await admin.get(f"/api/v1/servers/{server['id']}/backups")).json()
        safety = [item for item in listing if item["kind"] == "pre-restore"]
        assert len(safety) == 1
        assert safety[0]["status"] == "COMPLETED"

    async def test_running_server_cannot_be_restored(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_server(admin, fake_server_dir)
        backup = await _backup_now(admin, server["id"])
        await admin.post(f"/api/v1/servers/{server['id']}/start")

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/backups/{backup['id']}/restore",
            json={"confirm": True},
        )

        assert response.status_code == 409
        assert response.json()["remediation"]
        assert (fake_server_dir / "world" / "level.dat").read_bytes() == b"niveau-origine"

        await admin.post(f"/api/v1/servers/{server['id']}/stop")


class TestDelete:
    async def test_delete_removes_the_archive(
        self, admin: ApiClient, fake_server_dir: Path, api_settings
    ) -> None:
        server = await _create_server(admin, fake_server_dir)
        backup = await _backup_now(admin, server["id"])
        archives = list(api_settings.backups_root.glob("*.tar.gz"))
        assert len(archives) == 1

        response = await admin.delete(f"/api/v1/servers/{server['id']}/backups/{backup['id']}")

        assert response.status_code == 200
        assert not archives[0].exists()
        assert (await admin.get(f"/api/v1/servers/{server['id']}/backups")).json() == []


class TestPermissions:
    async def test_moderator_cannot_back_up(
        self, admin: ApiClient, moderator: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_server(admin, fake_server_dir)

        response = await moderator.post(f"/api/v1/servers/{server['id']}/backups")

        assert response.status_code == 403
        assert response.json()["remediation"]

    async def test_viewer_cannot_download(
        self, admin: ApiClient, viewer: ApiClient, fake_server_dir: Path
    ) -> None:
        """Une archive emporte tout le monde : la lire n'est pas anodin."""
        server = await _create_server(admin, fake_server_dir)
        backup = await _backup_now(admin, server["id"])

        response = await viewer.get(
            f"/api/v1/servers/{server['id']}/backups/{backup['id']}/download"
        )

        assert response.status_code == 403


class TestMetrics:
    async def test_history_is_empty_before_any_sample(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_server(admin, fake_server_dir)

        response = await admin.get(f"/api/v1/servers/{server['id']}/metrics")

        assert response.status_code == 200
        body = response.json()
        assert body["points"] == []
        assert body["range"] == "24h"

    async def test_samples_are_aggregated_by_bucket(
        self, admin: ApiClient, fake_server_dir: Path, app
    ) -> None:
        from datetime import UTC, datetime, timedelta

        from msm.db.models.metrics import MetricSample
        from msm.db.session import session_scope

        server = await _create_server(admin, fake_server_dir)
        now = datetime.now(UTC)
        async with session_scope() as session:
            # Six points sur trois minutes : le palier d'« 1h » fait une minute.
            for index in range(6):
                session.add(
                    MetricSample(
                        server_id=server["id"],
                        ts=now - timedelta(seconds=30 * index),
                        cpu_percent=10.0 * index,
                        memory_mb=100.0,
                        players_online=index,
                        online=True,
                    )
                )

        body = (
            await admin.get(f"/api/v1/servers/{server['id']}/metrics", params={"range": "1h"})
        ).json()

        assert 0 < len(body["points"]) <= 6
        # L'agrégation retient la pointe : c'est ce qu'on cherche sur une courbe.
        assert body["peak_cpu_percent"] == 50.0
        assert body["peak_players"] == 5

    async def test_unknown_range_is_refused_with_the_valid_ones(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create_server(admin, fake_server_dir)

        response = await admin.get(
            f"/api/v1/servers/{server['id']}/metrics", params={"range": "1an"}
        )

        assert response.status_code == 422
        assert "24h" in response.json()["remediation"]
