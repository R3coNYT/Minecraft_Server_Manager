"""Consultation du journal d'audit."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from msm.api.deps import DbSession, require_permission
from msm.api.schemas import AuditEntryOut, AuditPageOut
from msm.core.permissions import Permission
from msm.db.models.audit import AuditAction
from msm.db.repositories import AuditRepository
from msm.security.rbac import AccessContext

router = APIRouter(prefix="/audit", tags=["audit"])

AuditViewer = Annotated[AccessContext, Depends(require_permission(Permission.AUDIT_VIEW))]


@router.get("", response_model=AuditPageOut, summary="Consulter le journal d'audit")
async def search_audit(
    session: DbSession,
    _: AuditViewer,
    server_id: int | None = None,
    actor_id: int | None = None,
    action: AuditAction | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPageOut:
    """Recherche paginée dans le journal, du plus récent au plus ancien."""
    entries, total = await AuditRepository(session).search(
        server_id=server_id,
        actor_id=actor_id,
        action=action,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return AuditPageOut(
        entries=[
            AuditEntryOut.model_validate(
                {
                    **{
                        field: getattr(entry, field)
                        for field in (
                            "id",
                            "ts",
                            "actor_username",
                            "actor_role",
                            "ip_address",
                            "server_id",
                            "target_type",
                            "target_id",
                            "summary",
                            "payload",
                        )
                    },
                    "action": entry.action.value,
                    "result": entry.result.value,
                }
            )
            for entry in entries
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/actions", summary="Actions journalisables")
async def list_actions(_: AuditViewer) -> list[str]:
    """Valeurs possibles du filtre « action », pour alimenter l'interface."""
    return sorted(action.value for action in AuditAction)
