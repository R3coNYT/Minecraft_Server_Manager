"""Backend des serveurs isolés : une unité systemd confinée par serveur.

Les serveurs des comptes (pas ceux des administrateurs) ne tournent pas sous
l'utilisateur de MSM : le helper root ``msm-sandbox`` les lance dans leur propre
unité (``msm-server-<id>.service``), sous un compte système propre au compte
MSM, avec leur seul dossier en écriture, les autres serveurs et MSM invisibles,
et des plafonds de mémoire, de processeur et de tâches.

MSM lui parle par un socket réservé à son groupe : il n'a besoin d'aucun
privilège, et son unité garde toutes ses restrictions. Pour démarrer un serveur,
il lance le relais (:mod:`msm.runtime.backends.sandbox_relay`), qui devient la
console : le reste du runtime le voit comme un processus ordinaire.

Deux différences avec le backend POSIX :

* le PID retenu est celui du serveur (``MainPID`` de l'unité), pas celui du
  relais : c'est lui qu'on mesure, et lui qu'on retrouve après un redémarrage
  de MSM ;
* les signaux passent par le helper, qui les envoie à toute l'unité — MSM n'a
  pas le droit de signaler un processus d'un autre compte.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from msm.i18n import tr
from msm.launchers.base import ProcessSpec
from msm.logging_conf import get_logger
from msm.runtime.backends.base import STREAM_BUFFER_LIMIT, SpawnedProcess
from msm.runtime.backends.posix import PosixProcessBackend, _safe_create_time

logger = get_logger(__name__)

RELAY = Path(__file__).with_name("sandbox_relay.py")

#: Délai laissé au helper pour préparer puis démarrer l'unité (création du
#: compte, droits posés sur un dossier de modpack de milliers de fichiers).
_START_TIMEOUT_S = 120.0
_POLL_INTERVAL_S = 0.2
#: Une demande d'arrêt ou de PID répond en quelques millisecondes.
_REQUEST_TIMEOUT_S = 15.0


@dataclass(frozen=True, slots=True)
class SandboxSpec:
    """Isolation d'un serveur : son compte et ses plafonds."""

    #: Identifiant de stockage du propriétaire : le compte système sera ``msm-<account>``.
    account: str
    memory_limit_mb: int
    cpu_percent: int
    socket_path: Path = Path("/run/msm-sandbox.sock")
    status_dir: Path = Path("/run/msm-sandbox")


def sandbox_memory_limit(memory_max_mb: int | None) -> int:
    """Plafond de l'unité : le tas Java, plus ce que la JVM consomme en dehors."""
    heap = memory_max_mb or 2048
    return int(heap * 1.25) + 512


class SandboxError(OSError):
    """Le helper a refusé la demande ou n'a pas pu être joint."""


class SystemdSandboxBackend(PosixProcessBackend):
    """Un serveur, une unité systemd confinée, pilotée par le helper."""

    name: ClassVar[str] = "systemd-sandbox"

    def __init__(self, server_id: int, sandbox: SandboxSpec) -> None:
        self._server_id = server_id
        self._sandbox = sandbox

    # ---------------------------------------------------------------- #
    #  Demandes au helper
    # ---------------------------------------------------------------- #
    def request(self, *fields: str) -> str:
        """Envoie une demande au helper et renvoie sa réponse (bloquant, bref)."""
        payload = b"".join(field.encode() + b"\0" for field in fields) + b"\0"
        chunks: list[bytes] = []
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(_REQUEST_TIMEOUT_S)
                connection.connect(str(self._sandbox.socket_path))
                connection.sendall(payload)
                connection.shutdown(socket.SHUT_WR)
                while chunk := connection.recv(65536):
                    chunks.append(chunk)
        except OSError as exc:
            raise SandboxError(
                tr(
                    "The isolation helper is unreachable ({path}): {error}",
                    path=self._sandbox.socket_path,
                    error=exc.strerror or exc,
                )
            ) from exc
        answer = b"".join(chunks).decode("utf-8", errors="replace").strip()
        if answer.startswith("msm-sandbox:"):
            raise SandboxError(answer)
        return answer

    def main_pid(self) -> int:
        """PID du serveur dans son unité ; 0 s'il ne tourne pas."""
        answer = self.request("pid", "--server", str(self._server_id))
        try:
            return int(answer.splitlines()[-1]) if answer else 0
        except ValueError as exc:
            raise SandboxError(answer) from exc

    def run_argv(self, spec: ProcessSpec) -> list[str]:
        """Commande du relais : demande ``run`` complète, puis la commande du serveur."""
        sandbox = self._sandbox
        fields = [
            "run",
            "--server", str(self._server_id),
            "--account", sandbox.account,
            "--dir", str(spec.cwd),
            "--memory", str(sandbox.memory_limit_mb),
            "--cpu", str(sandbox.cpu_percent),
        ]  # fmt: skip
        # Seules les variables du serveur passent : l'environnement de MSM porte
        # ses secrets (clé de session, identifiants Google).
        for key, value in sorted(spec.env.items()):
            fields += ["--env", f"{key}={value}"]
        fields += ["--", *spec.argv]
        status = sandbox.status_dir / f"{self._server_id}.status"
        return [sys.executable, "-I", str(RELAY), str(sandbox.socket_path), str(status), *fields]

    # ---------------------------------------------------------------- #
    #  ProcessBackend
    # ---------------------------------------------------------------- #
    async def spawn(self, spec: ProcessSpec) -> SpawnedProcess:
        # Une instance restée en vie tient le nom de l'unité : autant le dire
        # tout de suite, plutôt que d'adopter son PID par erreur.
        if await asyncio.to_thread(self.main_pid):
            raise SandboxError(
                tr(
                    "An isolated instance of this server is already running "
                    "(unit msm-server-{id}.service).",
                    id=self._server_id,
                )
            )

        process = await asyncio.create_subprocess_exec(
            *self.run_argv(spec),
            cwd=str(spec.cwd),
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            limit=STREAM_BUFFER_LIMIT,
        )

        pid = await self._wait_for_unit(process)
        if not pid:
            # Refus du helper ou échec immédiat : le relais se termine en
            # affichant la cause, le runtime la verra comme une sortie normale.
            return SpawnedProcess(
                process=process, pid=process.pid, group_id=process.pid,
                create_time=_safe_create_time(process.pid),
            )  # fmt: skip
        return SpawnedProcess(process=process, pid=pid, create_time=_safe_create_time(pid))

    async def _wait_for_unit(self, process: asyncio.subprocess.Process) -> int:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _START_TIMEOUT_S
        while loop.time() < deadline and process.returncode is None:
            await asyncio.sleep(_POLL_INTERVAL_S)
            try:
                pid = await asyncio.to_thread(self.main_pid)
            except SandboxError as exc:
                logger.warning("sandbox_pid_failed", server_id=self._server_id, error=str(exc))
                continue
            if pid:
                return pid
        return 0

    def request_graceful_stop(self, spawned: SpawnedProcess) -> bool:
        return self._stop(spawned, force=False)

    def kill_tree(self, spawned: SpawnedProcess) -> bool:
        return self._stop(spawned, force=True)

    def terminate_external(
        self,
        pid: int,
        group_id: int | None = None,
        create_time: float | None = None,
        *,
        force: bool = False,
    ) -> bool:
        if not self.is_alive(pid, create_time):
            return False
        return self._signal_unit(force=force)

    # ---------------------------------------------------------------- #
    def _stop(self, spawned: SpawnedProcess, *, force: bool) -> bool:
        if spawned.pid == spawned.process.pid:
            # L'unité n'a jamais démarré : seul le relais existe.
            return self._signal_group(spawned, signal.SIGKILL if force else signal.SIGTERM)
        return self._signal_unit(force=force)

    def _signal_unit(self, *, force: bool) -> bool:
        fields = ["stop", "--server", str(self._server_id)]
        if force:
            fields.append("--force")
        try:
            return self.request(*fields) == "ok"
        except SandboxError as exc:
            logger.error("sandbox_stop_failed", server_id=self._server_id, error=str(exc))
            return False
