"""Tests du gestionnaire de processus, avec de vrais processus.

Le test le plus important du dépôt est :func:`test_stop_only_affects_target_server`.
C'est lui qui garantit ce que l'ancienne version ne garantissait pas : arrêter un
serveur n'en touche aucun autre. Il doit rester vert sur Linux **et** sous Windows.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import psutil
import pytest

from msm.core.restart_policy import AutoRestartMode, RestartPolicy
from msm.core.states import ServerState
from msm.exceptions import (
    ConsoleUnavailable,
    LaunchError,
    ServerAlreadyRunning,
    ServerNotRunning,
    UnsafeCommandError,
)
from msm.launchers import LaunchContext
from msm.runtime.process_handle import StopStage
from msm.runtime.server_runtime import ServerRuntime, ServerRuntimeConfig
from tests.conftest import FAKE_SERVER, wait_for

pytestmark = pytest.mark.asyncio


async def _start_and_wait_online(runtime, timeout: float = 20.0) -> None:
    await runtime.start(actor="test")
    assert await wait_for(lambda: runtime.state is ServerState.ONLINE, timeout=timeout), (
        f"Le serveur n'est pas passé en ligne (état : {runtime.state.value})"
    )


# --------------------------------------------------------------------------- #
#  Démarrage
# --------------------------------------------------------------------------- #
async def test_start_reaches_online(make_runtime) -> None:
    runtime = make_runtime()
    await _start_and_wait_online(runtime)

    assert runtime.pid is not None
    assert runtime.snapshot()["console_writable"] is True

    await runtime.stop(actor="test")


async def test_start_twice_is_refused(make_runtime) -> None:
    runtime = make_runtime()
    await _start_and_wait_online(runtime)

    with pytest.raises(ServerAlreadyRunning):
        await runtime.start(actor="test")

    await runtime.stop(actor="test")


async def test_start_failure_reports_cause_and_returns_offline(server_dir, bus) -> None:
    """Un démarrage impossible laisse le serveur hors ligne, avec une cause lisible."""
    config = ServerRuntimeConfig(
        id=99,
        name="serveur-casse",
        directory=server_dir,
        launcher_key="custom",
        launch=LaunchContext(
            name="serveur-casse",
            directory=server_dir,
            custom_argv=("programme-qui-nexiste-pas-msm",),
        ),
    )
    runtime = ServerRuntime(config, bus=bus)

    with pytest.raises(LaunchError) as excinfo:
        await runtime.start(actor="test")

    assert runtime.state is ServerState.OFFLINE
    assert excinfo.value.remediation, "L'erreur doit proposer une action corrective"

    last_error = runtime.snapshot()["last_error"]
    assert last_error is not None
    assert last_error["cause"]
    assert last_error["remediation"]
    # La console doit porter le diagnostic, pas seulement les logs serveur.
    assert any("Échec du démarrage" in line.text for line in runtime.logs_tail(20))


# --------------------------------------------------------------------------- #
#  Arrêt
# --------------------------------------------------------------------------- #
async def test_stop_is_graceful_when_server_cooperates(make_runtime) -> None:
    runtime = make_runtime()
    await _start_and_wait_online(runtime)

    outcome = await runtime.stop(actor="test")

    assert outcome.stage is StopStage.COMMAND, "L'arrêt aurait dû aboutir sur la commande `stop`"
    assert outcome.forced is False
    assert outcome.exit_code == 0
    assert await wait_for(lambda: runtime.state is ServerState.OFFLINE)


async def test_stop_escalates_to_kill_when_server_hangs(make_runtime) -> None:
    """Un serveur figé qui ignore `stop` et SIGTERM doit finir par être terminé."""
    runtime = make_runtime(
        "--ignore-stop",
        "--ignore-signals",
        stop_timeout_s=1.0,
        kill_timeout_s=1.0,
    )
    await _start_and_wait_online(runtime)
    pid = runtime.pid
    assert pid is not None

    outcome = await runtime.stop(actor="test")

    assert outcome.forced is True
    assert outcome.stage is StopStage.KILL
    assert await wait_for(lambda: not psutil.pid_exists(pid) or _is_dead(pid))


async def test_kill_terminates_the_whole_process_tree(make_runtime) -> None:
    """Un serveur lancé par script laisse un enfant : l'arbre entier doit tomber."""
    runtime = make_runtime(
        "--spawn-child",
        "--ignore-stop",
        "--ignore-signals",
        stop_timeout_s=1.0,
        kill_timeout_s=1.0,
    )
    await _start_and_wait_online(runtime)

    parent = psutil.Process(runtime.pid)
    assert await wait_for(lambda: len(parent.children(recursive=True)) >= 1, timeout=10.0)
    children = [child.pid for child in parent.children(recursive=True)]

    await runtime.stop(actor="test")

    for child_pid in children:
        assert await wait_for(lambda pid=child_pid: _is_dead(pid), timeout=10.0), (
            f"Le processus enfant {child_pid} a survécu à l'arrêt du serveur"
        )


# --------------------------------------------------------------------------- #
#  LE test d'isolation
# --------------------------------------------------------------------------- #
async def test_stop_only_affects_target_server(make_runtime, tmp_path) -> None:
    """Arrêter le serveur A ne doit avoir aucun effet sur le serveur B.

    C'est la régression majeure de la version 1, qui exécutait `pkill -f java`
    et coupait donc tous les serveurs de la machine — y compris ceux qu'elle ne
    gérait pas.
    """
    directory_a = tmp_path / "srv-a"
    directory_b = tmp_path / "srv-b"
    directory_a.mkdir()
    directory_b.mkdir()

    server_a = make_runtime(server_id=1, name="serveur-A", directory=directory_a)
    server_b = make_runtime(server_id=2, name="serveur-B", directory=directory_b)

    await _start_and_wait_online(server_a)
    await _start_and_wait_online(server_b)

    pid_a, pid_b = server_a.pid, server_b.pid
    assert pid_a is not None and pid_b is not None and pid_a != pid_b

    await server_a.stop(actor="test")
    assert await wait_for(lambda: server_a.state is ServerState.OFFLINE)

    # Le serveur B doit être intact : même état, même PID, console toujours ouverte.
    assert server_b.state is ServerState.ONLINE, "Le serveur B a changé d'état"
    assert server_b.pid == pid_b, "Le PID du serveur B a changé"
    assert not _is_dead(pid_b), "Le processus du serveur B a été tué"

    await server_b.send_command("say toujours vivant", actor="test")
    assert await wait_for(
        lambda: any("toujours vivant" in line.text for line in server_b.logs_tail(50))
    ), "Le serveur B ne répond plus à sa console"

    await server_b.stop(actor="test")


async def test_kill_only_affects_target_server(make_runtime, tmp_path) -> None:
    """Même garantie pour l'arrêt forcé, qui est le chemin le plus brutal."""
    directory_a = tmp_path / "k-a"
    directory_b = tmp_path / "k-b"
    directory_a.mkdir()
    directory_b.mkdir()

    server_a = make_runtime(server_id=10, name="kill-A", directory=directory_a)
    server_b = make_runtime(server_id=11, name="kill-B", directory=directory_b)

    await _start_and_wait_online(server_a)
    await _start_and_wait_online(server_b)
    pid_b = server_b.pid
    assert pid_b is not None

    await server_a.kill(actor="test")
    assert await wait_for(lambda: server_a.state is ServerState.OFFLINE)

    assert not _is_dead(pid_b)
    assert server_b.state is ServerState.ONLINE

    await server_b.stop(actor="test")


# --------------------------------------------------------------------------- #
#  Console
# --------------------------------------------------------------------------- #
async def test_send_command_reaches_the_server(make_runtime) -> None:
    runtime = make_runtime()
    await _start_and_wait_online(runtime)

    await runtime.send_command("say bonjour", actor="flavien")

    assert await wait_for(
        lambda: any("[Server] bonjour" in line.text for line in runtime.logs_tail(50))
    )
    # L'écho de la commande est tracé dans la console, avec son auteur.
    assert any("flavien" in line.text for line in runtime.logs_tail(50))

    await runtime.stop(actor="test")


async def test_command_with_newline_is_rejected(make_runtime) -> None:
    """Injection de commande : un saut de ligne permettrait d'en exécuter deux."""
    runtime = make_runtime()
    await _start_and_wait_online(runtime)

    with pytest.raises(UnsafeCommandError):
        await runtime.send_command("say bonjour\nop attaquant", actor="test")

    await runtime.stop(actor="test")


async def test_command_on_stopped_server_is_refused(make_runtime) -> None:
    runtime = make_runtime()
    with pytest.raises(ServerNotRunning):
        await runtime.send_command("say bonjour", actor="test")


async def test_broken_stdin_is_reported_with_an_actionable_message() -> None:
    """Un tube d'entrée rompu doit donner un diagnostic, jamais un silence.

    La détection est vérifiée directement sur le tube plutôt qu'en s'appuyant sur
    le comportement d'un script qui ferme son entrée : Linux signale la rupture
    immédiatement, Windows peut l'absorber. Ce qui doit être garanti sur les deux
    systèmes, c'est notre réaction une fois le tube inutilisable.
    """
    from msm.launchers import registry
    from msm.runtime.process_handle import ProcessHandle

    handle = ProcessHandle()
    spec = registry.build_spec(
        "custom",
        LaunchContext(
            name="stdin-test",
            directory=Path.cwd(),
            custom_argv=(sys.executable, str(FAKE_SERVER), "--heartbeat", "5"),
        ),
    )
    spawned = await handle.start(spec)
    try:
        assert handle.stdin_available is True
        await handle.write_line("say ok")

        # Le tube devient inutilisable (équivalent d'un script qui ferme stdin).
        assert spawned.process.stdin is not None
        spawned.process.stdin.close()

        assert handle.stdin_available is False
        with pytest.raises(ConsoleUnavailable) as excinfo:
            await handle.write_line("say perdu")

        assert excinfo.value.remediation
        assert "RCON" in excinfo.value.remediation or "PTY" in excinfo.value.remediation
    finally:
        await handle.kill_now()
        await handle.close()


async def test_server_with_closed_stdin_stays_manageable(make_runtime) -> None:
    """Un script qui ne relaie pas stdin ne doit pas rendre le serveur ingérable."""
    runtime = make_runtime("--close-stdin", stop_timeout_s=1.0, kill_timeout_s=1.0)
    await _start_and_wait_online(runtime)
    pid = runtime.pid
    assert pid is not None

    # L'arrêt doit aboutir malgré une console inopérante : la séquence bascule
    # sur le signal puis sur la terminaison forcée.
    outcome = await runtime.stop(actor="test")

    assert outcome.stage in (StopStage.SIGNAL, StopStage.KILL, StopStage.COMMAND)
    assert await wait_for(lambda: _is_dead(pid), timeout=15.0)
    assert await wait_for(lambda: runtime.state is ServerState.OFFLINE)


# --------------------------------------------------------------------------- #
#  Plantage et redémarrage automatique
# --------------------------------------------------------------------------- #
async def test_unexpected_exit_is_reported_as_crashed(make_runtime) -> None:
    runtime = make_runtime("--crash-after", "0.3", "--exit-code", "1")
    await _start_and_wait_online(runtime)

    assert await wait_for(lambda: runtime.state is ServerState.CRASHED, timeout=15.0)
    assert runtime.snapshot()["consecutive_crashes"] == 1


async def test_auto_restart_relaunches_after_crash(make_runtime) -> None:
    runtime = make_runtime(
        "--crash-after",
        "0.3",
        "--exit-code",
        "1",
        restart_policy=RestartPolicy(
            mode=AutoRestartMode.ON_CRASH,
            delay_s=0.2,
            max_consecutive_crashes=5,
            backoff_factor=1.0,
        ),
    )
    await _start_and_wait_online(runtime)

    assert await wait_for(lambda: runtime.state is ServerState.CRASHED, timeout=15.0)
    first_pid = runtime.pid

    # Le redémarrage automatique doit produire un nouveau processus.
    assert await wait_for(
        lambda: runtime.state.is_running and runtime.pid != first_pid, timeout=15.0
    ), "Le redémarrage automatique n'a pas eu lieu"

    await runtime.kill(actor="test")


async def test_auto_restart_stops_after_max_crashes(make_runtime) -> None:
    """La boucle infinie de redémarrage doit être impossible."""
    runtime = make_runtime(
        "--crash-after",
        "0.15",
        "--exit-code",
        "1",
        restart_policy=RestartPolicy(
            mode=AutoRestartMode.ON_CRASH,
            delay_s=0.05,
            max_consecutive_crashes=2,
            backoff_factor=1.0,
            stability_threshold_s=600.0,
        ),
    )
    await runtime.start(actor="test")

    assert await wait_for(lambda: runtime.snapshot()["consecutive_crashes"] >= 2, timeout=25.0)
    # Passé le plafond, plus aucune relance : l'état reste CRASHED.
    await asyncio.sleep(1.0)
    assert runtime.state is ServerState.CRASHED
    assert runtime.snapshot()["consecutive_crashes"] == 2


async def test_no_restart_when_stop_requested(make_runtime) -> None:
    runtime = make_runtime(restart_policy=RestartPolicy(mode=AutoRestartMode.ALWAYS, delay_s=0.1))
    await _start_and_wait_online(runtime)

    await runtime.stop(actor="test")
    assert await wait_for(lambda: runtime.state is ServerState.OFFLINE)

    await asyncio.sleep(0.8)
    assert runtime.state is ServerState.OFFLINE, "Un arrêt volontaire ne doit pas relancer"


# --------------------------------------------------------------------------- #
#  Suivi des joueurs et statistiques
# --------------------------------------------------------------------------- #
async def test_player_join_and_leave_are_tracked(make_runtime) -> None:
    runtime = make_runtime()
    await _start_and_wait_online(runtime)

    await runtime.send_command("join Flavien", actor="test")
    assert await wait_for(lambda: "Flavien" in runtime.online_players)

    await runtime.send_command("leave Flavien", actor="test")
    assert await wait_for(lambda: "Flavien" not in runtime.online_players)

    await runtime.stop(actor="test")


async def test_stats_are_collected(make_runtime) -> None:
    runtime = make_runtime("--heartbeat", "0.2", stats_interval_s=0.2)
    await _start_and_wait_online(runtime)

    assert await wait_for(lambda: runtime.stats.memory_mb > 0, timeout=10.0)
    assert runtime.stats.process_count >= 1

    await runtime.stop(actor="test")


# --------------------------------------------------------------------------- #
def _is_dead(pid: int) -> bool:
    """Le processus a-t-il disparu (ou n'est-il plus qu'un zombie) ?"""
    try:
        process = psutil.Process(pid)
        return not process.is_running() or process.status() == psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return True
