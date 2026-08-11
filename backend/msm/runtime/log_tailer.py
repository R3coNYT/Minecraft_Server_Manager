"""Lecture d'un fichier de log en continu, façon ``tail -F``.

Utilisé pour les serveurs **réadoptés** : après un redémarrage de MSM, les tubes
du processus sont perdus et sa sortie n'est plus captable. Le fichier
``logs/latest.log`` que le serveur écrit lui-même devient alors la seule fenêtre
sur son activité.

Deux détails comptent :

* la lecture démarre **à la fin du fichier**. Rejouer des heures de log à la
  réadoption inonderait la console d'événements passés ;
* le fichier est **rouvert s'il rétrécit**. Minecraft archive ``latest.log`` à
  chaque démarrage et en recrée un vide ; sans cette détection, la lecture
  resterait bloquée sur un descripteur devenu orphelin.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from msm.logging_conf import get_logger

logger = get_logger(__name__)

#: Fichier de log standard d'un serveur Minecraft.
DEFAULT_LOG_RELATIVE = "logs/latest.log"

POLL_INTERVAL_S = 0.5
#: Bornes de lecture, pour ne pas absorber un fichier entier d'un coup.
MAX_CHUNK_BYTES = 256 * 1024


def default_log_path(directory: Path) -> Path:
    return directory / DEFAULT_LOG_RELATIVE


async def tail(
    path: Path,
    *,
    poll_interval_s: float = POLL_INTERVAL_S,
    from_start: bool = False,
) -> AsyncIterator[str]:
    """Itère sur les nouvelles lignes d'un fichier, indéfiniment.

    L'itération se poursuit même si le fichier n'existe pas encore : le serveur
    peut être en train de démarrer.
    """
    position = 0
    started = False
    pending = ""

    while True:
        try:
            if not path.is_file():
                await asyncio.sleep(poll_interval_s)
                continue

            size = path.stat().st_size

            if not started:
                position = 0 if from_start else size
                started = True

            if size < position:
                # Fichier tronqué ou remplacé : on repart de son début.
                logger.debug("log_file_rotated", path=str(path))
                position = 0
                pending = ""

            if size == position:
                await asyncio.sleep(poll_interval_s)
                continue

            with path.open("rb") as handle:
                handle.seek(position)
                raw = handle.read(min(size - position, MAX_CHUNK_BYTES))
                position = handle.tell()

        except OSError as exc:  # pragma: no cover - fichier verrouillé ou effacé
            logger.debug("log_tail_read_failed", path=str(path), error=str(exc))
            await asyncio.sleep(poll_interval_s)
            continue

        text = pending + raw.decode("utf-8", errors="replace")
        lines = text.split("\n")
        # La dernière portion peut être une ligne incomplète : on la garde pour
        # le tour suivant plutôt que de la couper au milieu.
        pending = lines.pop()

        for line in lines:
            yield line.rstrip("\r")


class LogTailer:
    """Tâche de fond qui alimente un puits de lignes depuis un fichier."""

    def __init__(self, path: Path, sink: object) -> None:
        self._path = path
        self._sink = sink
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, name: str = "msm-log-tail") -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=name)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        consume = self._sink
        async for line in tail(self._path):
            consume(line)  # type: ignore[operator]
