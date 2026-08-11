"""Superviseur : registre des serveurs pilotés par cette instance de MSM.

Point d'entrée unique du runtime pour les couches supérieures. Il ne fait
volontairement presque rien lui-même — il détient les :class:`ServerRuntime` et
délègue. Toute la logique d'un serveur reste dans son propre runtime, ce qui évite
le gestionnaire monolithique qui finit par tout savoir sur tout le monde.

Le superviseur est **propriétaire des processus**, ce qui impose que MSM tourne en
un seul processus applicatif (uvicorn, un worker). Répartir les serveurs sur
plusieurs workers reviendrait à ce que personne ne sache qui détient quel PID.
La montée en charge passe par l'asynchrone, et plus tard par des agents distants.
"""

from __future__ import annotations

import asyncio
from typing import Any

from msm.bus import EventBus, get_event_bus
from msm.exceptions import ConflictError, NotFoundError
from msm.logging_conf import get_logger
from msm.runtime.backends import ProcessBackend
from msm.runtime.server_runtime import ServerRuntime, ServerRuntimeConfig

logger = get_logger(__name__)


class Supervisor:
    """Registre des runtimes de serveurs."""

    def __init__(
        self,
        *,
        bus: EventBus | None = None,
        backend: ProcessBackend | None = None,
    ) -> None:
        self._bus = bus or get_event_bus()
        self._backend = backend
        self._runtimes: dict[int, ServerRuntime] = {}

    # ------------------------------------------------------------------ #
    #  Registre
    # ------------------------------------------------------------------ #
    def register(self, config: ServerRuntimeConfig) -> ServerRuntime:
        """Prend en charge un serveur. Son identifiant doit être unique."""
        if config.id in self._runtimes:
            raise ConflictError(
                f"Le serveur « {config.name} » est déjà pris en charge.",
                cause=f"Un runtime existe déjà pour l'identifiant {config.id}.",
                remediation="Recharger la configuration du serveur au lieu de l'enregistrer.",
            )
        runtime = ServerRuntime(config, bus=self._bus, backend=self._backend)
        self._runtimes[config.id] = runtime
        logger.info("server_registered", server_id=config.id, server=config.name)
        return runtime

    def get(self, server_id: int) -> ServerRuntime:
        """Runtime d'un serveur, ou :class:`NotFoundError`."""
        try:
            return self._runtimes[server_id]
        except KeyError:
            raise NotFoundError(
                "Serveur introuvable.",
                cause=f"Aucun serveur ne porte l'identifiant {server_id}.",
                remediation="Rafraîchir la liste des serveurs.",
            ) from None

    def find(self, server_id: int) -> ServerRuntime | None:
        """Variante non levante de :meth:`get`."""
        return self._runtimes.get(server_id)

    def all(self) -> tuple[ServerRuntime, ...]:
        """Tous les runtimes, triés par nom."""
        return tuple(sorted(self._runtimes.values(), key=lambda r: r.name.casefold()))

    def __contains__(self, server_id: object) -> bool:
        return server_id in self._runtimes

    def __len__(self) -> int:
        return len(self._runtimes)

    async def unregister(self, server_id: int, *, stop: bool = False) -> None:
        """Retire un serveur du registre.

        ``stop=False`` par défaut : retirer un serveur de la supervision ne doit
        pas couper une partie en cours sans demande explicite.
        """
        runtime = self._runtimes.pop(server_id, None)
        if runtime is None:
            return
        if stop and runtime.state.is_running:
            with_suppressed_errors = asyncio.shield(runtime.stop(actor="MSM"))
            try:
                await with_suppressed_errors
            except Exception as exc:  # pragma: no cover
                logger.warning("unregister_stop_failed", server_id=server_id, error=str(exc))
        else:
            await runtime.detach()
        logger.info("server_unregistered", server_id=server_id, stopped=stop)

    # ------------------------------------------------------------------ #
    #  Vues d'ensemble
    # ------------------------------------------------------------------ #
    def snapshot_all(self) -> list[dict[str, Any]]:
        """État de tous les serveurs, pour le tableau de bord."""
        return [runtime.snapshot() for runtime in self.all()]

    def summary(self) -> dict[str, Any]:
        """Agrégats globaux affichés en tête du tableau de bord."""
        runtimes = self.all()
        online = [r for r in runtimes if r.state.is_running]
        return {
            "servers_total": len(runtimes),
            "servers_online": len(online),
            "servers_offline": len(runtimes) - len(online),
            "players_online": sum(len(r.online_players) for r in online),
            "cpu_percent": round(sum(r.stats.cpu_percent for r in online), 1),
            "memory_mb": round(sum(r.stats.memory_mb for r in online), 1),
        }

    # ------------------------------------------------------------------ #
    #  Cycle de vie de l'application
    # ------------------------------------------------------------------ #
    async def start_server(self, server_id: int, *, actor: str | None = None) -> None:
        await self.get(server_id).start(actor=actor)

    async def stop_server(self, server_id: int, *, actor: str | None = None) -> Any:
        return await self.get(server_id).stop(actor=actor)

    async def restart_server(self, server_id: int, *, actor: str | None = None) -> None:
        await self.get(server_id).restart(actor=actor)

    async def send_command(self, server_id: int, command: str, *, actor: str | None = None) -> str:
        return await self.get(server_id).send_command(command, actor=actor)

    async def shutdown(self, *, stop_servers: bool = False) -> None:
        """Arrêt de MSM.

        Par défaut, les serveurs Minecraft **continuent de tourner** : le panel
        n'est qu'un outil d'administration, son redémarrage ne doit pas déconnecter
        les joueurs. Ils seront réadoptés au prochain démarrage.
        """
        runtimes = self.all()
        if not runtimes:
            return

        if stop_servers:
            results = await asyncio.gather(
                *(runtime.stop(actor="arrêt de MSM") for runtime in runtimes),
                return_exceptions=True,
            )
            for runtime, result in zip(runtimes, results, strict=True):
                if isinstance(result, Exception):
                    logger.warning("shutdown_stop_failed", server_id=runtime.id, error=str(result))
        else:
            await asyncio.gather(*(runtime.detach() for runtime in runtimes))

        logger.info("supervisor_shutdown", servers=len(runtimes), stopped=stop_servers)
