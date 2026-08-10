"""Tests de la réadoption d'un serveur survivant à un redémarrage de MSM.

Le scénario réel est reproduit fidèlement : un serveur est démarré, MSM le
« détache » comme il le ferait à son arrêt, puis un runtime tout neuf — comme au
redémarrage suivant — reprend la main sur le même processus.
"""

from __future__ import annotations

import asyncio
import os
import sys

import psutil
import pytest

from msm.core.states import ServerState
from msm.exceptions import ServerAlreadyRunning
from msm.runtime.process_handle import StopStage
from tests.conftest import wait_for

pytestmark = pytest.mark.asyncio


async def _start_online(runtime, timeout: float = 20.0) -> None:
    await runtime.start(actor="test")
    assert await wait_for(lambda: runtime.state is ServerState.ONLINE, timeout=timeout)


def _alive(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


class TestAdoption:
    async def test_running_server_is_readopted(self, make_runtime, server_dir) -> None:
        """Le scénario complet : démarrage, arrêt de MSM, redémarrage, réadoption."""
        first = make_runtime("--heartbeat", "0.3", server_id=1, directory=server_dir)
        await _start_online(first)

        pid = first.pid
        create_time = first.process_create_time
        group_id = first.group_id
        assert pid is not None

        # MSM s'arrête : les serveurs ne sont pas tués, seulement détachés.
        await first.detach()
        assert _alive(pid), "Le serveur ne doit pas mourir avec le panneau"

        # MSM redémarre : un runtime neuf reprend le même processus.
        second = make_runtime(server_id=1, directory=server_dir)
        assert await second.adopt(pid, group_id=group_id, create_time=create_time)

        assert second.state is ServerState.UNKNOWN
        assert second.adopted is True
        assert second.pid == pid
        snapshot = second.snapshot()
        assert snapshot["adopted"] is True
        assert snapshot["console_writable"] is False

        await second.kill(actor="test")
        assert await wait_for(lambda: not _alive(pid), timeout=15.0)

    async def test_adoption_explains_the_situation_in_the_console(
        self, make_runtime, server_dir
    ) -> None:
        first = make_runtime(server_id=2, directory=server_dir)
        await _start_online(first)
        pid, create_time = first.pid, first.process_create_time
        await first.detach()

        second = make_runtime(server_id=2, directory=server_dir)
        await second.adopt(pid, create_time=create_time)

        texts = [line.text for line in second.logs_tail(20)]
        assert any("réadopté" in text for text in texts)
        assert any("lecture seule" in text for text in texts)

        await second.kill(actor="test")

    async def test_dead_process_is_not_adopted(self, make_runtime, server_dir) -> None:
        first = make_runtime(server_id=3, directory=server_dir)
        await _start_online(first)
        pid, create_time = first.pid, first.process_create_time
        await first.stop(actor="test")
        assert await wait_for(lambda: not _alive(pid))

        second = make_runtime(server_id=3, directory=server_dir)

        assert await second.adopt(pid, create_time=create_time) is False
        assert second.state is ServerState.OFFLINE

    async def test_recycled_pid_is_refused(self, make_runtime, server_dir) -> None:
        """Un PID réattribué à un autre programme ne doit jamais être adopté.

        C'est exactement la confusion qui rendait `pkill` dangereux : le PID
        seul ne suffit pas à identifier un processus.
        """
        runtime = make_runtime(server_id=4, directory=server_dir)

        # Le PID de ce processus de test existe, mais sa date de création ne
        # correspond évidemment pas à celle annoncée.
        assert await runtime.adopt(os.getpid(), create_time=1.0) is False
        assert runtime.state is ServerState.OFFLINE

    async def test_starting_an_adopted_server_is_refused(self, make_runtime, server_dir) -> None:
        first = make_runtime(server_id=5, directory=server_dir)
        await _start_online(first)
        pid, create_time = first.pid, first.process_create_time
        await first.detach()

        second = make_runtime(server_id=5, directory=server_dir)
        await second.adopt(pid, create_time=create_time)

        with pytest.raises(ServerAlreadyRunning) as excinfo:
            await second.start(actor="test")
        assert str(pid) in (excinfo.value.cause or "")

        await second.kill(actor="test")

    async def test_liveness_watcher_notices_an_external_stop(
        self, make_runtime, server_dir
    ) -> None:
        """Le serveur peut être arrêté depuis l'extérieur : l'état doit suivre."""
        first = make_runtime(server_id=6, directory=server_dir)
        await _start_online(first)
        pid, create_time = first.pid, first.process_create_time
        await first.detach()

        second = make_runtime(server_id=6, directory=server_dir)
        await second.adopt(pid, create_time=create_time)

        psutil.Process(pid).kill()

        assert await wait_for(lambda: second.state is ServerState.OFFLINE, timeout=15.0)
        assert second.adopted is False

    @pytest.mark.skipif(sys.platform == "win32", reason="pas de SIGTERM pour une JVM Windows")
    async def test_graceful_stop_of_an_adopted_server(self, make_runtime, server_dir) -> None:
        """Sous POSIX, SIGTERM déclenche l'arrêt propre du serveur."""
        first = make_runtime(server_id=7, directory=server_dir)
        await _start_online(first)
        pid, group_id, create_time = first.pid, first.group_id, first.process_create_time
        await first.detach()

        second = make_runtime(server_id=7, directory=server_dir)
        await second.adopt(pid, group_id=group_id, create_time=create_time)

        outcome = await second.stop(actor="test")

        assert outcome.stage is StopStage.SIGNAL
        assert outcome.forced is False
        assert await wait_for(lambda: not _alive(pid), timeout=15.0)

    async def test_adopting_one_server_does_not_touch_another(self, make_runtime, tmp_path) -> None:
        """L'isolation vaut aussi pour les processus réadoptés."""
        first_dir = tmp_path / "a"
        second_dir = tmp_path / "b"
        first_dir.mkdir()
        second_dir.mkdir()

        alpha = make_runtime(server_id=8, name="alpha", directory=first_dir)
        beta = make_runtime(server_id=9, name="beta", directory=second_dir)
        await _start_online(alpha)
        await _start_online(beta)

        alpha_pid, alpha_time = alpha.pid, alpha.process_create_time
        beta_pid = beta.pid
        await alpha.detach()

        readopted = make_runtime(server_id=8, name="alpha", directory=first_dir)
        await readopted.adopt(alpha_pid, group_id=alpha.group_id, create_time=alpha_time)
        await readopted.kill(actor="test")

        assert await wait_for(lambda: not _alive(alpha_pid), timeout=15.0)
        assert _alive(beta_pid), "Le second serveur ne doit pas être affecté"
        assert beta.state is ServerState.ONLINE

        await beta.stop(actor="test")


class TestLogTailing:
    async def test_adopted_server_reads_its_log_file(self, make_runtime, server_dir) -> None:
        """Sans tubes, `logs/latest.log` est la seule fenêtre restante."""
        logs = server_dir / "logs"
        logs.mkdir()
        log_file = logs / "latest.log"
        log_file.write_text("[12:00:00] [Server thread/INFO]: ancienne ligne\n", encoding="utf-8")

        first = make_runtime(server_id=10, directory=server_dir)
        await _start_online(first)
        pid, create_time = first.pid, first.process_create_time
        await first.detach()

        second = make_runtime(server_id=10, directory=server_dir)
        await second.adopt(pid, create_time=create_time)

        # La lecture démarre à la fin du fichier : les lignes déjà présentes ne
        # sont pas rejouées, seules les nouvelles apparaissent.
        await asyncio.sleep(1.0)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write("[12:00:05] [Server thread/INFO]: Flavien joined the game\n")

        assert await wait_for(
            lambda: any("joined the game" in line.text for line in second.logs_tail(50)),
            timeout=15.0,
        )
        assert not any("ancienne ligne" in line.text for line in second.logs_tail(50))
        assert "Flavien" in second.online_players

        await second.kill(actor="test")
