"""Tests bout en bout de l'intégration avec le serveur de fichiers d'un launcher.

Le serveur de fichiers est simulé par un transport httpx : il sert un manifest
au format FrankuMC, les fichiers annoncés, et enregistre les états reçus sur
`PUT /msm/state`.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from fastapi import FastAPI

from tests.integration.conftest import ApiClient, fake_server_payload, share

pytestmark = pytest.mark.asyncio

BASE = "https://files.test"
TOKEN = "jeton-du-serveur-de-fichiers"
_SERIAL = itertools.count()


def _jar(environment: str) -> bytes:
    """Un JAR Fabric minimal, distinct pour chaque appel."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("fabric.mod.json", json.dumps({"environment": environment}))
        archive.writestr("id.txt", str(next(_SERIAL)))
    return buffer.getvalue()


class FakeFileServer:
    """Serveur de fichiers minimal, conforme au protocole v1."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.extra: dict[str, Any] = {}
        self.raw_manifest: Any = None
        self.pushes: list[dict[str, Any]] = []
        self.push_status = 204
        self.tokens: list[str] = []

    def put(self, path: str, data: bytes) -> None:
        self.files[path] = data

    def manifest(self) -> Any:
        if self.raw_manifest is not None:
            return self.raw_manifest
        return {
            "packVersion": "1.0.0",
            "mcVersion": "1.21.1",
            "fabricVersion": "0.16.0",
            "files": [
                {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                for path, data in sorted(self.files.items())
            ],
            **self.extra,
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/manifest.json":
            return httpx.Response(200, json=self.manifest())
        if request.method == "GET" and path.startswith("/files/"):
            data = self.files.get(unquote(path.removeprefix("/files/")))
            return httpx.Response(200, content=data) if data is not None else httpx.Response(404)
        if request.method == "PUT" and path == "/msm/state":
            self.tokens.append(request.headers.get("Authorization", ""))
            if self.push_status < 300:
                self.pushes.append(json.loads(request.content))
            return httpx.Response(self.push_status)
        return httpx.Response(404)


@pytest.fixture
def file_server(app: FastAPI) -> FakeFileServer:
    fake = FakeFileServer()
    app.state.launcher_syncer.transport = httpx.MockTransport(fake.handler)
    return fake


async def _server(admin: ApiClient, directory: Path) -> dict[str, Any]:
    created = await admin.post("/api/v1/servers", json=fake_server_payload("survie", directory))
    assert created.status_code == 201, created.text
    return created.json()


async def _configure(admin: ApiClient, server_id: int, **extra: Any) -> dict[str, Any]:
    response = await admin.put(
        f"/api/v1/servers/{server_id}/launcher",
        json={"file_server_url": BASE, "interval_minutes": 30, **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _sync(admin: ApiClient, server_id: int, **payload: Any) -> dict[str, Any]:
    response = await admin.post(f"/api/v1/servers/{server_id}/launcher/sync", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def _wait_state(admin: ApiClient, server_id: int, state: str, timeout: float = 20.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        response = await admin.get(f"/api/v1/servers/{server_id}/status")
        if response.json().get("state") == state:
            return True
        await asyncio.sleep(0.05)
    return False


class TestConfiguration:
    async def test_no_integration_answers_204(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _server(admin, fake_server_dir)

        response = await admin.get(f"/api/v1/servers/{server['id']}/launcher")

        assert response.status_code == 204

    async def test_token_is_never_returned(
        self, admin: ApiClient, fake_server_dir: Path, file_server: FakeFileServer
    ) -> None:
        server = await _server(admin, fake_server_dir)

        body = await _configure(admin, server["id"], push_token=TOKEN)

        assert body["push_configured"] is True
        assert body["push_token_hint"] == f"…{TOKEN[-4:]}"
        assert TOKEN not in json.dumps(body)
        assert body["next_sync_at"] is not None
        assert body["sync_paths"] == ["mods/"]

    async def test_plain_http_on_the_internet_is_refused(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _server(admin, fake_server_dir)

        response = await admin.put(
            f"/api/v1/servers/{server['id']}/launcher",
            json={"file_server_url": "http://frankumc.frankulin.fr"},
        )

        assert response.status_code == 422
        assert response.json()["remediation"]

    async def test_viewer_cannot_configure(
        self, admin: ApiClient, viewer: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _server(admin, fake_server_dir)
        await share(admin, server["id"], "lecteur", "VIEWER")

        response = await viewer.put(
            f"/api/v1/servers/{server['id']}/launcher", json={"file_server_url": BASE}
        )

        assert response.status_code == 403


class TestSynchronization:
    async def test_stopped_server_receives_the_mods_but_not_client_ones(
        self, admin: ApiClient, fake_server_dir: Path, file_server: FakeFileServer
    ) -> None:
        file_server.put("mods/commun.jar", _jar("*"))
        file_server.put("mods/minimap.jar", _jar("client"))
        file_server.put("config/options.txt", b"hors des dossiers synchronises")
        server = await _server(admin, fake_server_dir)
        await _configure(admin, server["id"])

        body = await _sync(admin, server["id"])

        mods = fake_server_dir / "mods"
        assert body["last_sync_status"] == "APPLIED", body["last_sync_error"]
        assert (mods / "commun.jar").read_bytes() == file_server.files["mods/commun.jar"]
        assert not (mods / "minimap.jar").exists()
        assert not (fake_server_dir / "config" / "options.txt").exists()
        assert body["last_sync_summary"]["client_only"] == 1
        assert body["pack_version"] == "1.0.0"
        sides = {
            mod["path"]: (mod["side"], mod["side_source"], mod["on_server"]) for mod in body["mods"]
        }
        assert sides == {
            "mods/commun.jar": ("both", "detected", "enabled"),
            "mods/minimap.jar": ("client", "detected", "absent"),
        }

    async def test_second_sync_is_up_to_date(
        self, admin: ApiClient, fake_server_dir: Path, file_server: FakeFileServer
    ) -> None:
        file_server.put("mods/commun.jar", _jar("*"))
        server = await _server(admin, fake_server_dir)
        await _configure(admin, server["id"])
        await _sync(admin, server["id"])

        body = await _sync(admin, server["id"])

        assert body["last_sync_status"] == "UP_TO_DATE"

    async def test_removed_mod_is_deleted_but_manual_mods_are_kept(
        self, admin: ApiClient, fake_server_dir: Path, file_server: FakeFileServer
    ) -> None:
        file_server.put("mods/a.jar", _jar("*"))
        file_server.put("mods/b.jar", _jar("*"))
        server = await _server(admin, fake_server_dir)
        await _configure(admin, server["id"])
        await _sync(admin, server["id"])
        (fake_server_dir / "mods" / "ajoute-a-la-main.jar").write_bytes(b"perso")

        del file_server.files["mods/b.jar"]
        body = await _sync(admin, server["id"])

        mods = fake_server_dir / "mods"
        assert body["last_sync_status"] == "APPLIED"
        assert (mods / "a.jar").exists()
        assert not (mods / "b.jar").exists()
        assert (mods / "ajoute-a-la-main.jar").read_bytes() == b"perso"

    async def test_mass_deletion_is_blocked_until_confirmed(
        self, admin: ApiClient, fake_server_dir: Path, file_server: FakeFileServer
    ) -> None:
        for index in range(8):
            file_server.put(f"mods/mod{index}.jar", _jar("*"))
        server = await _server(admin, fake_server_dir)
        await _configure(admin, server["id"])
        await _sync(admin, server["id"])

        file_server.files.clear()  # manifest régénéré à vide par erreur
        blocked = await _sync(admin, server["id"])

        assert blocked["last_sync_status"] == "BLOCKED"
        assert len(list((fake_server_dir / "mods").glob("*.jar"))) == 8

        confirmed = await _sync(admin, server["id"], allow_mass_delete=True)

        assert confirmed["last_sync_status"] == "APPLIED"
        assert not list((fake_server_dir / "mods").glob("*.jar"))

    async def test_invalid_manifest_touches_nothing(
        self, admin: ApiClient, fake_server_dir: Path, file_server: FakeFileServer
    ) -> None:
        file_server.put("mods/a.jar", _jar("*"))
        server = await _server(admin, fake_server_dir)
        await _configure(admin, server["id"])
        await _sync(admin, server["id"])

        file_server.raw_manifest = {
            "files": [{"path": "../../evil.jar", "sha256": "a" * 64, "size": 1}]
        }
        body = await _sync(admin, server["id"])

        assert body["last_sync_status"] == "FAILED"
        assert body["last_sync_error"]
        assert (fake_server_dir / "mods" / "a.jar").exists()

    async def test_side_override_excludes_a_mod(
        self, admin: ApiClient, fake_server_dir: Path, file_server: FakeFileServer
    ) -> None:
        """Un mod mal déclaré, qui ferait planter le serveur, peut être écarté."""
        file_server.put("mods/mal-declare.jar", _jar("*"))
        server = await _server(admin, fake_server_dir)
        await _configure(admin, server["id"])
        await _sync(admin, server["id"])

        response = await admin.put(
            f"/api/v1/servers/{server['id']}/launcher/side",
            json={"path": "mods/mal-declare.jar", "side": "client"},
        )
        assert response.status_code == 200, response.text
        body = await _sync(admin, server["id"])

        assert not (fake_server_dir / "mods" / "mal-declare.jar").exists()
        assert body["mods"][0]["side_source"] == "override"

    async def test_running_server_is_updated_at_next_start(
        self, admin: ApiClient, fake_server_dir: Path, file_server: FakeFileServer
    ) -> None:
        server = await _server(admin, fake_server_dir)
        await _configure(admin, server["id"])
        started = await admin.post(f"/api/v1/servers/{server['id']}/start")
        assert started.status_code == 200, started.text
        assert await _wait_state(admin, server["id"], "ONLINE")

        file_server.put("mods/nouveau.jar", _jar("*"))
        body = await _sync(admin, server["id"])

        assert body["last_sync_status"] == "PENDING_RESTART"
        assert body["pending"]["installs"] == 1
        assert not (fake_server_dir / "mods" / "nouveau.jar").exists()

        await admin.post(f"/api/v1/servers/{server['id']}/stop")
        assert await _wait_state(admin, server["id"], "OFFLINE")
        # Arrêt puis démarrage immédiat : c'est le point d'accroche qui applique,
        # pas la boucle de fond.
        restarted = await admin.post(f"/api/v1/servers/{server['id']}/start")
        assert restarted.status_code == 200, restarted.text

        assert (fake_server_dir / "mods" / "nouveau.jar").exists()
        # La tenue des registres suit le démarrage, en tâche de fond.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5
        state = (await admin.get(f"/api/v1/servers/{server['id']}/launcher")).json()
        while state["pending"] is not None and loop.time() < deadline:
            await asyncio.sleep(0.05)
            state = (await admin.get(f"/api/v1/servers/{server['id']}/launcher")).json()
        assert state["pending"] is None
        assert state["last_sync_status"] == "APPLIED"
        await admin.post(f"/api/v1/servers/{server['id']}/stop")


class TestPublication:
    async def test_toggling_a_mod_publishes_the_disabled_list(
        self, admin: ApiClient, fake_server_dir: Path, file_server: FakeFileServer
    ) -> None:
        file_server.put("mods/a.jar", _jar("*"))
        server = await _server(admin, fake_server_dir)
        await _configure(admin, server["id"], push_token=TOKEN)
        await _sync(admin, server["id"])
        assert file_server.pushes[-1]["disabledFiles"] == []

        toggled = await admin.post(
            f"/api/v1/servers/{server['id']}/files/mods/a.jar/toggle", json={"enabled": False}
        )
        assert toggled.status_code == 200, toggled.text

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5
        while loop.time() < deadline and file_server.pushes[-1]["disabledFiles"] == []:
            await asyncio.sleep(0.05)

        push = file_server.pushes[-1]
        assert push == {
            "protocol": 1,
            "server": "survie",
            "revision": push["revision"],
            "disabledFiles": ["mods/a.jar"],
        }
        assert push["revision"] > file_server.pushes[0]["revision"]
        assert file_server.tokens[-1] == f"Bearer {TOKEN}"

        state = (await admin.get(f"/api/v1/servers/{server['id']}/launcher")).json()
        assert state["publish"]["up_to_date"] is True
        assert state["publish"]["disabled_files"] == ["mods/a.jar"]

    async def test_disabled_mod_stays_on_the_server_when_published_upstream(
        self, admin: ApiClient, fake_server_dir: Path, file_server: FakeFileServer
    ) -> None:
        """Le serveur de fichiers range le mod dans `disabledFiles` : ce n'est pas un retrait."""
        data = _jar("*")
        file_server.put("mods/a.jar", data)
        server = await _server(admin, fake_server_dir)
        await _configure(admin, server["id"], push_token=TOKEN)
        await _sync(admin, server["id"])
        await admin.post(
            f"/api/v1/servers/{server['id']}/files/mods/a.jar/toggle", json={"enabled": False}
        )

        # Ce que fait la route de référence à la réception de l'état.
        del file_server.files["mods/a.jar"]
        file_server.extra["disabledFiles"] = [
            {"path": "mods/a.jar", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        ]
        body = await _sync(admin, server["id"])

        assert body["last_sync_status"] == "UP_TO_DATE", body["last_sync_error"]
        assert (fake_server_dir / "mods" / "a.jar.disabled").exists()

    async def test_refused_token_is_reported(
        self, admin: ApiClient, fake_server_dir: Path, file_server: FakeFileServer
    ) -> None:
        file_server.push_status = 401
        server = await _server(admin, fake_server_dir)
        await _configure(admin, server["id"], push_token="mauvais-jeton")

        response = await admin.post(f"/api/v1/servers/{server['id']}/launcher/publish")

        assert response.status_code == 200, response.text
        publish = response.json()["publish"]
        assert publish["up_to_date"] is False
        assert "Token refused" in publish["last_push_error"]

    async def test_without_token_nothing_is_sent(
        self, admin: ApiClient, fake_server_dir: Path, file_server: FakeFileServer
    ) -> None:
        file_server.put("mods/a.jar", _jar("*"))
        server = await _server(admin, fake_server_dir)
        await _configure(admin, server["id"])

        body = await _sync(admin, server["id"])

        assert file_server.pushes == []
        assert body["publish"]["up_to_date"] is False
