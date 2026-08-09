"""Abstraction « agent » — préparation de la gestion multi-machines.

Aujourd'hui, MSM pilote des serveurs qui tournent sur la même machine que lui.
Demain, il doit pouvoir en piloter sur d'autres hôtes (spécification § 41).

Plutôt que de réécrire les services à ce moment-là, ils dialoguent dès maintenant
avec un :class:`Agent`. L'implémentation locale délègue au superviseur ; une future
implémentation distante parlera à un démon MSM sur une autre machine. Aucun service
métier n'aura à changer.

C'est le seul endroit du code où cette extension est anticipée — ailleurs, on écrit
ce dont on a besoin aujourd'hui.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from msm.core.log_line import LogLine
from msm.runtime.server_runtime import ServerRuntimeConfig
from msm.runtime.supervisor import Supervisor


class Agent(ABC):
    """Exécutant capable de piloter des serveurs Minecraft."""

    #: Identifiant de la machine ; ``local`` pour l'hôte de MSM.
    name: str

    @abstractmethod
    async def register(self, config: ServerRuntimeConfig) -> None: ...

    @abstractmethod
    async def start(self, server_id: int, *, actor: str | None = None) -> None: ...

    @abstractmethod
    async def stop(self, server_id: int, *, actor: str | None = None) -> Any: ...

    @abstractmethod
    async def restart(self, server_id: int, *, actor: str | None = None) -> None: ...

    @abstractmethod
    async def send_command(
        self, server_id: int, command: str, *, actor: str | None = None
    ) -> str: ...

    @abstractmethod
    async def status(self, server_id: int) -> dict[str, Any]: ...

    @abstractmethod
    async def logs_tail(self, server_id: int, limit: int = 500) -> list[LogLine]: ...


class LocalAgent(Agent):
    """Agent pilotant les serveurs de la machine hôte, via le superviseur."""

    name = "local"

    def __init__(self, supervisor: Supervisor) -> None:
        self._supervisor = supervisor

    @property
    def supervisor(self) -> Supervisor:
        return self._supervisor

    async def register(self, config: ServerRuntimeConfig) -> None:
        self._supervisor.register(config)

    async def start(self, server_id: int, *, actor: str | None = None) -> None:
        await self._supervisor.start_server(server_id, actor=actor)

    async def stop(self, server_id: int, *, actor: str | None = None) -> Any:
        return await self._supervisor.stop_server(server_id, actor=actor)

    async def restart(self, server_id: int, *, actor: str | None = None) -> None:
        await self._supervisor.restart_server(server_id, actor=actor)

    async def send_command(self, server_id: int, command: str, *, actor: str | None = None) -> str:
        return await self._supervisor.send_command(server_id, command, actor=actor)

    async def status(self, server_id: int) -> dict[str, Any]:
        return self._supervisor.get(server_id).snapshot()

    async def logs_tail(self, server_id: int, limit: int = 500) -> list[LogLine]:
        return self._supervisor.get(server_id).logs_tail(limit)
