"""Interface d'un backend de processus.

Toute la spécificité système de MSM est concentrée ici. Le reste du code ne
manipule que :class:`SpawnedProcess` et ignore s'il tourne sous Linux ou Windows.

Trois opérations seulement, mais elles doivent être rigoureusement isolées :

* :meth:`ProcessBackend.spawn` — créer le processus **dans son propre groupe** ;
* :meth:`ProcessBackend.request_graceful_stop` — demander un arrêt poli ;
* :meth:`ProcessBackend.kill_tree` — tuer l'arbre, *et rien d'autre*.

L'invariant qui gouverne ce module : une opération d'arrêt ne cible jamais qu'un
identifiant de groupe précis, jamais un motif de ligne de commande. C'est ce qui
garantit qu'arrêter un serveur n'en affecte aucun autre.
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from msm.launchers.base import ProcessSpec

#: Taille maximale d'une ligne de log bufferisée (1 Mio).
#: La valeur par défaut d'asyncio (64 Kio) est trop basse : certaines traces Java
#: et messages de mods dépassent cette limite et feraient échouer la lecture.
STREAM_BUFFER_LIMIT = 1024 * 1024


@dataclass(slots=True)
class SpawnedProcess:
    """Un processus lancé et les identifiants permettant d'agir sur lui."""

    process: asyncio.subprocess.Process
    pid: int
    #: Identifiant de groupe (pgid POSIX). ``None`` sous Windows, où l'isolation
    #: repose sur un Job Object ou sur le parcours de l'arbre.
    group_id: int | None = None
    #: Horodatage de création, comparé avant tout signal : un PID peut être
    #: recyclé par le système et désigner un processus totalement étranger.
    create_time: float | None = None
    #: Ressource native de confinement (Job Object Windows), refermée à l'arrêt.
    native_handle: Any = None
    #: Diagnostics non bloquants relevés au lancement (repli utilisé, etc.).
    warnings: list[str] = field(default_factory=list)

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    @property
    def running(self) -> bool:
        return self.process.returncode is None


class ProcessBackend(ABC):
    """Primitives de gestion de processus, déclinées par système d'exploitation."""

    name: ClassVar[str]
    #: Le système offre-t-il un signal d'arrêt poli intermédiaire (SIGTERM) ?
    #: Faux sous Windows : la séquence d'arrêt saute alors cette étape au lieu
    #: d'attendre en vain un signal qui n'aura aucun effet.
    supports_graceful_signal: ClassVar[bool] = True

    @abstractmethod
    async def spawn(self, spec: ProcessSpec) -> SpawnedProcess:
        """Lance le processus dans son propre groupe, tubes ouverts."""

    @abstractmethod
    def request_graceful_stop(self, spawned: SpawnedProcess) -> bool:
        """Demande un arrêt propre au processus (signal, pas destruction).

        Renvoie ``False`` si le signal n'a pas pu être délivré — le processus a
        probablement déjà disparu.
        """

    @abstractmethod
    def kill_tree(self, spawned: SpawnedProcess) -> bool:
        """Termine le processus **et sa descendance**, sans toucher au reste."""

    @abstractmethod
    def is_alive(self, pid: int, create_time: float | None = None) -> bool:
        """Le processus existe-t-il toujours et est-ce bien le même ?

        La comparaison de ``create_time`` évite d'agir sur un PID recyclé — c'est
        exactement la confusion qui rend un `pkill` par motif dangereux.
        """

    def release(self, spawned: SpawnedProcess) -> None:  # noqa: B027 - optionnel par nature
        """Libère les ressources natives associées. Sans effet par défaut."""

    # ---------------------------------------------------------------- #
    #  Aides communes
    # ---------------------------------------------------------------- #
    @staticmethod
    def _build_env(spec: ProcessSpec) -> dict[str, str]:
        """Environnement du processus : celui de MSM, enrichi des variables du serveur."""
        env = os.environ.copy()
        env.update(spec.env)
        return env
