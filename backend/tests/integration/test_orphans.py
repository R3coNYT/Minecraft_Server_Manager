"""Serveurs perdus de vue : MSM redémarré en plein démarrage d'un serveur.

Le cas réel : un modpack met plusieurs minutes à charger, MSM est mis à jour
pendant ce temps. Le PID n'était enregistré qu'une fois le serveur « en ligne » :
au redémarrage, MSM ne le réadoptait pas, et la relance suivante échouait sur le
verrou du monde (`session.lock`) tenu par l'instance orpheline.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI

from msm.bus import EventBus, topics
from msm.core.states import ServerState
from msm.db.session import session_scope
from msm.exceptions import ServerAlreadyRunning
from msm.runtime.orphans import find_server_process
from msm.services.server_service import ServerService
from tests.conftest import wait_for
from tests.integration.conftest import ApiClient, fake_server_payload

_SLEEP = "import time; time.sleep(120)"


@pytest.fixture
def spawn() -> Iterator:
    """Lance des processus dormants, tués en fin de test quoi qu'il arrive."""
    started: list[subprocess.Popen[bytes]] = []

    def factory(cwd: Path, *extra: str) -> subprocess.Popen[bytes]:
        process = subprocess.Popen([sys.executable, "-c", _SLEEP, *extra], cwd=cwd)
        started.append(process)
        return process

    yield factory

    for process in started:
        process.kill()
        process.wait(timeout=10)


def _found_pid(directory: Path) -> int | None:
    found = find_server_process(directory)
    return found.pid if found else None


class TestFindServerProcess:
    def test_a_jar_running_in_the_folder_is_found(self, tmp_path: Path, spawn) -> None:
        process = spawn(tmp_path, "server.jar", "nogui")

        assert wait_for_sync(lambda: _found_pid(tmp_path) == process.pid)
        found = find_server_process(tmp_path)
        assert found is not None and found.create_time > 0

    def test_a_server_in_another_folder_is_ignored(self, tmp_path: Path, spawn) -> None:
        elsewhere = tmp_path / "other"
        elsewhere.mkdir()
        mine = tmp_path / "mine"
        mine.mkdir()
        spawn(elsewhere, "server.jar")

        assert wait_for_sync(lambda: _found_pid(elsewhere) is not None)
        assert find_server_process(mine) is None

    def test_a_process_that_is_not_a_server_is_ignored(self, tmp_path: Path, spawn) -> None:
        """Un shell ou un éditeur ouvert dans le dossier n'est pas un serveur."""
        process = spawn(tmp_path)

        # Laisse au processus le temps d'exister, puis vérifie qu'il est écarté.
        assert wait_for_sync(lambda: process.poll() is None)
        assert find_server_process(tmp_path) is None


def wait_for_sync(condition, timeout: float = 10.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return condition()


@pytest.mark.asyncio
class TestRuntime:
    async def test_the_pid_is_published_as_soon_as_the_process_is_spawned(
        self, make_runtime, server_dir, bus: EventBus
    ) -> None:
        """C'est ce qui permet de réadopter un serveur encore en chargement."""
        subscription = bus.subscribe(topics.server_topic(1, topics.STATUS))
        runtime = make_runtime("--startup-delay", "3", "--heartbeat", "0.3", server_id=1)

        await runtime.start(actor="test")

        async def first_starting_pid() -> int:
            while True:
                event = await subscription.get()
                payload = event.payload
                if payload["state"] == ServerState.STARTING.value and payload["pid"]:
                    return int(payload["pid"])

        # Le faux serveur met 3 s à « charger » : le PID doit arriver avant.
        assert await asyncio.wait_for(first_starting_pid(), timeout=2.5) == runtime.pid
        assert runtime.state is ServerState.STARTING

        await runtime.kill(actor="test")

    async def test_starting_over_an_orphan_is_refused(
        self, make_runtime, server_dir: Path, spawn
    ) -> None:
        orphan = spawn(server_dir, "server.jar", "nogui")
        assert wait_for_sync(lambda: _found_pid(server_dir) == orphan.pid)
        runtime = make_runtime(server_id=2)

        with pytest.raises(ServerAlreadyRunning) as excinfo:
            await runtime.start(actor="test")

        assert str(orphan.pid) in (excinfo.value.cause or "")
        assert f"kill {orphan.pid}" in (excinfo.value.remediation or "")
        assert runtime.state is ServerState.OFFLINE


@pytest.mark.asyncio
async def test_msm_readopts_an_orphan_found_in_the_server_folder(
    app: FastAPI, admin: ApiClient, fake_server_dir: Path, spawn
) -> None:
    """Sans PID en base, le serveur est retrouvé par son dossier au démarrage de MSM."""
    created = await admin.post(
        "/api/v1/servers", json=fake_server_payload("modpack", fake_server_dir)
    )
    server_id = created.json()["id"]
    orphan = spawn(fake_server_dir, "server.jar", "nogui")
    assert wait_for_sync(lambda: _found_pid(fake_server_dir) == orphan.pid)

    async with session_scope() as session:
        service = ServerService(session, app.state.settings, app.state.supervisor)
        assert await service.adopt_running() == 1

    runtime = app.state.supervisor.find(server_id)
    assert runtime.pid == orphan.pid
    assert runtime.state is ServerState.UNKNOWN

    await runtime.kill(actor="test")
    assert await wait_for(lambda: runtime.state is ServerState.OFFLINE, timeout=15.0)
