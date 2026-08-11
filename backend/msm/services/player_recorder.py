"""Enregistrement en base des arrivées et départs de joueurs.

Le runtime ne connaît pas la base de données — c'est ce qui le rend testable
avec un simple faux serveur. Il publie donc ses événements sur le bus, et ce
consommateur les transforme en écritures.

Une écriture qui échoue ne doit jamais interrompre le suivi : une base
momentanément verrouillée peut faire perdre une ligne d'historique, pas le flux
temps réel des joueurs connectés.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from msm.bus import EventBus, topics
from msm.db.repositories.player_repo import PlayerRepository
from msm.db.session import session_scope
from msm.logging_conf import get_logger

logger = get_logger(__name__)


class PlayerRecorder:
    """Consommateur du bus qui persiste l'historique des joueurs."""

    #: Sujet interne servant uniquement à réveiller la boucle à l'arrêt.
    _WAKE_TOPIC = "server.0.__shutdown__"

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="msm-player-recorder")

    async def stop(self, *, timeout: float = 5.0) -> None:
        """Termine la boucle en laissant l'écriture en cours s'achever.

        Une annulation brutale interromprait une transaction au milieu d'un
        aller-retour SQLite, et la connexion tenterait ensuite de notifier une
        boucle d'événements déjà fermée.
        """
        if self._task is None:
            return

        self._stopping = True
        # La boucle est bloquée sur la file : il faut la réveiller pour qu'elle
        # constate la demande d'arrêt.
        self._bus.publish(self._WAKE_TOPIC, None)

        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError):  # pragma: no cover - écriture bloquée
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        finally:
            self._task = None

    async def _run(self) -> None:
        # Un seul abonnement générique : les serveurs vont et viennent, s'abonner
        # par serveur imposerait de suivre leur cycle de vie ici aussi.
        subscription = self._bus.subscribe("server.")
        try:
            async for event in subscription:
                if self._stopping:
                    break
                suffix = event.topic.rsplit(".", 1)[-1]
                if suffix in (topics.PLAYER_JOIN, topics.PLAYER_LEAVE):
                    await self._record(suffix, event.payload)
        except asyncio.CancelledError:
            raise
        finally:
            subscription.close()

    async def _record(self, kind: str, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        server_id = payload.get("server_id")
        username = payload.get("username")
        if not isinstance(server_id, int) or not isinstance(username, str):
            return

        try:
            async with session_scope() as session:
                repository = PlayerRepository(session)
                if kind == topics.PLAYER_JOIN:
                    await repository.record_join(server_id, username, payload.get("uuid"))
                else:
                    await repository.record_leave(server_id, username)
        except Exception as exc:
            logger.warning(
                "player_record_failed",
                server_id=server_id,
                username=username,
                kind=kind,
                error=str(exc),
            )
