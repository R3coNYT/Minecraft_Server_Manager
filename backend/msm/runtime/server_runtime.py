"""Cycle de vie complet d'**un** serveur Minecraft.

Cet objet est le propriétaire exclusif du processus d'un serveur : il détient son
tube d'entrée, lit sa sortie, connaît son état et décide de son redémarrage. Deux
serveurs sont deux instances totalement indépendantes qui ne partagent rien —
c'est la garantie structurelle qu'une action sur l'un ne peut pas atteindre l'autre.

Il ne connaît ni HTTP, ni WebSocket, ni base de données : il publie des événements
sur le bus et expose des méthodes. Cela le rend testable avec un simple faux
serveur en Python, sans Java ni application web.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from msm.bus import EventBus, get_event_bus, topics
from msm.core.commands import sanitize_command
from msm.core.log_line import LogLevel, LogLine
from msm.core.patterns import MinecraftEvent, MinecraftEventKind
from msm.core.restart_policy import RestartPolicy
from msm.core.states import ServerState, assert_transition
from msm.exceptions import (
    ServerAlreadyRunning,
    ServerNotRunning,
    ServerStartFailed,
)
from msm.launchers import LaunchContext
from msm.launchers import registry as launcher_registry
from msm.logging_conf import get_logger
from msm.minecraft import eula as eula_module
from msm.runtime.backends import ProcessBackend, get_backend
from msm.runtime.log_pipeline import LogPipeline
from msm.runtime.log_tailer import LogTailer, default_log_path
from msm.runtime.process_handle import ProcessHandle, StopOutcome, StopStage
from msm.runtime.ring_buffer import RingBuffer
from msm.runtime.stats import EMPTY_STATS, ProcessStats, StatsCollector

logger = get_logger(__name__)

#: Plafond du vestibule d'UUID en attente de connexion confirmée.
_MAX_PENDING_UUIDS = 64

#: Fréquence de vérification de survie d'un processus réadopté.
_ADOPTED_POLL_INTERVAL_S = 2.0


@dataclass(frozen=True, slots=True)
class ServerRuntimeConfig:
    """Configuration nécessaire au pilotage d'un serveur.

    Volontairement dissociée des modèles SQLAlchemy : le runtime se teste sans
    base de données, et un changement de schéma n'impose pas de le modifier.
    """

    id: int
    name: str
    directory: Path
    launcher_key: str
    launch: LaunchContext
    stop_command: str = "stop"
    stop_timeout_s: float = 60.0
    kill_timeout_s: float = 15.0
    start_timeout_s: float = 300.0
    log_history_lines: int = 5000
    stats_interval_s: float = 2.0
    auto_accept_eula: bool = True
    restart_policy: RestartPolicy = field(default_factory=RestartPolicy)


@dataclass(frozen=True, slots=True)
class AdoptedProcess:
    """Un processus retrouvé vivant après un redémarrage de MSM.

    Ses tubes sont définitivement perdus — ils appartenaient au processus MSM
    précédent — mais son identité reste connue, ce qui suffit à le surveiller et
    à l'arrêter.
    """

    pid: int
    group_id: int | None
    create_time: float | None


class ServerRuntime:
    """Pilote d'un serveur : démarrage, arrêt, console, statistiques, redémarrage."""

    def __init__(
        self,
        config: ServerRuntimeConfig,
        *,
        bus: EventBus | None = None,
        backend: ProcessBackend | None = None,
    ) -> None:
        self._config = config
        self._bus = bus or get_event_bus()
        self._backend = backend
        #: Backend résolu, utilisé pour agir sur un processus réadopté.
        self._backend_ref = backend or get_backend()

        self._state = ServerState.OFFLINE
        self._state_since = datetime.now(UTC)
        self._state_reason: str | None = None
        self._last_error: dict[str, str] | None = None

        self._handle: ProcessHandle | None = None
        self._buffer = RingBuffer(config.log_history_lines)
        self._pipeline = LogPipeline(
            self._buffer, on_line=self._publish_line, on_event=self._handle_minecraft_event
        )
        self._stats_collector: StatsCollector | None = None
        self._stats = EMPTY_STATS

        self._lock = asyncio.Lock()
        self._stop_requested = False
        self._consecutive_crashes = 0
        self._started_at: datetime | None = None

        self._reader_task: asyncio.Task[None] | None = None
        self._stats_task: asyncio.Task[None] | None = None
        self._supervise_task: asyncio.Task[None] | None = None
        self._readiness_task: asyncio.Task[None] | None = None
        self._restart_task: asyncio.Task[None] | None = None

        self._online_players: dict[str, str | None] = {}
        #: UUID annoncés mais dont la connexion n'est pas encore confirmée.
        self._pending_uuids: dict[str, str] = {}

        #: Processus réadopté, sans tubes ; exclusif de ``_handle``.
        self._adopted: AdoptedProcess | None = None
        self._tailer: LogTailer | None = None
        self._liveness_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ #
    #  Lecture d'état
    # ------------------------------------------------------------------ #
    @property
    def config(self) -> ServerRuntimeConfig:
        return self._config

    @property
    def id(self) -> int:
        return self._config.id

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def state(self) -> ServerState:
        return self._state

    @property
    def pid(self) -> int | None:
        if self._adopted is not None:
            return self._adopted.pid
        return self._handle.pid if self._handle else None

    @property
    def group_id(self) -> int | None:
        if self._adopted is not None:
            return self._adopted.group_id
        return self._handle.group_id if self._handle else None

    @property
    def process_create_time(self) -> float | None:
        """Date de création du processus, comparée au PID lors d'une réadoption."""
        if self._adopted is not None:
            return self._adopted.create_time
        return self._handle.create_time if self._handle else None

    @property
    def adopted(self) -> bool:
        """Le serveur tourne-t-il sans que MSM ne détienne ses tubes ?"""
        return self._adopted is not None

    @property
    def stats(self) -> ProcessStats:
        return self._stats

    @property
    def online_players(self) -> tuple[str, ...]:
        return tuple(sorted(self._online_players))

    @property
    def online_player_map(self) -> dict[str, str | None]:
        """Joueurs connectés et leur UUID quand le serveur l'a annoncé."""
        return dict(self._online_players)

    @property
    def uptime_s(self) -> float:
        if self._started_at is None or not self._state.is_running:
            return 0.0
        return (datetime.now(UTC) - self._started_at).total_seconds()

    def snapshot(self) -> dict[str, Any]:
        """État complet, tel qu'exposé par l'API et le WebSocket."""
        return {
            "id": self._config.id,
            "name": self._config.name,
            "state": self._state.value,
            "state_since": self._state_since.isoformat(),
            "state_reason": self._state_reason,
            "pid": self.pid,
            "uptime_s": round(self.uptime_s, 1),
            "players_online": len(self._online_players),
            "players": list(self.online_players),
            "consecutive_crashes": self._consecutive_crashes,
            "console_writable": bool(self._handle and self._handle.stdin_available),
            "adopted": self._adopted is not None,
            "last_error": self._last_error,
            "stats": self._stats.to_dict(),
            "log_seq": self._pipeline.last_seq,
            "log_dropped": self._buffer.dropped,
        }

    # ------------------------------------------------------------------ #
    #  Console : historique et recherche
    # ------------------------------------------------------------------ #
    def logs_tail(self, limit: int = 500) -> list[LogLine]:
        return self._buffer.tail(limit)

    def logs_since(self, seq: int, *, limit: int | None = None) -> list[LogLine]:
        return self._buffer.since(seq, limit=limit)

    def logs_before(self, seq: int, *, limit: int = 500) -> list[LogLine]:
        return self._buffer.before(seq, limit=limit)

    def logs_search(
        self, query: str, *, limit: int = 200, use_regex: bool = False
    ) -> list[LogLine]:
        return self._buffer.search(query, limit=limit, use_regex=use_regex)

    # ------------------------------------------------------------------ #
    #  Démarrage
    # ------------------------------------------------------------------ #
    async def start(self, *, actor: str | None = None) -> None:
        """Démarre le serveur. Lève si l'état ou la configuration l'interdisent."""
        async with self._lock:
            await self._start_locked(actor=actor)

    async def _start_locked(self, *, actor: str | None) -> None:
        if self._adopted is not None and self._backend_ref.is_alive(
            self._adopted.pid, self._adopted.create_time
        ):
            raise ServerAlreadyRunning(
                f"Le serveur « {self._config.name} » tourne déjà.",
                cause=(
                    f"Un processus (PID {self._adopted.pid}) survivant à un redémarrage "
                    "de MSM est toujours actif."
                ),
                remediation="Arrêter ce serveur avant de le relancer.",
            )

        if self._state.is_running:
            raise ServerAlreadyRunning(
                f"Le serveur « {self._config.name} » est déjà en cours d'exécution.",
                cause=f"Son état actuel est {self._state.value}.",
                remediation="Utiliser « Redémarrer » pour le relancer.",
            )

        self._cancel_pending_restart()
        self._stop_requested = False
        self._last_error = None
        self._set_state(ServerState.STARTING, reason=f"Démarrage demandé par {actor or 'MSM'}")
        self._pipeline.emit_system(
            f"Démarrage du serveur « {self._config.name} » demandé par {actor or 'MSM'}."
        )

        try:
            spec = self._build_spec()
            self._handle_eula()
        except Exception as exc:
            self._fail_start(exc)
            raise

        self._pipeline.emit_system(f"Commande : {spec.display()}")

        handle = ProcessHandle(self._backend)
        try:
            spawned = await handle.start(spec)
        except FileNotFoundError as exc:
            self._fail_start(
                ServerStartFailed(
                    "Impossible de démarrer le serveur.",
                    cause=f"Le programme « {spec.argv[0]} » est introuvable.",
                    remediation="Vérifier le chemin de Java ou du script dans les réglages.",
                )
            )
            raise ServerStartFailed(
                "Impossible de démarrer le serveur.",
                cause=f"Le programme « {spec.argv[0]} » est introuvable.",
                remediation="Vérifier le chemin de Java ou du script dans les réglages.",
            ) from exc
        except PermissionError as exc:
            error = ServerStartFailed(
                "Impossible de démarrer le serveur.",
                cause=f"Droits insuffisants pour exécuter « {spec.argv[0]} ».",
                remediation=f"Sous Linux : chmod +x {spec.argv[0]}",
            )
            self._fail_start(error)
            raise error from exc
        except OSError as exc:
            error = ServerStartFailed(
                "Impossible de démarrer le serveur.",
                cause=str(exc),
                remediation="Vérifier la configuration de démarrage du serveur.",
            )
            self._fail_start(error)
            raise error from exc

        for warning in handle.warnings:
            self._pipeline.emit_system(warning, level=LogLevel.WARN)

        self._handle = handle
        self._adopted = None
        self._started_at = datetime.now(UTC)
        self._stats_collector = StatsCollector(spawned.pid)
        self._online_players.clear()
        self._pending_uuids.clear()

        self._reader_task = asyncio.create_task(
            self._pump_output(handle), name=f"msm-logs-{self.id}"
        )
        self._stats_task = asyncio.create_task(self._pump_stats(), name=f"msm-stats-{self.id}")
        self._supervise_task = asyncio.create_task(
            self._supervise(handle), name=f"msm-supervise-{self.id}"
        )
        self._readiness_task = asyncio.create_task(
            self._watch_readiness(), name=f"msm-ready-{self.id}"
        )

        logger.info(
            "server_started",
            server_id=self.id,
            server=self._config.name,
            pid=spawned.pid,
            group_id=spawned.group_id,
        )

    def _build_spec(self) -> Any:
        launcher = launcher_registry.get(self._config.launcher_key)
        return launcher.build_spec(self._config.launch)

    def _handle_eula(self) -> None:
        """Accepte le CLUF si l'administrateur l'a autorisé, et le trace."""
        status = eula_module.read_status(self._config.directory)
        if not status.needs_acceptance:
            return

        if not self._config.auto_accept_eula:
            self._pipeline.emit_system(
                "Le CLUF Minecraft (eula.txt) n'est pas accepté : le serveur s'arrêtera "
                "immédiatement. Activer l'acceptation automatique dans les réglages, "
                "ou passer eula=true manuellement.",
                level=LogLevel.WARN,
            )
            return

        if eula_module.accept(self._config.directory):
            self._pipeline.emit_system(
                "CLUF Minecraft accepté automatiquement (eula.txt : eula=true)."
            )

    def _fail_start(self, exc: Exception) -> None:
        """Ramène l'état à OFFLINE et trace la cause dans la console."""
        cause = getattr(exc, "cause", None) or str(exc)
        remediation = getattr(exc, "remediation", None)
        self._last_error = {
            "code": getattr(exc, "code", "SERVER_START_FAILED"),
            "message": getattr(exc, "message", str(exc)),
            "cause": cause or "",
            "remediation": remediation or "",
        }
        self._pipeline.emit_system(f"Échec du démarrage : {cause}", level=LogLevel.ERROR)
        if remediation:
            self._pipeline.emit_system(f"Action corrective : {remediation}", level=LogLevel.ERROR)
        self._set_state(ServerState.OFFLINE, reason=cause)
        logger.warning("server_start_failed", server_id=self.id, cause=cause)

    # ------------------------------------------------------------------ #
    #  Réadoption après un redémarrage de MSM
    # ------------------------------------------------------------------ #
    async def adopt(
        self,
        pid: int,
        *,
        group_id: int | None = None,
        create_time: float | None = None,
        started_at: datetime | None = None,
    ) -> bool:
        """Reprend la surveillance d'un processus survivant à MSM.

        Renvoie ``False`` si le processus n'existe plus, ou si le PID a été
        recyclé par le système — la comparaison de la date de création est ce
        qui évite d'adopter un processus totalement étranger.

        L'état résultant est ``UNKNOWN`` et non ``ONLINE`` : MSM sait que le
        processus vit, mais ne peut ni lui parler ni garantir qu'il a fini de
        démarrer. Le prétendre en ligne serait une information inventée.
        """
        if not self._backend_ref.is_alive(pid, create_time):
            return False

        self._adopted = AdoptedProcess(pid=pid, group_id=group_id, create_time=create_time)
        self._started_at = started_at or datetime.now(UTC)
        self._stats_collector = StatsCollector(pid)

        self._pipeline.emit_system(
            f"Serveur réadopté après un redémarrage de MSM (PID {pid}). "
            "La console est en lecture seule : les commandes ne peuvent plus être "
            "transmises, mais l'arrêt reste possible.",
            level=LogLevel.WARN,
        )

        self._set_state(ServerState.UNKNOWN, reason="Réadopté après un redémarrage de MSM.")

        self._start_log_tailing()
        self._stats_task = asyncio.create_task(self._pump_stats(), name=f"msm-stats-{self.id}")
        self._liveness_task = asyncio.create_task(
            self._watch_adopted(), name=f"msm-liveness-{self.id}"
        )

        logger.info("server_adopted", server_id=self.id, server=self._config.name, pid=pid)
        return True

    def _start_log_tailing(self) -> None:
        """Suit ``logs/latest.log`` : seule fenêtre restante sur l'activité."""
        path = default_log_path(self._config.directory)
        if not path.parent.is_dir():
            self._pipeline.emit_system(
                f"Aucun dossier de logs trouvé ({path.parent}) : la console restera "
                "vide tant que le serveur n'en écrira pas.",
                level=LogLevel.WARN,
            )
        self._tailer = LogTailer(path, self._pipeline.ingest)
        self._tailer.start(name=f"msm-log-tail-{self.id}")

    async def _watch_adopted(self) -> None:
        """Surveille la disparition d'un processus réadopté."""
        while self._adopted is not None:
            await asyncio.sleep(_ADOPTED_POLL_INTERVAL_S)
            adopted = self._adopted
            if adopted is None:
                return
            if self._backend_ref.is_alive(adopted.pid, adopted.create_time):
                continue

            self._pipeline.emit_system("Le serveur réadopté s'est arrêté.")
            await self._release_adopted()
            self._set_state(ServerState.OFFLINE, reason="Le processus réadopté a disparu.")
            return

    async def _release_adopted(self) -> None:
        """Libère les ressources liées à un processus réadopté."""
        self._adopted = None
        self._stats = EMPTY_STATS
        self._online_players.clear()
        self._pending_uuids.clear()
        if self._tailer is not None:
            await self._tailer.stop()
            self._tailer = None
        self._cancel_task("_stats_task")

    async def _stop_adopted(self, *, force: bool) -> StopOutcome:
        """Arrête un processus réadopté, sans passer par sa console.

        Sous POSIX, ``SIGTERM`` déclenche les crochets d'arrêt de la JVM : le
        monde est sauvegardé. Sous Windows, faute d'équivalent, l'arrêt est
        brutal — ce que la console annonce explicitement.
        """
        adopted = self._adopted
        assert adopted is not None
        loop = asyncio.get_running_loop()
        started_at = loop.time()

        if self._backend_ref.supports_graceful_signal and not force:
            self._pipeline.emit_system(
                "Signal d'arrêt envoyé au groupe de processus du serveur réadopté : "
                "le monde sera sauvegardé."
            )
            self._backend_ref.terminate_external(
                adopted.pid, adopted.group_id, adopted.create_time, force=False
            )
            if await self._wait_until_gone(adopted, self._config.stop_timeout_s):
                return await self._finish_adopted_stop(
                    StopStage.SIGNAL, forced=False, started_at=started_at
                )

        self._pipeline.emit_system(
            "Terminaison forcée du serveur réadopté : le monde n'est pas sauvegardé.",
            level=LogLevel.WARN,
        )
        self._backend_ref.terminate_external(
            adopted.pid, adopted.group_id, adopted.create_time, force=True
        )
        await self._wait_until_gone(adopted, self._config.kill_timeout_s)
        return await self._finish_adopted_stop(StopStage.KILL, forced=True, started_at=started_at)

    async def _wait_until_gone(self, adopted: AdoptedProcess, timeout: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if not self._backend_ref.is_alive(adopted.pid, adopted.create_time):
                return True
            await asyncio.sleep(0.2)
        return not self._backend_ref.is_alive(adopted.pid, adopted.create_time)

    async def _finish_adopted_stop(
        self, stage: StopStage, *, forced: bool, started_at: float
    ) -> StopOutcome:
        self._cancel_task("_liveness_task")
        await self._release_adopted()
        self._set_state(ServerState.OFFLINE, reason="Serveur arrêté.")
        return StopOutcome(
            stage=stage,
            exit_code=None,
            forced=forced,
            duration_s=asyncio.get_running_loop().time() - started_at,
        )

    # ------------------------------------------------------------------ #
    #  Arrêt
    # ------------------------------------------------------------------ #
    async def stop(self, *, actor: str | None = None) -> StopOutcome:
        """Arrête le serveur selon la séquence graduée."""
        async with self._lock:
            self._cancel_pending_restart()

            if self._adopted is not None:
                self._set_state(ServerState.STOPPING, reason=f"Arrêt demandé par {actor or 'MSM'}")
                self._pipeline.emit_system(
                    f"Arrêt du serveur réadopté « {self._config.name} » demandé par "
                    f"{actor or 'MSM'}."
                )
                return await self._stop_adopted(force=False)

            if self._handle is None or not self._handle.running:
                raise ServerNotRunning(
                    f"Le serveur « {self._config.name} » n'est pas en cours d'exécution.",
                    cause=f"Son état actuel est {self._state.value}.",
                    remediation="Démarrer le serveur avant de tenter de l'arrêter.",
                )

            self._stop_requested = True
            self._set_state(ServerState.STOPPING, reason=f"Arrêt demandé par {actor or 'MSM'}")
            self._pipeline.emit_system(
                f"Arrêt du serveur « {self._config.name} » demandé par {actor or 'MSM'}."
            )
            handle = self._handle

        # Hors verrou : l'arrêt peut durer une minute, les lectures d'état ne
        # doivent pas être bloquées pendant ce temps.
        outcome = await handle.stop(
            stop_command=self._config.stop_command,
            stop_timeout_s=self._config.stop_timeout_s,
            kill_timeout_s=self._config.kill_timeout_s,
            on_stage=self._on_stop_stage,
        )
        logger.info(
            "server_stopped",
            server_id=self.id,
            stage=outcome.stage.value,
            forced=outcome.forced,
            exit_code=outcome.exit_code,
            duration_s=round(outcome.duration_s, 1),
        )
        return outcome

    def _on_stop_stage(self, stage: StopStage, message: str) -> None:
        level = LogLevel.WARN if stage in (StopStage.SIGNAL, StopStage.KILL) else LogLevel.INFO
        self._pipeline.emit_system(message, level=level)

    async def kill(self, *, actor: str | None = None) -> None:
        """Terminaison immédiate, sans arrêt propre. Réservée aux administrateurs."""
        async with self._lock:
            self._cancel_pending_restart()

            if self._adopted is not None:
                self._set_state(ServerState.STOPPING, reason=f"Arrêt forcé par {actor or 'MSM'}")
                await self._stop_adopted(force=True)
                return

            if self._handle is None or not self._handle.running:
                raise ServerNotRunning(
                    f"Le serveur « {self._config.name} » n'est pas en cours d'exécution.",
                    cause=f"Son état actuel est {self._state.value}.",
                    remediation="Aucune action nécessaire.",
                )
            self._stop_requested = True
            self._set_state(ServerState.STOPPING, reason=f"Arrêt forcé par {actor or 'MSM'}")
            self._pipeline.emit_system(
                f"Arrêt FORCÉ demandé par {actor or 'MSM'} : le monde ne sera pas sauvegardé.",
                level=LogLevel.WARN,
            )
            handle = self._handle

        await handle.kill_now()

    async def restart(self, *, actor: str | None = None) -> None:
        """Arrête puis relance le serveur."""
        if self._state.is_running:
            await self.stop(actor=actor)
            await self._wait_until_stopped()
        await self.start(actor=actor)

    async def _wait_until_stopped(self, timeout: float = 30.0) -> None:
        """Attend que le superviseur ait constaté la sortie du processus."""
        deadline = asyncio.get_running_loop().time() + timeout
        while self._state.is_running and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)

    # ------------------------------------------------------------------ #
    #  Console
    # ------------------------------------------------------------------ #
    async def send_command(self, command: str, *, actor: str | None = None) -> str:
        """Envoie une commande à la console du serveur.

        La commande est assainie ici aussi : le contrôle des permissions se fait
        en amont, mais la protection contre l'injection de sauts de ligne ne doit
        dépendre d'aucun appelant.
        """
        clean = sanitize_command(command)

        if self._handle is None or not self._handle.running:
            raise ServerNotRunning(
                f"Le serveur « {self._config.name} » n'est pas en cours d'exécution.",
                cause="Aucune console active.",
                remediation="Démarrer le serveur avant d'envoyer une commande.",
            )

        await self._handle.write_line(clean)
        self._pipeline.echo_command(clean, actor=actor)
        logger.info("command_sent", server_id=self.id, actor=actor, command=clean)
        return clean

    # ------------------------------------------------------------------ #
    #  Tâches de fond
    # ------------------------------------------------------------------ #
    async def _pump_output(self, handle: ProcessHandle) -> None:
        """Lit la sortie du processus jusqu'à sa fermeture."""
        try:
            async for raw in handle.iter_output():
                self._pipeline.ingest(raw)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - filet de sécurité
            logger.exception("log_reader_crashed", server_id=self.id)

    async def _pump_stats(self) -> None:
        """Relève périodiquement CPU et mémoire tant que le serveur tourne."""
        interval = self._config.stats_interval_s
        topic = topics.server_topic(self.id, topics.STATS)
        try:
            while self._handle is not None and self._handle.running:
                await asyncio.sleep(interval)
                if self._stats_collector is None:
                    continue
                loop_time = asyncio.get_running_loop().time()
                self._stats = self._stats_collector.collect(now=loop_time, uptime_s=self.uptime_s)
                # Aucun abonné : inutile de sérialiser et de publier.
                if self._bus.has_subscribers(topic):
                    self._bus.publish(topic, self._stats.to_dict())
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover
            logger.exception("stats_collector_crashed", server_id=self.id)

    async def _watch_readiness(self) -> None:
        """Surveille l'absence de message « Done » au-delà du délai de démarrage.

        Le processus n'est **pas** tué : un modpack lourd peut mettre plusieurs
        minutes, et certains forks n'émettent pas la ligne attendue. Le serveur
        est alors considéré en ligne, avec un avertissement explicite — préférable
        à un état bloqué sur « Démarrage » ou à l'arrêt d'un serveur sain.
        """
        try:
            await asyncio.sleep(self._config.start_timeout_s)
        except asyncio.CancelledError:
            return

        if self._state is not ServerState.STARTING:
            return
        if self._handle is None or not self._handle.running:
            return

        self._pipeline.emit_system(
            f"Aucun message de fin de démarrage détecté après "
            f"{self._config.start_timeout_s:.0f} s, mais le processus fonctionne. "
            "Le serveur est considéré comme en ligne.",
            level=LogLevel.WARN,
        )
        self._set_state(ServerState.ONLINE, reason="Démarrage supposé terminé (délai dépassé)")

    async def _supervise(self, handle: ProcessHandle) -> None:
        """Attend la fin du processus et applique la politique de redémarrage."""
        try:
            exit_code = await handle.wait()
        except asyncio.CancelledError:
            raise

        uptime = self.uptime_s
        self._cancel_task("_readiness_task")
        await self._drain_reader()
        await handle.close()

        if self._config.restart_policy.is_stable(uptime):
            self._consecutive_crashes = 0

        crashed = not self._stop_requested and exit_code != 0

        if crashed:
            self._consecutive_crashes += 1
            reason = f"Le serveur s'est arrêté de façon inattendue (code {exit_code})."
            self._pipeline.emit_system(reason, level=LogLevel.ERROR)
            self._set_state(ServerState.CRASHED, reason=reason)
            self._bus.publish(
                topics.server_topic(self.id, topics.CRASH),
                {
                    "server_id": self.id,
                    # Le nom voyage avec l'événement : un consommateur hors du
                    # navigateur — une notification Discord — n'a pas de liste de
                    # serveurs sous la main pour le retrouver.
                    "server": self._config.name,
                    "exit_code": exit_code,
                    "reason": reason,
                    "consecutive_crashes": self._consecutive_crashes,
                    "last_lines": [line.to_dict() for line in self._buffer.tail(30)],
                },
            )
        else:
            reason = (
                "Serveur arrêté."
                if self._stop_requested
                else "Le serveur s'est arrêté de lui-même (code 0)."
            )
            self._pipeline.emit_system(reason)
            self._set_state(ServerState.OFFLINE, reason=reason)

        self._stats = EMPTY_STATS
        self._online_players.clear()
        self._pending_uuids.clear()
        self._publish_players()

        decision = self._config.restart_policy.evaluate(
            stop_requested=self._stop_requested,
            exit_code=exit_code,
            consecutive_crashes=self._consecutive_crashes,
        )
        if decision.should_restart:
            self._schedule_restart(decision.delay_s, decision.reason)
        elif decision.reason and not self._stop_requested:
            self._pipeline.emit_system(decision.reason, level=LogLevel.WARN)

    async def _drain_reader(self, timeout: float = 2.0) -> None:
        """Laisse le lecteur consommer les dernières lignes avant de conclure."""
        if self._reader_task is None:
            return
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(self._reader_task), timeout=timeout)

    # ------------------------------------------------------------------ #
    #  Redémarrage automatique
    # ------------------------------------------------------------------ #
    def _schedule_restart(self, delay_s: float, reason: str) -> None:
        self._pipeline.emit_system(
            f"{reason} Nouvelle tentative dans {delay_s:.0f} s.", level=LogLevel.WARN
        )
        self._bus.publish(
            topics.server_topic(self.id, topics.RESTART_SCHEDULED),
            {
                "server_id": self.id,
                "server": self._config.name,
                "delay_s": delay_s,
                "reason": reason,
            },
        )
        self._restart_task = asyncio.create_task(
            self._restart_after(delay_s), name=f"msm-restart-{self.id}"
        )

    async def _restart_after(self, delay_s: float) -> None:
        try:
            await asyncio.sleep(delay_s)
        except asyncio.CancelledError:
            self._pipeline.emit_system("Redémarrage automatique annulé.")
            raise

        # Le délai est écoulé : cette tâche n'est plus « en attente ». Sans cette
        # remise à zéro, le `_cancel_pending_restart()` exécuté au début de
        # `start()` annulerait la tâche qui est précisément en train d'appeler
        # `start()` — le serveur resterait bloqué sur STARTING.
        self._restart_task = None

        try:
            await self.start(actor="redémarrage automatique")
        except Exception as exc:  # pragma: no cover - déjà tracé par _fail_start
            logger.warning("auto_restart_failed", server_id=self.id, error=str(exc))

    def _cancel_pending_restart(self) -> None:
        task = self._restart_task
        self._restart_task = None
        if task is None or task.done():
            return
        # Garde-fou : ne jamais s'auto-annuler si l'appel vient de la tâche
        # de redémarrage elle-même.
        if task is asyncio.current_task():
            return
        task.cancel()

    # ------------------------------------------------------------------ #
    #  Événements Minecraft
    # ------------------------------------------------------------------ #
    def _handle_minecraft_event(self, event: MinecraftEvent) -> None:
        match event.kind:
            case MinecraftEventKind.SERVER_READY:
                if self._state is ServerState.STARTING:
                    self._cancel_task("_readiness_task")
                    self._set_state(ServerState.ONLINE, reason="Démarrage terminé.")

            case MinecraftEventKind.SERVER_STOPPING:
                if self._state is ServerState.ONLINE:
                    self._set_state(ServerState.STOPPING, reason="Arrêt en cours.")

            case MinecraftEventKind.PLAYER_JOIN if event.username:
                # L'UUID a été annoncé quelques lignes plus tôt : on le récupère
                # dans le vestibule plutôt que de le perdre.
                uuid = self._pending_uuids.pop(event.username, None)
                self._online_players[event.username] = uuid
                self._bus.publish(
                    topics.server_topic(self.id, topics.PLAYER_JOIN),
                    {"server_id": self.id, "username": event.username, "uuid": uuid},
                )
                self._publish_players()

            case MinecraftEventKind.PLAYER_LEAVE if event.username:
                uuid = self._online_players.pop(event.username, None)
                self._bus.publish(
                    topics.server_topic(self.id, topics.PLAYER_LEAVE),
                    {"server_id": self.id, "username": event.username, "uuid": uuid},
                )
                self._publish_players()

            case MinecraftEventKind.PLAYER_UUID if event.username:
                # « UUID of player X is … » précède « X joined the game » de
                # quelques lignes. Le joueur n'est donc pas encore connu : on
                # met l'UUID de côté en attendant sa connexion effective.
                if event.uuid:
                    self._remember_uuid(event.username, event.uuid)

            case MinecraftEventKind.PLAYER_LIST:
                self._online_players = {
                    name: self._online_players.get(name) for name in event.players
                }
                self._publish_players()

            case MinecraftEventKind.FATAL:
                self._last_error = {
                    "code": "SERVER_FATAL",
                    "message": "Le serveur a signalé une erreur fatale.",
                    "cause": event.cause or "",
                    "remediation": event.remediation or "",
                }
                if event.remediation:
                    self._pipeline.emit_system(
                        f"Action corrective : {event.remediation}", level=LogLevel.ERROR
                    )

            case _:
                pass

    # ------------------------------------------------------------------ #
    #  Publication
    # ------------------------------------------------------------------ #
    def _set_state(self, target: ServerState, *, reason: str | None = None) -> None:
        if target is self._state:
            return
        assert_transition(self._state, target, server=self._config.name)
        previous = self._state
        self._state = target
        self._state_since = datetime.now(UTC)
        self._state_reason = reason

        logger.info(
            "server_state_changed",
            server_id=self.id,
            server=self._config.name,
            previous=previous.value,
            state=target.value,
            reason=reason,
        )
        self._bus.publish(topics.server_topic(self.id, topics.STATUS), self.snapshot())

    def _publish_line(self, line: LogLine) -> None:
        self._bus.publish(topics.server_topic(self.id, topics.LOG), line)

    def _remember_uuid(self, username: str, uuid: str) -> None:
        """Mémorise un UUID annoncé, en bornant le vestibule.

        Une tentative de connexion peut échouer après l'annonce de l'UUID (liste
        blanche, bannissement) : sans limite, ces entrées s'accumuleraient pour
        la durée de vie du serveur.
        """
        if username in self._online_players:
            self._online_players[username] = uuid
            return
        if len(self._pending_uuids) >= _MAX_PENDING_UUIDS:
            self._pending_uuids.pop(next(iter(self._pending_uuids)), None)
        self._pending_uuids[username] = uuid

    def _publish_players(self) -> None:
        self._bus.publish(
            topics.server_topic(self.id, topics.PLAYERS),
            {
                "server_id": self.id,
                "count": len(self._online_players),
                "players": [
                    {"username": name, "uuid": uuid}
                    for name, uuid in sorted(self._online_players.items())
                ],
            },
        )

    # ------------------------------------------------------------------ #
    #  Arrêt de MSM
    # ------------------------------------------------------------------ #
    def _cancel_task(self, attribute: str) -> None:
        task: asyncio.Task[Any] | None = getattr(self, attribute, None)
        setattr(self, attribute, None)
        if task is not None and not task.done():
            task.cancel()

    async def detach(self) -> None:
        """Libère les tâches sans toucher au processus du serveur.

        Utilisé à l'arrêt de MSM : les serveurs Minecraft continuent de tourner et
        seront réadoptés au redémarrage du panel. Couper une partie en cours parce
        que l'interface d'administration redémarre serait le pire des comportements.
        """
        self._cancel_pending_restart()
        for attribute in (
            "_readiness_task",
            "_stats_task",
            "_supervise_task",
            "_reader_task",
            "_liveness_task",
        ):
            self._cancel_task(attribute)

        if self._tailer is not None:
            await self._tailer.stop()
            self._tailer = None

        running = (self._handle is not None and self._handle.running) or self._adopted is not None
        if running and self._state is not ServerState.UNKNOWN:
            self._set_state(ServerState.UNKNOWN, reason="MSM s'est arrêté ; serveur détaché.")
