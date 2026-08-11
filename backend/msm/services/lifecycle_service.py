"""Cycle de vie des serveurs : démarrer, arrêter, redémarrer, terminer.

Chaque action est journalisée **avant** d'être exécutée pour ce qui relève de
l'intention, et complétée par son résultat. Un démarrage qui échoue laisse ainsi
une trace : « untel a tenté de démarrer ce serveur, voici pourquoi ça n'a pas
fonctionné ».
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from msm.core.permissions import Permission
from msm.db.models.audit import AuditAction, AuditResult
from msm.db.models.server import Server
from msm.db.repositories import AuditRepository, ServerRepository
from msm.logging_conf import get_logger
from msm.runtime.server_runtime import ServerRuntime
from msm.runtime.supervisor import Supervisor
from msm.security.rbac import AccessContext

logger = get_logger(__name__)


class LifecycleService:
    """Actions de cycle de vie, avec contrôle des droits et audit."""

    def __init__(self, session: AsyncSession, supervisor: Supervisor) -> None:
        self._supervisor = supervisor
        self._audit = AuditRepository(session)
        self._servers = ServerRepository(session)

    async def start(
        self, server: Server, *, context: AccessContext, ip_address: str | None = None
    ) -> dict[str, Any]:
        context.require(Permission.SERVER_START, action="démarrer ce serveur")
        runtime = self._supervisor.get(server.id)

        try:
            await runtime.start(actor=context.username)
        except Exception as exc:
            self._record(
                AuditAction.SERVER_STARTED,
                f"Échec du démarrage de « {server.name} » : {getattr(exc, 'cause', exc)}",
                server,
                context,
                ip_address,
                result=AuditResult.ERROR,
            )
            raise

        self._record(
            AuditAction.SERVER_STARTED,
            f"Démarrage du serveur « {server.name} ».",
            server,
            context,
            ip_address,
        )
        await self._persist(server, runtime)
        return runtime.snapshot()

    async def stop(
        self, server: Server, *, context: AccessContext, ip_address: str | None = None
    ) -> dict[str, Any]:
        context.require(Permission.SERVER_STOP, action="arrêter ce serveur")
        runtime = self._supervisor.get(server.id)

        outcome = await runtime.stop(actor=context.username)

        detail = "arrêt propre" if not outcome.forced else f"arrêt forcé ({outcome.stage.value})"
        self._record(
            AuditAction.SERVER_STOPPED,
            f"Arrêt du serveur « {server.name} » — {detail}.",
            server,
            context,
            ip_address,
            payload={
                "stage": outcome.stage.value,
                "forced": outcome.forced,
                "exit_code": outcome.exit_code,
                "duration_s": round(outcome.duration_s, 1),
            },
        )
        await self._persist(server, runtime)
        return {
            "stage": outcome.stage.value,
            "forced": outcome.forced,
            "exit_code": outcome.exit_code,
            "duration_s": round(outcome.duration_s, 1),
            "status": runtime.snapshot(),
        }

    async def restart(
        self, server: Server, *, context: AccessContext, ip_address: str | None = None
    ) -> dict[str, Any]:
        context.require(Permission.SERVER_RESTART, action="redémarrer ce serveur")
        runtime = self._supervisor.get(server.id)

        await runtime.restart(actor=context.username)

        self._record(
            AuditAction.SERVER_RESTARTED,
            f"Redémarrage du serveur « {server.name} ».",
            server,
            context,
            ip_address,
        )
        await self._persist(server, runtime)
        return runtime.snapshot()

    async def kill(
        self, server: Server, *, context: AccessContext, ip_address: str | None = None
    ) -> dict[str, Any]:
        """Terminaison immédiate : le monde n'est pas sauvegardé."""
        context.require(Permission.SERVER_KILL, action="forcer l'arrêt de ce serveur")
        runtime = self._supervisor.get(server.id)

        await runtime.kill(actor=context.username)

        self._record(
            AuditAction.SERVER_KILLED,
            f"Arrêt FORCÉ du serveur « {server.name} » — le monde n'a pas été sauvegardé.",
            server,
            context,
            ip_address,
        )
        logger.warning("server_killed", server_id=server.id, actor=context.username)
        await self._persist(server, runtime)
        return runtime.snapshot()

    # ------------------------------------------------------------------ #
    async def _persist(self, server: Server, runtime: ServerRuntime) -> None:
        """Mémorise l'état du processus pour la réadoption au prochain démarrage."""
        snapshot = runtime.snapshot()
        await self._servers.save_runtime_state(
            server.id,
            state=runtime.state,
            pid=runtime.pid,
            group_id=runtime.group_id,
            process_create_time=runtime.process_create_time,
            consecutive_crashes=snapshot.get("consecutive_crashes", 0),
            last_error=snapshot.get("last_error"),
        )

    def _record(
        self,
        action: AuditAction,
        summary: str,
        server: Server,
        context: AccessContext,
        ip_address: str | None,
        *,
        result: AuditResult = AuditResult.SUCCESS,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._audit.record(
            action=action,
            summary=summary,
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            server_id=server.id,
            result=result,
            payload=payload,
        )
