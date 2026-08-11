"""Enveloppe de haut niveau autour d'un processus de serveur Minecraft.

Fournit trois choses au reste du code :

* l'écriture d'une commande sur l'entrée standard, avec détection franche des cas
  où celle-ci n'est pas atteignable (script qui ferme stdin) ;
* la lecture ligne à ligne de la sortie, robuste aux lignes démesurées ;
* la **séquence d'arrêt graduée**, qui est le point le plus sensible du projet.

Séquence d'arrêt ::

    1. « stop » sur l'entrée standard   → arrêt propre, le monde est sauvegardé
    2. attente de stop_timeout_s
    3. signal d'arrêt au groupe          → POSIX uniquement (SIGTERM)
    4. attente de kill_timeout_s
    5. terminaison de l'arbre            → SIGKILL au groupe / TerminateJobObject

Chaque étape ne cible que le groupe de processus de *ce* serveur. Aucune étape ne
recherche de processus par nom ou par ligne de commande.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import Enum

from msm.exceptions import ConsoleUnavailable
from msm.launchers.base import ProcessSpec
from msm.logging_conf import get_logger
from msm.runtime.backends import ProcessBackend, SpawnedProcess, get_backend

logger = get_logger(__name__)

#: Délai de grâce après la terminaison forcée, avant de renoncer.
_POST_KILL_GRACE_S = 10.0


class StopStage(str, Enum):
    """Étape atteinte par la séquence d'arrêt — remontée à l'interface."""

    COMMAND = "command"
    SIGNAL = "signal"
    KILL = "kill"
    ALREADY_STOPPED = "already_stopped"


@dataclass(frozen=True, slots=True)
class StopOutcome:
    """Résultat d'un arrêt."""

    stage: StopStage
    exit_code: int | None
    forced: bool
    duration_s: float


StageCallback = Callable[[StopStage, str], None]


class ProcessHandle:
    """Cycle de vie d'un processus unique, sans aucune notion de serveur Minecraft."""

    __slots__ = ("_backend", "_spawned", "_started_at", "_stdin_broken", "_waiter")

    def __init__(self, backend: ProcessBackend | None = None) -> None:
        self._backend = backend or get_backend()
        self._spawned: SpawnedProcess | None = None
        self._waiter: asyncio.Task[int] | None = None
        self._stdin_broken = False
        self._started_at: float | None = None

    # ------------------------------------------------------------------ #
    #  État
    # ------------------------------------------------------------------ #
    @property
    def backend_name(self) -> str:
        return self._backend.name

    @property
    def started(self) -> bool:
        return self._spawned is not None

    @property
    def running(self) -> bool:
        return self._spawned is not None and self._spawned.running

    @property
    def pid(self) -> int | None:
        return self._spawned.pid if self._spawned else None

    @property
    def group_id(self) -> int | None:
        return self._spawned.group_id if self._spawned else None

    @property
    def create_time(self) -> float | None:
        return self._spawned.create_time if self._spawned else None

    @property
    def exit_code(self) -> int | None:
        return self._spawned.returncode if self._spawned else None

    @property
    def uptime_s(self) -> float:
        if self._started_at is None:
            return 0.0
        return asyncio.get_running_loop().time() - self._started_at

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(self._spawned.warnings) if self._spawned else ()

    @property
    def stdin_available(self) -> bool:
        """L'entrée standard est-elle utilisable pour envoyer des commandes ?

        Un tube dont le transport est en cours de fermeture accepterait encore les
        écritures **sans erreur** tout en les jetant. Le tester explicitement évite
        d'afficher « commande envoyée » alors qu'elle n'atteindra jamais le serveur.
        """
        if self._spawned is None or self._stdin_broken or not self.running:
            return False
        stdin = self._spawned.process.stdin
        return stdin is not None and not stdin.is_closing()

    # ------------------------------------------------------------------ #
    #  Démarrage
    # ------------------------------------------------------------------ #
    async def start(self, spec: ProcessSpec) -> SpawnedProcess:
        """Lance le processus. Une instance ne peut servir qu'une fois."""
        if self._spawned is not None:
            raise RuntimeError("Ce ProcessHandle a déjà été utilisé.")

        spawned = await self._backend.spawn(spec)
        self._spawned = spawned
        self._started_at = asyncio.get_running_loop().time()
        # Tâche unique d'attente : permet des `wait` multiples et des délais
        # d'attente sans jamais annuler l'attente sous-jacente.
        self._waiter = asyncio.create_task(
            spawned.process.wait(), name=f"process-wait-{spawned.pid}"
        )
        logger.info(
            "process_started",
            pid=spawned.pid,
            group_id=spawned.group_id,
            backend=self._backend.name,
            command=spec.display(),
        )
        return spawned

    # ------------------------------------------------------------------ #
    #  Sortie standard
    # ------------------------------------------------------------------ #
    async def iter_output(self) -> AsyncIterator[str]:
        """Itère sur les lignes de sortie, décodées et sans saut de ligne final.

        Une ligne dépassant la capacité du tampon ne casse pas la lecture : elle
        est remplacée par un avertissement explicite et le flux continue.
        """
        if self._spawned is None or self._spawned.process.stdout is None:
            return

        stream = self._spawned.process.stdout
        while True:
            try:
                chunk = await stream.readline()
            except ValueError:
                # Ligne plus longue que STREAM_BUFFER_LIMIT : asyncio a vidé son
                # tampon. On le signale plutôt que de laisser un trou silencieux.
                yield "[MSM] Ligne de log tronquée : elle dépassait la taille maximale (1 Mio)."
                continue
            except (asyncio.IncompleteReadError, ConnectionResetError):
                break

            if not chunk:
                break
            yield chunk.decode("utf-8", errors="replace").rstrip("\r\n")

    # ------------------------------------------------------------------ #
    #  Entrée standard
    # ------------------------------------------------------------------ #
    async def write_line(self, text: str) -> None:
        """Écrit une ligne sur l'entrée standard du processus.

        ``text`` doit déjà avoir été assaini par :func:`msm.core.commands.sanitize_command`.
        """
        if self._spawned is None or not self.running:
            raise ConsoleUnavailable(
                "Le serveur n'est pas en cours d'exécution.",
                cause="Aucun processus actif auquel envoyer la commande.",
                remediation="Démarrer le serveur avant d'envoyer une commande.",
            )

        stdin = self._spawned.process.stdin
        if stdin is None or self._stdin_broken or stdin.is_closing():
            self._stdin_broken = True
            raise ConsoleUnavailable(
                "Console en lecture seule.",
                cause="L'entrée standard du processus n'est pas accessible.",
                remediation=(
                    "Ce script de démarrage ne transmet pas l'entrée standard au serveur. "
                    "Activer le mode PTY ou configurer RCON dans les réglages du serveur."
                ),
            )

        try:
            stdin.write(f"{text}\n".encode())
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
            self._stdin_broken = True
            raise ConsoleUnavailable(
                "Commande non transmise.",
                cause=f"L'entrée standard du serveur s'est fermée ({type(exc).__name__}).",
                remediation=(
                    "Vérifier que le script de démarrage ne redirige pas l'entrée standard "
                    "(par exemple `< /dev/null`), ou configurer RCON."
                ),
            ) from exc

    # ------------------------------------------------------------------ #
    #  Attente et arrêt
    # ------------------------------------------------------------------ #
    async def wait(self) -> int:
        """Attend la fin du processus et renvoie son code de sortie."""
        if self._waiter is None:
            raise RuntimeError("Le processus n'a pas été démarré.")
        return await asyncio.shield(self._waiter)

    async def _wait_for_exit(self, timeout: float) -> bool:
        """Attend au plus ``timeout`` secondes. ``True`` si le processus est sorti."""
        if self._waiter is None:
            return True
        if self._waiter.done():
            return True
        done, _ = await asyncio.wait({self._waiter}, timeout=timeout)
        return bool(done)

    async def stop(
        self,
        *,
        stop_command: str = "stop",
        stop_timeout_s: float = 60.0,
        kill_timeout_s: float = 15.0,
        on_stage: StageCallback | None = None,
    ) -> StopOutcome:
        """Exécute la séquence d'arrêt graduée. Ne cible jamais que ce processus."""
        loop = asyncio.get_running_loop()
        started_at = loop.time()

        def notify(stage: StopStage, message: str) -> None:
            if on_stage is not None:
                on_stage(stage, message)

        if self._spawned is None or not self.running:
            return StopOutcome(StopStage.ALREADY_STOPPED, self.exit_code, False, 0.0)

        # --- Étape 1 : arrêt propre par la console -----------------------
        if stop_command and self.stdin_available:
            try:
                await self.write_line(stop_command)
                notify(
                    StopStage.COMMAND,
                    f"Commande « {stop_command} » envoyée, arrêt propre en cours "
                    f"(jusqu'à {stop_timeout_s:.0f} s).",
                )
                if await self._wait_for_exit(stop_timeout_s):
                    return self._outcome(StopStage.COMMAND, forced=False, started_at=started_at)
            except ConsoleUnavailable as exc:
                notify(StopStage.COMMAND, f"Arrêt propre impossible : {exc.cause}")
        else:
            notify(
                StopStage.COMMAND,
                "Entrée standard indisponible : passage direct à l'arrêt système.",
            )

        # --- Étape 2 : signal d'arrêt au groupe (POSIX) ------------------
        if self._backend.supports_graceful_signal:
            notify(
                StopStage.SIGNAL,
                f"Le serveur n'a pas répondu : envoi du signal d'arrêt au groupe "
                f"de processus (jusqu'à {kill_timeout_s:.0f} s).",
            )
            self._backend.request_graceful_stop(self._spawned)
            if await self._wait_for_exit(kill_timeout_s):
                return self._outcome(StopStage.SIGNAL, forced=True, started_at=started_at)

        # --- Étape 3 : terminaison forcée de l'arbre ---------------------
        notify(
            StopStage.KILL,
            "Arrêt forcé du groupe de processus du serveur.",
        )
        logger.warning("process_force_kill", pid=self.pid, group_id=self.group_id)
        self._backend.kill_tree(self._spawned)
        await self._wait_for_exit(_POST_KILL_GRACE_S)
        return self._outcome(StopStage.KILL, forced=True, started_at=started_at)

    def _outcome(self, stage: StopStage, *, forced: bool, started_at: float) -> StopOutcome:
        duration = asyncio.get_running_loop().time() - started_at
        return StopOutcome(
            stage=stage, exit_code=self.exit_code, forced=forced, duration_s=duration
        )

    async def kill_now(self) -> None:
        """Terminaison immédiate, sans étape intermédiaire (action « Kill » du panel)."""
        if self._spawned is None or not self.running:
            return
        logger.warning("process_kill_now", pid=self.pid, group_id=self.group_id)
        self._backend.kill_tree(self._spawned)
        await self._wait_for_exit(_POST_KILL_GRACE_S)

    async def close(self) -> None:
        """Libère tubes et ressources natives. Idempotent."""
        if self._spawned is None:
            return

        process = self._spawned.process
        if process.stdin is not None:
            with contextlib.suppress(Exception):
                process.stdin.close()

        if self._waiter is not None and not self._waiter.done():
            self._waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._waiter

        self._backend.release(self._spawned)
