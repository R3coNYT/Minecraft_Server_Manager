"""Serveurs isolés : backend systemd, relais et choix du confinement.

Le vrai helper (systemd/msm-sandbox) exige root et systemd ; il est remplacé ici
par un faux qui parle le même protocole sur un socket Unix et se comporte comme
``systemd-run --pipe`` : le serveur reçoit la connexion comme entrée et sortie.
Tout le reste — relais, backend, runtime — est le vrai code.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from msm.config import Settings
from msm.core.permissions import Role
from msm.core.states import ServerState
from msm.exceptions import ServerStartFailed
from msm.launchers import LaunchContext
from msm.launchers.base import ProcessSpec
from msm.runtime.backends.sandbox import (
    RELAY,
    SandboxSpec,
    SystemdSandboxBackend,
    sandbox_memory_limit,
)
from msm.runtime.process_handle import StopStage
from msm.runtime.server_runtime import ServerRuntime, ServerRuntimeConfig
from msm.services.server_service import ServerService
from tests.conftest import FAKE_SERVER, wait_for

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="sockets Unix et signaux POSIX")


# --------------------------------------------------------------------------- #
#  Faux helper
# --------------------------------------------------------------------------- #
class FakeHelper:
    """Le protocole de msm-sandbox, sans root ni systemd."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir = Path(tempfile.mkdtemp(prefix="msm-sbx-"))
        self.socket_path = self.dir / "helper.sock"
        self.status_dir = self.dir / "status"
        self.status_dir.mkdir()
        self.requests: list[list[str]] = []
        self.pids: dict[str, int] = {}
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(str(self.socket_path))
        self._listener.listen(16)
        self._closed = False
        threading.Thread(target=self._accept, daemon=True).start()

    def spec(self, **overrides: object) -> SandboxSpec:
        values: dict[str, object] = {
            "account": "abcde12345",
            "memory_limit_mb": 1024,
            "cpu_percent": 100,
            "socket_path": self.socket_path,
            "status_dir": self.status_dir,
        }
        values.update(overrides)
        return SandboxSpec(**values)  # type: ignore[arg-type]

    def close(self) -> None:
        self._closed = True
        with contextlib.suppress(OSError):
            self._listener.close()
        for pid in self.pids.values():
            if pid:
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)

    # ---------------------------------------------------------------- #
    def _accept(self) -> None:
        while not self._closed:
            try:
                connection, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    @staticmethod
    def _read_request(connection: socket.socket) -> list[str]:
        # Octet par octet, comme bash : la suite du flux est la console.
        fields: list[str] = []
        current = bytearray()
        while True:
            byte = connection.recv(1)
            if not byte:
                return fields
            if byte != b"\0":
                current += byte
                continue
            if not current:
                return fields
            fields.append(current.decode())
            current.clear()

    def _serve(self, connection: socket.socket) -> None:
        with connection:
            fields = self._read_request(connection)
            self.requests.append(fields)
            command, args = fields[0], fields[1:]
            server = args[args.index("--server") + 1]
            if command == "pid":
                connection.sendall(f"{self.pids.get(server, 0)}\n".encode())
            elif command == "stop":
                pid = self.pids.get(server, 0)
                if not pid:
                    connection.sendall(b"gone\n")
                    return
                os.kill(pid, signal.SIGKILL if "--force" in args else signal.SIGTERM)
                connection.sendall(b"ok\n")
            elif command == "run":
                self._run(connection, server, args)

    def _run(self, connection: socket.socket, server: str, args: list[str]) -> None:
        separator = args.index("--")
        options, argv = args[:separator], args[separator + 1 :]
        directory = Path(options[options.index("--dir") + 1])
        if self.root not in directory.resolve().parents:
            connection.sendall(
                f"msm-sandbox: '{directory}' is not under a servers root.\n".encode()
            )
            return
        env = {"PATH": os.environ.get("PATH", "")}
        for index, option in enumerate(options):
            if option == "--env":
                key, _, value = options[index + 1].partition("=")
                env[key] = value
        fd = connection.fileno()
        process = subprocess.Popen(argv, cwd=directory, env=env, stdin=fd, stdout=fd, stderr=fd)
        self.pids[server] = process.pid
        code = process.wait()
        self.pids[server] = 0
        (self.status_dir / f"{server}.status").write_text(f"{code}\n")


@pytest.fixture
def helper(tmp_path: Path):
    fake = FakeHelper(tmp_path)
    yield fake
    fake.close()


def _config(
    directory: Path, sandbox: SandboxSpec, *fake_args: str, **overrides
) -> ServerRuntimeConfig:
    return ServerRuntimeConfig(
        id=overrides.pop("server_id", 7),
        name="isole",
        directory=directory,
        launcher_key="custom",
        launch=LaunchContext(
            name="isole",
            directory=directory,
            custom_argv=(sys.executable, str(FAKE_SERVER), *fake_args),
            env={"MSM_TEST_VAR": "visible"},
        ),
        stop_timeout_s=overrides.pop("stop_timeout_s", 5.0),
        kill_timeout_s=overrides.pop("kill_timeout_s", 3.0),
        start_timeout_s=30.0,
        stats_interval_s=0.2,
        isolation=sandbox,
        **overrides,
    )


def _server_dir(root: Path) -> Path:
    """Un dossier de serveur est toujours *sous* une racine, jamais la racine elle-même."""
    directory = root / "serveur"
    directory.mkdir(exist_ok=True)
    return directory


async def _online(runtime: ServerRuntime) -> None:
    await runtime.start(actor="test")
    assert await wait_for(lambda: runtime.state is ServerState.ONLINE, timeout=20.0), (
        runtime.state.value
    )


# --------------------------------------------------------------------------- #
#  Pièces isolées
# --------------------------------------------------------------------------- #
def test_memory_limit_leaves_room_outside_the_heap() -> None:
    assert sandbox_memory_limit(4096) == 5632
    assert sandbox_memory_limit(None) == sandbox_memory_limit(2048)


def test_run_request_carries_only_the_server_variables(monkeypatch, tmp_path: Path) -> None:
    """L'environnement de MSM porte ses secrets : il ne doit pas passer au serveur."""
    monkeypatch.setenv("MSM_SECRET_KEY", "ne-doit-pas-fuir")
    backend = SystemdSandboxBackend(
        12, SandboxSpec(account="abcde12345", memory_limit_mb=3072, cpu_percent=150)
    )
    spec = ProcessSpec(argv=("java", "-jar", "server.jar"), cwd=tmp_path, env={"TZ": "UTC"})

    argv = backend.run_argv(spec)

    assert argv[:3] == [sys.executable, "-I", str(RELAY)]
    assert argv[3:5] == [
        str(Path("/run/msm-sandbox.sock")),
        str(Path("/run/msm-sandbox/12.status")),
    ]
    fields = argv[5:]
    assert fields[:13] == [
        "run", "--server", "12", "--account", "abcde12345", "--dir", str(tmp_path),
        "--memory", "3072", "--cpu", "150", "--env", "TZ=UTC",
    ]  # fmt: skip
    assert fields[13:] == ["--", "java", "-jar", "server.jar"]
    assert not any("ne-doit-pas-fuir" in field for field in argv)


def test_runtime_uses_the_sandbox_only_when_isolated(tmp_path: Path) -> None:
    sandbox = SandboxSpec(account="abcde12345", memory_limit_mb=1024, cpu_percent=100)
    config = _config(tmp_path, sandbox)
    isolated = ServerRuntime(config)
    plain = ServerRuntime(replace(config, isolation=None))

    assert isinstance(isolated._backend_ref, SystemdSandboxBackend)
    assert not isinstance(plain._backend_ref, SystemdSandboxBackend)


@pytest.mark.parametrize(
    ("mode", "platform", "role", "expected"),
    [
        ("systemd", "linux", Role.USER, True),
        ("systemd", "linux", Role.MODERATOR, True),
        ("systemd", "linux", Role.ADMIN, False),
        ("off", "linux", Role.USER, False),
        ("systemd", "win32", Role.USER, False),
    ],
)
def test_only_account_servers_are_isolated(monkeypatch, mode, platform, role, expected) -> None:
    """Les serveurs des administrateurs tournent sous MSM, comme avant."""
    settings = Settings(isolation=mode, isolation_cpu_percent=300)
    service = ServerService(None, settings, None)  # type: ignore[arg-type]
    owner = SimpleNamespace(role=role, storage_id="zyxwv98765")
    server = SimpleNamespace(owner=owner)
    monkeypatch.setattr(sys, "platform", platform)

    spec = service._isolation_for(server, SimpleNamespace(memory_max_mb=2048))  # type: ignore[arg-type]

    if not expected:
        assert spec is None
        return
    assert spec == SandboxSpec(
        account="zyxwv98765",
        memory_limit_mb=sandbox_memory_limit(2048),
        cpu_percent=300,
        socket_path=settings.isolation_socket,
        status_dir=settings.isolation_status_dir,
    )


# --------------------------------------------------------------------------- #
#  Avec le faux helper
# --------------------------------------------------------------------------- #
@POSIX_ONLY
@pytest.mark.asyncio
async def test_isolated_server_lifecycle(helper: FakeHelper, tmp_path: Path) -> None:
    """Démarrage, console, statistiques sur le vrai PID, arrêt par la console."""
    runtime = ServerRuntime(_config(_server_dir(tmp_path), helper.spec()))
    await _online(runtime)

    run = next(request for request in helper.requests if request[0] == "run")
    assert run[run.index("--account") + 1] == "abcde12345"
    assert "MSM_TEST_VAR=visible" in run
    # Le PID suivi est celui du serveur dans son unité, pas celui du relais.
    assert runtime.pid == helper.pids["7"]
    assert runtime.snapshot()["console_writable"] is True

    outcome = await runtime.stop(actor="test")

    assert outcome.stage is StopStage.COMMAND
    assert outcome.exit_code == 0
    assert runtime.state is ServerState.OFFLINE


@POSIX_ONLY
@pytest.mark.asyncio
async def test_frozen_isolated_server_is_killed_through_the_helper(
    helper: FakeHelper, tmp_path: Path
) -> None:
    runtime = ServerRuntime(
        _config(
            _server_dir(tmp_path), helper.spec(), "--ignore-stop", "--ignore-signals",
            stop_timeout_s=0.5, kill_timeout_s=3.0,
        )
    )  # fmt: skip
    await _online(runtime)

    outcome = await runtime.stop(actor="test")

    assert outcome.stage is StopStage.KILL
    stops = [request for request in helper.requests if request[0] == "stop"]
    assert [request[-1] for request in stops][-2:] == ["7", "--force"]
    assert runtime.state is ServerState.OFFLINE


@POSIX_ONLY
@pytest.mark.asyncio
async def test_relay_returns_the_server_exit_code(helper: FakeHelper, tmp_path: Path) -> None:
    backend = SystemdSandboxBackend(3, helper.spec())
    spec = ProcessSpec(
        argv=(sys.executable, "-c", "raise SystemExit(3)"), cwd=_server_dir(tmp_path)
    )

    spawned = await backend.spawn(spec)

    assert await spawned.process.wait() == 3


@POSIX_ONLY
@pytest.mark.asyncio
async def test_helper_refusal_reaches_the_console(helper: FakeHelper, tmp_path: Path) -> None:
    outside = Path(tempfile.mkdtemp(prefix="msm-outside-"))
    runtime = ServerRuntime(_config(outside, helper.spec()))

    await runtime.start(actor="test")

    assert await wait_for(lambda: runtime.state is not ServerState.STARTING, timeout=10.0)
    lines = [line.text for line in runtime.logs_since(0)]
    assert any("not under a servers root" in line for line in lines), lines


@POSIX_ONLY
@pytest.mark.asyncio
async def test_unreachable_helper_fails_the_start(tmp_path: Path) -> None:
    sandbox = SandboxSpec(
        account="abcde12345", memory_limit_mb=1024, cpu_percent=100,
        socket_path=tmp_path / "absent.sock", status_dir=tmp_path,
    )  # fmt: skip
    runtime = ServerRuntime(_config(tmp_path, sandbox))

    with pytest.raises(ServerStartFailed) as excinfo:
        await runtime.start(actor="test")

    assert "absent.sock" in str(excinfo.value.cause)
    assert runtime.state is ServerState.OFFLINE


@POSIX_ONLY
@pytest.mark.asyncio
async def test_a_leftover_unit_is_not_taken_for_a_new_start(
    helper: FakeHelper, tmp_path: Path
) -> None:
    """Une instance restée en vie tient l'unité : on refuse au lieu d'adopter son PID."""
    helper.pids["7"] = os.getpid()
    runtime = ServerRuntime(_config(_server_dir(tmp_path), helper.spec()))

    with pytest.raises(ServerStartFailed) as excinfo:
        await runtime.start(actor="test")

    helper.pids["7"] = 0
    assert "msm-server-7" in str(excinfo.value.cause)
