"""Persistance de l'état des processus, pour la réadoption au redémarrage.

Le runtime change d'état de lui-même — démarrage terminé, plantage, redémarrage
automatique — et pas seulement sur action de l'utilisateur. Ne persister que les
actions explicites laisserait en base un état faux dès le premier plantage
nocturne.

Ce consommateur écoute donc les changements d'état sur le bus. Le runtime,
lui, continue d'ignorer l'existence de la base de données.

Ce qui est enregistré n'est pas décoratif : le couple **PID + date de création**
est ce qui permettra, au prochain démarrage de MSM, de distinguer « mon serveur
tourne toujours » de « ce PID appartient désormais à un autre programme ».
"""

from __future__ import annotations

import asyncio
import contextlib

from msm.bus import EventBus, topics
from msm.db.repositories import ServerRepository
from msm.db.session import session_scope
from msm.logging_conf import get_logger
from msm.runtime.supervisor import Supervisor

logger = get_logger(__name__)


class RuntimeStateRecorder:
    """Consommateur du bus qui reflète l'état des processus en base."""

    _WAKE_TOPIC = "server.0.__shutdown__"

    def __init__(self, bus: EventBus, supervisor: Supervisor) -> None:
        self._bus = bus
        self._supervisor = supervisor
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="msm-runtime-recorder")

    async def stop(self, *, timeout: float = 5.0) -> None:
        """Termine la boucle en laissant l'écriture en cours s'achever."""
        if self._task is None:
            return

        self._stopping = True
        self._bus.publish(self._WAKE_TOPIC, None)
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError):  # pragma: no cover
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        finally:
            self._task = None

    async def _run(self) -> None:
        subscription = self._bus.subscribe("server.")
        try:
            async for event in subscription:
                if self._stopping:
                    break
                if event.topic.rsplit(".", 1)[-1] == topics.STATUS:
                    await self._persist(event.payload)
        except asyncio.CancelledError:
            raise
        finally:
            subscription.close()

    async def _persist(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        server_id = payload.get("id")
        if not isinstance(server_id, int):
            return

        # Les identifiants du processus sont lus sur le runtime plutôt que dans
        # l'événement : ce sont des détails d'exploitation, ils n'ont pas à
        # transiter par l'API ni par le WebSocket.
        runtime = self._supervisor.find(server_id)
        if runtime is None:
            return

        try:
            async with session_scope() as session:
                await ServerRepository(session).save_runtime_state(
                    server_id,
                    state=runtime.state,
                    pid=runtime.pid,
                    group_id=runtime.group_id,
                    process_create_time=runtime.process_create_time,
                    consecutive_crashes=payload.get("consecutive_crashes", 0),
                    last_error=payload.get("last_error"),
                )
        except Exception as exc:
            logger.warning("runtime_state_persist_failed", server_id=server_id, error=str(exc))
