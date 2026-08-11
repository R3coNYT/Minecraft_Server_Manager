"""Écriture et consultation du journal d'audit."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from msm.db.models.audit import AuditAction, AuditLog, AuditResult


class AuditRepository:
    """Journal en ajout seul : aucune méthode de modification ni de suppression."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def record(
        self,
        *,
        action: AuditAction,
        summary: str,
        actor_id: int | None = None,
        actor_username: str = "système",
        actor_role: str | None = None,
        ip_address: str | None = None,
        server_id: int | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        result: AuditResult = AuditResult.SUCCESS,
        payload: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Ajoute une entrée à la session courante.

        L'écriture participe à la transaction de l'action auditée : si celle-ci
        est annulée, l'entrée l'est aussi. Un journal qui consignerait des actions
        n'ayant pas eu lieu serait pire qu'inutile.
        """
        entry = AuditLog(
            ts=datetime.now(UTC),
            actor_id=actor_id,
            actor_username=actor_username,
            actor_role=actor_role,
            ip_address=ip_address,
            action=action,
            result=result,
            server_id=server_id,
            target_type=target_type,
            target_id=target_id,
            summary=summary,
            payload=payload,
        )
        self._session.add(entry)
        return entry

    async def search(
        self,
        *,
        server_id: int | None = None,
        actor_id: int | None = None,
        action: AuditAction | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[AuditLog], int]:
        """Recherche paginée, du plus récent au plus ancien."""
        filters = []
        if server_id is not None:
            filters.append(AuditLog.server_id == server_id)
        if actor_id is not None:
            filters.append(AuditLog.actor_id == actor_id)
        if action is not None:
            filters.append(AuditLog.action == action)
        if since is not None:
            filters.append(AuditLog.ts >= since)
        if until is not None:
            filters.append(AuditLog.ts <= until)

        total_statement = select(func.count()).select_from(AuditLog)
        page_statement = select(AuditLog).order_by(AuditLog.ts.desc(), AuditLog.id.desc())
        if filters:
            total_statement = total_statement.where(*filters)
            page_statement = page_statement.where(*filters)

        total = (await self._session.execute(total_statement)).scalar_one()
        rows = list(
            (await self._session.execute(page_statement.limit(limit).offset(offset))).scalars()
        )
        return rows, int(total)
