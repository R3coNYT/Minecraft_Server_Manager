"""Fixtures partagées.

Le faux serveur Minecraft est piloté via le launcher « commande personnalisée » :
les tests exercent donc exactement le même chemin de code qu'un vrai serveur —
registre de launchers, backend système, séquence d'arrêt — sans dépendre de Java.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from msm.bus.event_bus import EventBus
from msm.core.restart_policy import RestartPolicy
from msm.launchers import LaunchContext
from msm.runtime.server_runtime import ServerRuntime, ServerRuntimeConfig
from msm.runtime.supervisor import Supervisor

FAKE_SERVER = Path(__file__).parent / "fixtures" / "fake_minecraft_server.py"


@pytest.fixture(scope="session", autouse=True)
def _windows_event_loop_policy() -> None:
    """Sous Windows, seule la boucle Proactor sait créer des sous-processus.

    C'est déjà la politique par défaut depuis Python 3.8 ; on ne la force que sur
    les versions où l'API de politique n'est pas dépréciée (< 3.14).
    """
    if sys.platform == "win32" and sys.version_info < (3, 14):
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


@pytest.fixture
def bus() -> Iterator[EventBus]:
    """Bus isolé — jamais le bus applicatif partagé."""
    instance = EventBus()
    yield instance
    instance.close()


@pytest.fixture
def server_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "server"
    directory.mkdir()
    return directory


RuntimeFactory = Callable[..., ServerRuntime]


@pytest.fixture
def make_runtime(server_dir: Path, bus: EventBus) -> Iterator[RuntimeFactory]:
    """Fabrique de runtimes branchés sur le faux serveur."""
    created: list[ServerRuntime] = []

    def factory(
        *fake_args: str,
        server_id: int = 1,
        name: str = "test-server",
        directory: Path | None = None,
        restart_policy: RestartPolicy | None = None,
        **overrides: object,
    ) -> ServerRuntime:
        target = directory or server_dir
        config = ServerRuntimeConfig(
            id=server_id,
            name=name,
            directory=target,
            launcher_key="custom",
            launch=LaunchContext(
                name=name,
                directory=target,
                custom_argv=(sys.executable, str(FAKE_SERVER), *fake_args),
            ),
            stop_timeout_s=overrides.pop("stop_timeout_s", 5.0),  # type: ignore[arg-type]
            kill_timeout_s=overrides.pop("kill_timeout_s", 3.0),  # type: ignore[arg-type]
            start_timeout_s=overrides.pop("start_timeout_s", 30.0),  # type: ignore[arg-type]
            stats_interval_s=overrides.pop("stats_interval_s", 0.2),  # type: ignore[arg-type]
            restart_policy=restart_policy or RestartPolicy(),
            **overrides,  # type: ignore[arg-type]
        )
        runtime = ServerRuntime(config, bus=bus)
        created.append(runtime)
        return runtime

    yield factory

    # Filet de sécurité : aucun processus de test ne doit survivre à la session.
    # Nettoyage synchrone via psutil : la boucle d'événements du test est déjà
    # fermée à ce stade, on ne peut plus y ordonnancer de coroutine.
    for runtime in created:
        _kill_process_tree(runtime.pid)


def _kill_process_tree(pid: int | None) -> None:
    """Termine un processus et ses descendants. Best-effort, ne lève jamais."""
    if pid is None:
        return
    import psutil

    try:
        parent = psutil.Process(pid)
        victims = [*parent.children(recursive=True), parent]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return

    for victim in victims:
        try:
            victim.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


@pytest.fixture
def supervisor(bus: EventBus) -> Supervisor:
    return Supervisor(bus=bus)


async def wait_for(predicate: Callable[[], bool], *, timeout: float = 10.0) -> bool:
    """Attend qu'une condition devienne vraie, sans temporisation fixe."""
    import asyncio

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()
