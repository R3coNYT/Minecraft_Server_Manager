"""Tests bout en bout de la création de serveurs de zéro.

Le réseau est remplacé : la « source » renvoie un faux JAR, et l'installeur de
NeoForge est simulé. Tout le reste est réel — dossier, fichiers écrits,
enregistrement, droits, nettoyage en cas d'échec.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from msm.config import Settings
from msm.downloads.sources import DownloadTarget
from msm.exceptions import ValidationError
from tests.integration.conftest import ApiClient

pytestmark = pytest.mark.asyncio


@pytest.fixture
def servers_root(tmp_path: Path) -> Path:
    root = tmp_path / "servers"
    root.mkdir()
    return root


@pytest.fixture
def api_settings(tmp_path: Path, servers_root: Path) -> Settings:
    """Comme en production : les serveurs vivent sous une racine autorisée."""
    return Settings(
        environment="test",
        secret_key="cle-de-test-suffisamment-longue-pour-la-validation-0123456789",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        cors_origins=[],
        server_roots=[servers_root],
    )


@pytest.fixture
def fake_network(monkeypatch: pytest.MonkeyPatch, java_on_path: Path) -> dict[str, Any]:
    """Sources et téléchargements simulés ; `state` pilote les échecs."""
    from msm.services import provisioning_service

    state: dict[str, Any] = {"fail_download": False, "installed": []}

    async def fake_resolve(source: str, version: str, build: str | None = None) -> DownloadTarget:
        kind = "installer" if source == "neoforge" else "jar"
        return DownloadTarget(
            url=f"https://example.invalid/{source}-{version}.jar",
            filename=f"{source}-{version}.jar",
            checksum=None,
            algorithm=None,
            kind=kind,
        )

    async def fake_download(target: DownloadTarget, destination: Path, **kwargs: Any) -> None:
        if state["fail_download"]:
            raise ValidationError(
                "Download interrupted.", cause="réseau coupé", remediation="réessayer"
            )
        on_progress = kwargs.get("on_progress")
        destination.write_bytes(b"faux jar")
        if on_progress:
            on_progress(8, 8)

    async def fake_installer(installer: Path, directory: Path, **_: Any) -> Path:
        script = directory / ("run.bat" if sys.platform == "win32" else "run.sh")
        script.write_text("echo NeoForge\n")
        script.chmod(0o755)
        (directory / "user_jvm_args.txt").write_text("# fourni par NeoForge\n")
        installer.unlink()
        state["installed"].append(directory)
        return script

    monkeypatch.setattr(provisioning_service, "resolve", fake_resolve)
    monkeypatch.setattr(provisioning_service, "download_file", fake_download)
    monkeypatch.setattr(provisioning_service, "run_installer", fake_installer)
    monkeypatch.setattr(provisioning_service, "find_java", lambda: "java")
    return state


def _payload(directory: Path, **overrides: Any) -> dict[str, Any]:
    payload = {
        "name": "survie",
        "directory": str(directory),
        "distribution": "paper",
        "version": "1.21.1",
        "memory_min_mb": 1024,
        "memory_max_mb": 4096,
        "port": 25570,
        "accept_eula": True,
    }
    payload.update(overrides)
    return payload


async def _wait_job(admin: ApiClient, job_id: str, timeout: float = 20.0) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        job = (await admin.get(f"/api/v1/provisioning/{job_id}")).json()
        if job["status"] != "RUNNING":
            return job
        await asyncio.sleep(0.05)
    raise AssertionError("La création ne s'est pas terminée à temps.")


class TestChoices:
    async def test_every_type_is_offered(self, admin: ApiClient) -> None:
        body = (await admin.get("/api/v1/provisioning/distributions")).json()

        by_key = {item["key"]: item for item in body}
        assert set(by_key) == {
            "vanilla",
            "paper",
            "purpur",
            "fabric",
            "neoforge",
            "mohist",
            "youer",
        }
        assert by_key["neoforge"]["kind"] == "installer"
        assert by_key["fabric"]["has_builds"] is True
        assert by_key["paper"]["has_builds"] is False

    async def test_defaults_suggest_a_folder_under_the_root_and_a_free_port(
        self, admin: ApiClient, servers_root: Path
    ) -> None:
        body = (
            await admin.get("/api/v1/provisioning/defaults", params={"name": "Mini-jeux Été"})
        ).json()

        storage_id = (await admin.get("/api/v1/auth/me")).json()["storage_id"]
        assert body["roots"] == [str(servers_root)]
        # Proposé dans le dossier du compte ; un admin reste libre de le changer.
        assert Path(body["directory"]) == servers_root / storage_id / "mini-jeux-ete"
        assert body["directory_locked"] is False
        assert body["port"] == 25565

    async def test_every_account_may_create_servers(self, viewer: ApiClient) -> None:
        """Créer ses serveurs est le cœur du rôle user."""
        assert (await viewer.get("/api/v1/provisioning/distributions")).status_code == 200


class TestCreation:
    async def test_a_jar_server_is_created_configured_and_registered(
        self, admin: ApiClient, servers_root: Path, fake_network: dict[str, Any]
    ) -> None:
        directory = servers_root / "survie"

        response = await admin.post("/api/v1/provisioning", json=_payload(directory))

        assert response.status_code == 202, response.text
        job = await _wait_job(admin, response.json()["id"])
        assert job["status"] == "COMPLETED", job
        assert [step["status"] for step in job["steps"]] == ["done"] * len(job["steps"])
        assert (directory / "paper-1.21.1.jar").is_file()
        assert "eula=true" in (directory / "eula.txt").read_text()
        assert "server-port=25570" in (directory / "server.properties").read_text()

        server = (await admin.get(f"/api/v1/servers/{job['server_id']}")).json()
        assert server["name"] == "survie"
        assert server["server_type"] == "PAPER"
        assert server["launcher_key"] == "jar"
        assert server["settings"]["memory_max_mb"] == 4096
        assert server["settings"]["port"] == 25570

    async def test_without_eula_consent_nothing_is_accepted(
        self, admin: ApiClient, servers_root: Path, fake_network: dict[str, Any]
    ) -> None:
        directory = servers_root / "survie"

        response = await admin.post(
            "/api/v1/provisioning", json=_payload(directory, accept_eula=False)
        )

        job = await _wait_job(admin, response.json()["id"])
        assert job["status"] == "COMPLETED"
        assert not (directory / "eula.txt").exists()

    async def test_neoforge_runs_its_installer_and_starts_from_its_script(
        self, admin: ApiClient, servers_root: Path, fake_network: dict[str, Any]
    ) -> None:
        directory = servers_root / "frankumc"

        response = await admin.post(
            "/api/v1/provisioning",
            json=_payload(directory, name="frankumc", distribution="neoforge", build="21.1.77"),
        )

        job = await _wait_job(admin, response.json()["id"])
        assert job["status"] == "COMPLETED", job
        assert "install" in [step["key"] for step in job["steps"]]
        assert fake_network["installed"] == [directory]
        jvm_args = (directory / "user_jvm_args.txt").read_text().splitlines()
        assert "-Xms1024M" in jvm_args and "-Xmx4096M" in jvm_args

        server = (await admin.get(f"/api/v1/servers/{job['server_id']}")).json()
        assert server["server_type"] == "NEOFORGE"
        assert server["launcher_key"] == ("batch" if sys.platform == "win32" else "shell")

    async def test_a_failed_download_leaves_nothing_behind(
        self, admin: ApiClient, servers_root: Path, fake_network: dict[str, Any]
    ) -> None:
        fake_network["fail_download"] = True
        directory = servers_root / "survie"

        response = await admin.post("/api/v1/provisioning", json=_payload(directory))

        job = await _wait_job(admin, response.json()["id"])
        assert job["status"] == "FAILED"
        assert job["error"]["cause"] == "réseau coupé"
        assert job["error"]["remediation"]
        assert [step["status"] for step in job["steps"]][:2] == ["done", "failed"]
        assert not directory.exists()
        assert (await admin.get("/api/v1/servers")).json() == []

    async def test_an_existing_empty_folder_is_kept_after_a_failure(
        self, admin: ApiClient, servers_root: Path, fake_network: dict[str, Any]
    ) -> None:
        fake_network["fail_download"] = True
        directory = servers_root / "survie"
        directory.mkdir()

        response = await admin.post("/api/v1/provisioning", json=_payload(directory))

        await _wait_job(admin, response.json()["id"])
        assert directory.is_dir()
        assert list(directory.iterdir()) == []


class TestValidation:
    async def test_a_folder_with_files_is_refused(
        self, admin: ApiClient, servers_root: Path, fake_network: dict[str, Any]
    ) -> None:
        directory = servers_root / "survie"
        directory.mkdir()
        (directory / "world").mkdir()

        response = await admin.post("/api/v1/provisioning", json=_payload(directory))

        assert response.status_code == 409
        assert "Add an existing server" in response.json()["remediation"]

    async def test_a_folder_outside_the_roots_is_refused(
        self, admin: ApiClient, tmp_path: Path, fake_network: dict[str, Any]
    ) -> None:
        response = await admin.post("/api/v1/provisioning", json=_payload(tmp_path / "ailleurs"))

        assert response.status_code == 422

    async def test_a_taken_name_is_refused(
        self, admin: ApiClient, servers_root: Path, fake_network: dict[str, Any]
    ) -> None:
        first = await admin.post("/api/v1/provisioning", json=_payload(servers_root / "a"))
        await _wait_job(admin, first.json()["id"])

        response = await admin.post("/api/v1/provisioning", json=_payload(servers_root / "b"))

        assert response.status_code == 409

    @pytest.mark.parametrize(
        "overrides",
        [
            {"memory_min_mb": 4096, "memory_max_mb": 1024},
            {"memory_min_mb": 128},
            {"port": 70000},
            {"distribution": "bukkit"},
            {"version": "1.21; rm -rf /"},
        ],
    )
    async def test_invalid_requests_are_refused_before_anything_happens(
        self,
        admin: ApiClient,
        servers_root: Path,
        fake_network: dict[str, Any],
        overrides: dict[str, Any],
    ) -> None:
        directory = servers_root / "survie"

        response = await admin.post("/api/v1/provisioning", json=_payload(directory, **overrides))

        assert response.status_code == 422, response.text
        assert not directory.exists()

    async def test_an_unknown_job_is_reported(self, admin: ApiClient) -> None:
        response = await admin.get("/api/v1/provisioning/inconnu")

        assert response.status_code == 404


# --------------------------------------------------------------------------- #
#  Hébergement des comptes : dossiers, ports, quotas (ouverture au public, étape 3)
# --------------------------------------------------------------------------- #
async def _hosting(admin: ApiClient, **changes: Any) -> dict[str, Any]:
    current = (await admin.get("/api/v1/settings/hosting")).json()
    quota = {**current["quota"], **changes.pop("quota", {})}
    response = await admin.put(
        "/api/v1/settings/hosting", json={**current, **changes, "quota": quota}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _created(client: ApiClient, servers_root: Path, **overrides: Any) -> dict[str, Any]:
    response = await client.post("/api/v1/provisioning", json=_payload(servers_root, **overrides))
    assert response.status_code == 202, response.text
    job = await _wait_job(client, response.json()["id"])
    assert job["status"] == "COMPLETED", job
    return (await client.get(f"/api/v1/servers/{job['server_id']}")).json()


class TestHosting:
    async def test_a_user_creates_in_their_own_folder_on_an_allocated_port(
        self, viewer: ApiClient, servers_root: Path, fake_network: dict[str, Any]
    ) -> None:
        storage_id = (await viewer.get("/api/v1/auth/me")).json()["storage_id"]
        # Dossier et port demandés : ignorés, ce n'est pas au compte de les choisir.
        server = await _created(viewer, servers_root / "ailleurs", port=22)

        directory = servers_root / storage_id / "survie"
        assert Path(server["directory"]) == directory
        assert server["settings"]["port"] == 25565
        assert server["access"] == "OWNER"
        properties = (directory / "server.properties").read_text()
        for line in ("server-port=25565", "enable-rcon=false", "enable-query=false"):
            assert line in properties

    async def test_defaults_are_imposed_on_users(self, viewer: ApiClient) -> None:
        body = (await viewer.get("/api/v1/provisioning/defaults", params={"name": "x"})).json()
        assert body["directory_locked"] is True
        assert body["port_locked"] is True
        assert body["max_memory_mb"] == 4096

    async def test_each_server_gets_its_own_port(
        self, viewer: ApiClient, servers_root: Path, fake_network: dict[str, Any]
    ) -> None:
        first = await _created(viewer, servers_root, name="un")
        second = await _created(viewer, servers_root, name="deux")
        assert [first["settings"]["port"], second["settings"]["port"]] == [25565, 25566]

    async def test_the_server_count_is_limited(
        self, admin: ApiClient, viewer: ApiClient, servers_root: Path, fake_network: dict[str, Any]
    ) -> None:
        await _hosting(admin, quota={"max_servers": 1})
        await _created(viewer, servers_root, name="un")

        second = await viewer.post("/api/v1/provisioning", json=_payload(servers_root, name="deux"))
        assert second.status_code == 403
        assert second.json()["code"] == "QUOTA_EXCEEDED"

        # Surcharge pour ce compte : il peut en avoir davantage.
        users = (await admin.get("/api/v1/users")).json()
        user_id = next(user["id"] for user in users if user["username"] == "lecteur")
        raised = await admin.put(f"/api/v1/users/{user_id}/quota", json={"max_servers": 3})
        assert raised.status_code == 200, raised.text
        mine = (await viewer.get("/api/v1/auth/me/quota")).json()
        assert mine["quota"]["max_servers"] == 3
        assert mine["usage"]["servers"] == 1
        details = (await admin.get(f"/api/v1/users/{user_id}")).json()
        assert details["quota_overrides"] == {"max_servers": 3}

        third = await viewer.post("/api/v1/provisioning", json=_payload(servers_root, name="deux"))
        assert third.status_code == 202, third.text

    async def test_memory_per_server_is_limited(
        self, viewer: ApiClient, servers_root: Path, fake_network: dict[str, Any]
    ) -> None:
        response = await viewer.post(
            "/api/v1/provisioning", json=_payload(servers_root, memory_max_mb=8192)
        )
        assert response.status_code == 403
        assert response.json()["code"] == "QUOTA_EXCEEDED"

    async def test_owners_change_neither_the_port_nor_memory_beyond_their_quota(
        self, viewer: ApiClient, servers_root: Path, fake_network: dict[str, Any]
    ) -> None:
        server = await _created(viewer, servers_root)
        url = f"/api/v1/servers/{server['id']}"

        assert (await viewer.put(url, json={"settings": {"port": 22}})).status_code == 403
        too_much = await viewer.put(url, json={"settings": {"memory_max_mb": 8192}})
        assert too_much.status_code == 403
        fine = await viewer.put(url, json={"settings": {"memory_max_mb": 3072}})
        assert fine.status_code == 200, fine.text

    async def test_the_port_is_enforced_again_before_each_start(
        self, viewer: ApiClient, servers_root: Path, fake_network: dict[str, Any]
    ) -> None:
        from msm.services.hosting_service import make_network_hook

        server = await _created(viewer, servers_root)
        properties = Path(server["directory"]) / "server.properties"
        properties.write_text("motd=x\nserver-port=22\nenable-rcon=true\n")

        messages = await make_network_hook()(server["id"])

        assert messages
        content = properties.read_text()
        for line in ("server-port=25565", "enable-rcon=false", "enable-query=false", "motd=x"):
            assert line in content

    async def test_admin_servers_are_left_alone(
        self, admin: ApiClient, servers_root: Path, fake_network: dict[str, Any]
    ) -> None:
        from msm.services.hosting_service import make_network_hook

        server = await _created(admin, servers_root / "admin", port=25570)
        properties = Path(server["directory"]) / "server.properties"
        properties.write_text("server-port=25999\nenable-rcon=true\n")

        assert await make_network_hook()(server["id"]) == []
        assert "enable-rcon=true" in properties.read_text()

    async def test_the_admin_sets_the_hosting_rules(
        self, admin: ApiClient, viewer: ApiClient, servers_root: Path
    ) -> None:
        assert (await viewer.get("/api/v1/settings/hosting")).status_code == 403
        body = await _hosting(
            admin, port_min=30000, port_max=30010, users_root=str(servers_root / "comptes")
        )
        assert body["users_root_effective"] == str((servers_root / "comptes").resolve())

        refused = await admin.put(
            "/api/v1/settings/hosting", json={**body, "port_min": 30010, "port_max": 30000}
        )
        assert refused.status_code == 422
        outside = await admin.put(
            "/api/v1/settings/hosting", json={**body, "users_root": str(servers_root.parent)}
        )
        assert outside.status_code == 422

    async def test_a_full_disk_blocks_backups(
        self,
        viewer: ApiClient,
        servers_root: Path,
        fake_network: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from msm.services import hosting_service

        server = await _created(viewer, servers_root)

        async def full(self: Any, user: Any, owned: Any = None) -> float:
            return 1_000_000.0

        monkeypatch.setattr(hosting_service.HostingService, "disk_mb", full)
        backup = await viewer.post(f"/api/v1/servers/{server['id']}/backups", json={})
        assert backup.status_code == 403
        assert backup.json()["code"] == "QUOTA_EXCEEDED"
