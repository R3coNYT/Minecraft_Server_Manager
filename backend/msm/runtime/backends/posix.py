"""Backend POSIX (Linux, macOS, BSD).

Isolation par **groupe de processus** : ``start_new_session=True`` place le
processus lancé dans une nouvelle session, dont il devient le leader. Son PGID est
alors égal à son PID, et tous ses descendants héritent de ce groupe.

Les signaux sont envoyés avec :func:`os.killpg`, donc à ce groupe et à lui seul.
Un serveur Forge qui lance Java depuis ``run.sh`` est arrêté intégralement, tandis
qu'un autre serveur tournant à côté n'est jamais atteint.
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import ClassVar

import psutil

from msm.launchers.base import ProcessSpec
from msm.logging_conf import get_logger
from msm.runtime.backends.base import STREAM_BUFFER_LIMIT, ProcessBackend, SpawnedProcess

logger = get_logger(__name__)

#: Tolérance de comparaison des dates de création (résolution de l'horloge noyau).
_CREATE_TIME_TOLERANCE_S = 1.0


class PosixProcessBackend(ProcessBackend):
    """Gestion de processus fondée sur les sessions et signaux POSIX."""

    name: ClassVar[str] = "posix"

    async def spawn(self, spec: ProcessSpec) -> SpawnedProcess:
        process = await asyncio.create_subprocess_exec(
            *spec.argv,
            cwd=str(spec.cwd),
            env=self._build_env(spec),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            # stderr fusionné dans stdout : l'ordre chronologique des lignes est
            # préservé, ce qui compte plus que la distinction des deux flux.
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            limit=STREAM_BUFFER_LIMIT,
        )

        try:
            group_id = os.getpgid(process.pid)
        except ProcessLookupError:
            # Processus déjà mort (échec immédiat) : on retombe sur le PID, qui
            # vaut le PGID puisque le processus était leader de sa session.
            group_id = process.pid

        return SpawnedProcess(
            process=process,
            pid=process.pid,
            group_id=group_id,
            create_time=_safe_create_time(process.pid),
        )

    def request_graceful_stop(self, spawned: SpawnedProcess) -> bool:
        return self._signal_group(spawned, signal.SIGTERM)

    def kill_tree(self, spawned: SpawnedProcess) -> bool:
        return self._signal_group(spawned, signal.SIGKILL)

    def terminate_external(
        self,
        pid: int,
        group_id: int | None = None,
        create_time: float | None = None,
        *,
        force: bool = False,
    ) -> bool:
        """Signale le groupe d'un processus réadopté.

        ``SIGTERM`` déclenche les crochets d'arrêt de la JVM : un serveur
        Minecraft sauvegarde son monde puis s'arrête proprement, même sans accès
        à sa console.
        """
        if not self.is_alive(pid, create_time):
            return False

        target = group_id or pid
        if target in (0, os.getpgrp()):
            logger.error("external_signal_refused", reason="groupe de MSM", group_id=target)
            return False

        try:
            os.killpg(target, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            return False
        except PermissionError:
            logger.error("external_signal_denied", pid=pid, group_id=target)
            return False
        return True

    def is_alive(self, pid: int, create_time: float | None = None) -> bool:
        try:
            process = psutil.Process(pid)
            if process.status() == psutil.STATUS_ZOMBIE:
                return False
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            return False

        if create_time is None:
            return True
        try:
            return abs(process.create_time() - create_time) < _CREATE_TIME_TOLERANCE_S
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    # ---------------------------------------------------------------- #
    def _signal_group(self, spawned: SpawnedProcess, sig: signal.Signals) -> bool:
        """Envoie un signal au groupe du processus, avec garde anti-PID recyclé."""
        group_id = spawned.group_id or spawned.pid

        # Garde-fou : ne jamais signaler le groupe de MSM lui-même. Sans cette
        # vérification, un identifiant de groupe corrompu ferait s'auto-terminer
        # le panel — et donc tous les serveurs avec lui.
        if group_id in (0, os.getpgrp()):
            logger.error(
                "signal_group_refused",
                reason="le groupe visé est celui de MSM",
                group_id=group_id,
                pid=spawned.pid,
            )
            return False

        if not self.is_alive(spawned.pid, spawned.create_time):
            return False

        try:
            os.killpg(group_id, sig)
        except ProcessLookupError:
            return False
        except PermissionError:
            logger.error(
                "signal_group_denied",
                group_id=group_id,
                signal=sig.name,
                hint="MSM n'a pas les droits sur ce groupe de processus",
            )
            return False

        logger.debug("signal_sent", group_id=group_id, signal=sig.name, pid=spawned.pid)
        return True


def _safe_create_time(pid: int) -> float | None:
    try:
        return psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
