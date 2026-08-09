"""Mesure de consommation CPU et mémoire d'un serveur.

Deux subtilités justifient un module dédié plutôt qu'un appel direct à psutil :

* **le processus lancé n'est pas toujours celui qui consomme**. Avec un ``run.sh``,
  le processus direct est un shell de quelques kilo-octets ; le vrai serveur est
  son descendant Java. Les statistiques doivent donc porter sur l'arbre entier.
* ``cpu_percent()`` mesure un *écart* entre deux appels sur le **même objet**
  psutil. Recréer l'objet à chaque relevé renverrait systématiquement 0. Les
  objets sont donc conservés d'un relevé à l'autre.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

import psutil

from msm.logging_conf import get_logger

logger = get_logger(__name__)

#: Intervalle minimal entre deux redécouvertes de l'arbre de processus.
_REFRESH_CHILDREN_EVERY_S = 10.0


@dataclass(frozen=True, slots=True)
class ProcessStats:
    """Instantané de consommation d'un serveur."""

    #: Pourcentage CPU cumulé de l'arbre, rapporté à un cœur (peut dépasser 100).
    cpu_percent: float
    #: Mémoire résidente cumulée, en mébioctets.
    memory_mb: float
    #: Nombre de processus dans l'arbre (1 pour un JAR lancé directement).
    process_count: int
    #: PID du processus Java réellement porteur du serveur, s'il est identifiable.
    java_pid: int | None = None
    uptime_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_mb": round(self.memory_mb, 1),
            "process_count": self.process_count,
            "java_pid": self.java_pid,
            "uptime_s": round(self.uptime_s, 1),
        }


EMPTY_STATS = ProcessStats(cpu_percent=0.0, memory_mb=0.0, process_count=0)


class StatsCollector:
    """Collecteur de statistiques pour un arbre de processus donné."""

    __slots__ = ("_java_pid", "_last_refresh", "_root", "_root_pid", "_tracked")

    def __init__(self, root_pid: int) -> None:
        self._root_pid = root_pid
        self._root: psutil.Process | None = None
        self._tracked: dict[int, psutil.Process] = {}
        self._last_refresh = 0.0
        self._java_pid: int | None = None

    @property
    def java_pid(self) -> int | None:
        """PID du processus Java de l'arbre, découvert au premier relevé."""
        return self._java_pid

    def collect(self, *, now: float, uptime_s: float = 0.0) -> ProcessStats:
        """Relève les statistiques courantes. Ne lève jamais."""
        try:
            self._refresh(now)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return EMPTY_STATS

        if not self._tracked:
            return EMPTY_STATS

        cpu = 0.0
        memory_bytes = 0
        dead: list[int] = []

        for pid, process in self._tracked.items():
            try:
                with process.oneshot():
                    cpu += process.cpu_percent(None)
                    memory_bytes += process.memory_info().rss
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                dead.append(pid)
            except psutil.AccessDenied:
                # Processus hors de portée des droits de MSM : on l'ignore
                # silencieusement plutôt que de fausser tout le relevé.
                continue

        for pid in dead:
            self._tracked.pop(pid, None)
            if pid == self._java_pid:
                self._java_pid = None

        return ProcessStats(
            cpu_percent=cpu,
            memory_mb=memory_bytes / (1024 * 1024),
            process_count=len(self._tracked),
            java_pid=self._java_pid,
            uptime_s=uptime_s,
        )

    # ------------------------------------------------------------------ #
    def _refresh(self, now: float) -> None:
        """Redécouvre périodiquement les descendants du processus racine."""
        if self._root is None:
            self._root = psutil.Process(self._root_pid)
            self._prime(self._root)
            self._tracked[self._root_pid] = self._root
            self._last_refresh = 0.0  # force une découverte immédiate

        if now - self._last_refresh < _REFRESH_CHILDREN_EVERY_S:
            return
        self._last_refresh = now

        if not self._root.is_running():
            self._tracked.pop(self._root_pid, None)
            return

        for child in self._root.children(recursive=True):
            if child.pid in self._tracked:
                continue
            self._prime(child)
            self._tracked[child.pid] = child

        if self._java_pid is None:
            self._java_pid = self._find_java_pid()

    @staticmethod
    def _prime(process: psutil.Process) -> None:
        """Premier appel à ``cpu_percent`` : établit la référence de mesure."""
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            process.cpu_percent(None)

    def _find_java_pid(self) -> int | None:
        """Identifie le processus Java de l'arbre (cas d'un lancement par script)."""
        for pid, process in self._tracked.items():
            try:
                name = process.name().casefold()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if name.startswith("java"):
                return pid
        return None


def find_descendants(pid: int) -> list[int]:
    """PID de tous les descendants d'un processus. Liste vide en cas d'échec."""
    try:
        return [child.pid for child in psutil.Process(pid).children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


def system_stats() -> dict[str, Any]:
    """Statistiques globales de la machine hôte, pour le tableau de bord."""
    memory = psutil.virtual_memory()
    try:
        disk = psutil.disk_usage(".")
        disk_info = {
            "disk_total_gb": round(disk.total / 1024**3, 1),
            "disk_used_gb": round(disk.used / 1024**3, 1),
            "disk_percent": disk.percent,
        }
    except OSError:  # pragma: no cover - dépend du système de fichiers
        disk_info = {}

    return {
        "cpu_percent": psutil.cpu_percent(None),
        "cpu_count": psutil.cpu_count(logical=True) or 1,
        "memory_total_mb": round(memory.total / (1024 * 1024), 1),
        "memory_used_mb": round((memory.total - memory.available) / (1024 * 1024), 1),
        "memory_percent": memory.percent,
        **disk_info,
    }
