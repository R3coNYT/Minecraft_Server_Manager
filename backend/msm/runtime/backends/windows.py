"""Backend Windows.

Windows n'a ni ``setsid`` ni ``killpg``. L'isolation repose sur deux mécanismes :

* ``CREATE_NEW_PROCESS_GROUP`` — le processus devient chef de son propre groupe ;
* un **Job Object** auquel le processus est rattaché : tous ses descendants y sont
  automatiquement inclus, et ``TerminateJobObject`` les termine d'un bloc, sans
  jamais toucher un processus extérieur au job.

Si ``pywin32`` n'est pas installé, le repli parcourt l'arbre des descendants avec
``psutil``. Moins atomique (un processus créé pendant le parcours peut survivre),
mais tout aussi ciblé.

**Choix assumé** : le job n'active pas ``KILL_ON_JOB_CLOSE``. Un panel qui plante
ne doit pas emporter avec lui les serveurs Minecraft en cours de partie — comme
sous POSIX, les processus survivent à MSM et sont réadoptés à son redémarrage.

**Limite documentée** : il n'existe pas d'équivalent Windows au ``SIGTERM`` pour
une JVM. ``CTRL_BREAK_EVENT`` y déclenche un vidage de threads, pas un arrêt, et
``CTRL_C_EVENT`` est ignoré par un processus créé avec son propre groupe. L'arrêt
propre passe donc exclusivement par la commande ``stop`` envoyée sur l'entrée
standard — chemin identique aux deux systèmes — et l'étape suivante est directement
la terminaison de l'arbre.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from typing import Any, ClassVar

import psutil

from msm.launchers.base import ProcessSpec
from msm.logging_conf import get_logger
from msm.runtime.backends.base import STREAM_BUFFER_LIMIT, ProcessBackend, SpawnedProcess

logger = get_logger(__name__)

_CREATE_TIME_TOLERANCE_S = 1.0

# `pywin32` est optionnel : sans lui, le repli psutil prend le relais.
try:  # pragma: no cover - dépend de l'environnement
    import win32api
    import win32con
    import win32job

    _HAS_PYWIN32 = True
except ImportError:  # pragma: no cover
    _HAS_PYWIN32 = False


class WindowsProcessBackend(ProcessBackend):
    """Gestion de processus fondée sur les groupes et Job Objects Windows."""

    name: ClassVar[str] = "windows"
    #: Aucune primitive d'arrêt poli exploitable pour une JVM (voir en-tête).
    supports_graceful_signal: ClassVar[bool] = False

    async def spawn(self, spec: ProcessSpec) -> SpawnedProcess:
        self._require_subprocess_capable_loop()

        import subprocess

        process = await asyncio.create_subprocess_exec(
            *spec.argv,
            cwd=str(spec.cwd),
            env=self._build_env(spec),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            limit=STREAM_BUFFER_LIMIT,
        )

        spawned = SpawnedProcess(
            process=process,
            pid=process.pid,
            group_id=None,
            create_time=_safe_create_time(process.pid),
        )

        handle, warning = self._attach_to_job(process.pid)
        spawned.native_handle = handle
        if warning:
            spawned.warnings.append(warning)
        return spawned

    def request_graceful_stop(self, spawned: SpawnedProcess) -> bool:
        # Volontairement inopérant : voir la limite documentée en tête de module.
        logger.debug("graceful_signal_unavailable", pid=spawned.pid, platform="windows")
        return False

    def kill_tree(self, spawned: SpawnedProcess) -> bool:
        if not self.is_alive(spawned.pid, spawned.create_time):
            return False

        if spawned.native_handle is not None:
            try:
                win32job.TerminateJobObject(spawned.native_handle, 1)
                logger.debug("job_terminated", pid=spawned.pid)
                return True
            except Exception as exc:  # pragma: no cover - dépend du système
                logger.warning("job_terminate_failed", pid=spawned.pid, error=str(exc))

        return self._kill_tree_via_psutil(spawned.pid, spawned.create_time)

    def terminate_external(
        self,
        pid: int,
        group_id: int | None = None,
        create_time: float | None = None,
        *,
        force: bool = False,
    ) -> bool:
        """Termine l'arbre d'un processus réadopté.

        Le Job Object d'origine a disparu avec le processus MSM précédent : seul
        le parcours de l'arbre reste possible. Et faute d'équivalent au SIGTERM,
        l'arrêt est toujours brutal — le monde n'est pas sauvegardé.
        """
        del group_id, force  # sans objet sous Windows
        return self._kill_tree_via_psutil(pid, create_time)

    def is_alive(self, pid: int, create_time: float | None = None) -> bool:
        try:
            process = psutil.Process(pid)
            if not process.is_running():
                return False
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            return False

        if create_time is None:
            return True
        try:
            return abs(process.create_time() - create_time) < _CREATE_TIME_TOLERANCE_S
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def release(self, spawned: SpawnedProcess) -> None:
        if spawned.native_handle is None:
            return
        try:  # pragma: no cover - dépend du système
            win32api.CloseHandle(spawned.native_handle)
        except Exception as exc:  # pragma: no cover
            logger.debug("job_handle_close_failed", error=str(exc))
        finally:
            spawned.native_handle = None

    # ---------------------------------------------------------------- #
    def _attach_to_job(self, pid: int) -> tuple[Any, str | None]:
        """Rattache le processus à un Job Object dédié.

        Renvoie ``(handle, avertissement)``. Un handle ``None`` signifie que le
        repli psutil sera utilisé — le fonctionnement reste correct, simplement
        moins atomique.
        """
        if not _HAS_PYWIN32:
            return None, (
                "pywin32 n'est pas installé : l'arrêt forcé parcourra l'arbre des "
                "processus au lieu d'utiliser un Job Object. "
                'Installer avec `pip install "msm[windows]"` pour un arrêt atomique.'
            )

        try:  # pragma: no cover - dépend du système
            job = win32job.CreateJobObject(None, "")
            info = win32job.QueryInformationJobObject(
                job, win32job.JobObjectExtendedLimitInformation
            )
            # Pas de KILL_ON_JOB_CLOSE : les serveurs survivent à un arrêt de MSM.
            info["BasicLimitInformation"]["LimitFlags"] = 0
            win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)

            permissions = (
                win32con.PROCESS_TERMINATE
                | win32con.PROCESS_SET_QUOTA
                | win32con.PROCESS_QUERY_INFORMATION
            )
            process_handle = win32api.OpenProcess(permissions, False, pid)
            try:
                win32job.AssignProcessToJobObject(job, process_handle)
            finally:
                win32api.CloseHandle(process_handle)
            return job, None
        except Exception as exc:  # pragma: no cover - dépend du système
            logger.warning("job_object_unavailable", pid=pid, error=str(exc))
            return None, (
                f"Impossible de créer un Job Object ({exc}). "
                "L'arrêt forcé utilisera le parcours de l'arbre des processus."
            )

    def _kill_tree_via_psutil(self, pid: int, create_time: float | None) -> bool:
        """Repli : termine les descendants puis le parent, du plus profond au plus haut."""
        try:
            parent = psutil.Process(pid)
            if create_time is not None and abs(parent.create_time() - create_time) >= (
                _CREATE_TIME_TOLERANCE_S
            ):
                # PID recyclé : ce processus n'est pas le nôtre, ne pas y toucher.
                logger.error("kill_refused_pid_reused", pid=pid)
                return False
            victims = parent.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

        # Les enfants d'abord : tuer le parent en premier réorphelinerait les autres.
        for victim in reversed(victims):
            _safe_kill(victim)
        _safe_kill(parent)
        return True

    @staticmethod
    def _require_subprocess_capable_loop() -> None:
        """Vérifie que la boucle asyncio sait créer des sous-processus.

        Sous Windows, seule ``ProactorEventLoop`` le permet ; une boucle
        ``Selector`` provoquerait un ``NotImplementedError`` opaque au lancement.
        """
        if sys.platform != "win32":  # pragma: no cover
            return
        loop = asyncio.get_running_loop()
        if isinstance(loop, asyncio.SelectorEventLoop):
            from msm.exceptions import ServerStartFailed

            raise ServerStartFailed(
                "Impossible de lancer un serveur.",
                cause=(
                    "La boucle d'événements asyncio utilisée (SelectorEventLoop) ne sait "
                    "pas créer de sous-processus sous Windows."
                ),
                remediation=(
                    "Démarrer MSM avec la boucle Proactor : "
                    "`asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())`, "
                    "ou lancer uvicorn sans `--loop uvloop`."
                ),
            )


def _safe_kill(process: psutil.Process) -> None:
    with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
        process.kill()


def _safe_create_time(pid: int) -> float | None:
    try:
        return psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
