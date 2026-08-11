"""Sauvegardes d'un serveur et historique de ses ressources."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse

from msm.api.deps import (
    AppSettings,
    ClientIp,
    CsrfProtected,
    DbSession,
    ServerAccess,
    SupervisorDep,
)
from msm.api.schemas import (
    BackupManifestOut,
    BackupOut,
    MetricsOut,
    RestoreRequest,
)
from msm.core.permissions import Permission
from msm.db.models.misc import Backup, BackupStatus
from msm.services.backup_service import BackupService
from msm.services.metrics_service import RANGES, MetricsService

router = APIRouter(tags=["sauvegardes"])


def _backups(session: DbSession, supervisor: SupervisorDep, settings: AppSettings) -> BackupService:
    # Les réglages viennent de l'application, jamais du cache global : sans cela,
    # une instance configurée pour écrire ailleurs — un test, par exemple —
    # déposerait ses archives dans le dossier de données par défaut.
    return BackupService(session, supervisor, settings=settings)


def _metrics(session: DbSession) -> MetricsService:
    return MetricsService(session)


BackupsDep = Annotated[BackupService, Depends(_backups)]
MetricsDep = Annotated[MetricsService, Depends(_metrics)]


def _to_out(backup: Backup) -> BackupOut:
    return BackupOut(
        id=backup.id,
        server_id=backup.server_id,
        kind=backup.kind,
        status=backup.status.value,
        size_bytes=backup.size_bytes,
        created_at=backup.created_at,
        created_by=backup.created_by,
        error=backup.error,
        available=backup.status is BackupStatus.COMPLETED,
    )


# --------------------------------------------------------------------------- #
#  Sauvegardes
# --------------------------------------------------------------------------- #
@router.get(
    "/servers/{server_id}/backups",
    response_model=list[BackupOut],
    summary="Lister les sauvegardes",
)
async def list_backups(access: ServerAccess, service: BackupsDep) -> list[BackupOut]:
    server, _ = access
    return [_to_out(backup) for backup in await service.list_backups(server)]


@router.post(
    "/servers/{server_id}/backups",
    response_model=BackupOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Lancer une sauvegarde",
    dependencies=[CsrfProtected],
)
async def create_backup(access: ServerAccess, service: BackupsDep, ip: ClientIp) -> BackupOut:
    """Démarre la sauvegarde et répond aussitôt.

    L'écriture d'une archive peut durer plusieurs minutes : sa progression est
    poussée par WebSocket, la requête ne l'attend pas.
    """
    server, context = access
    return _to_out(await service.start_backup(server, context=context, ip_address=ip))


@router.get(
    "/servers/{server_id}/backups/{backup_id}/manifest",
    response_model=BackupManifestOut,
    summary="Contenu déclaré d'une sauvegarde",
)
async def backup_manifest(
    backup_id: int, access: ServerAccess, service: BackupsDep
) -> BackupManifestOut:
    """Mondes sauvegardés, mods et plugins inventoriés — sans rien extraire."""
    server, _ = access
    return BackupManifestOut.model_validate(await service.describe(server, backup_id))


@router.get(
    "/servers/{server_id}/backups/{backup_id}/download",
    summary="Télécharger une archive",
    response_class=FileResponse,
)
async def download_backup(
    backup_id: int, access: ServerAccess, service: BackupsDep, ip: ClientIp
) -> FileResponse:
    """Sort l'archive de la machine — d'où la permission et la trace d'audit."""
    server, context = access
    context.require(Permission.BACKUP_CREATE, action="télécharger une sauvegarde")

    backup = await service.get_backup(server, backup_id)
    path = service.archive_path(backup)
    await service.record_download(server, backup, context=context, ip_address=ip)
    return FileResponse(path, filename=path.name, media_type="application/gzip")


@router.post(
    "/servers/{server_id}/backups/{backup_id}/restore",
    response_model=BackupOut,
    summary="Restaurer une sauvegarde",
    dependencies=[CsrfProtected],
)
async def restore_backup(
    backup_id: int,
    payload: RestoreRequest,
    access: ServerAccess,
    service: BackupsDep,
    ip: ClientIp,
) -> BackupOut:
    """Remplace mondes et configurations. Le serveur doit être arrêté."""
    server, context = access
    backup = await service.restore(
        server, backup_id, context=context, confirm=payload.confirm, ip_address=ip
    )
    return _to_out(backup)


@router.post(
    "/servers/{server_id}/backups/{backup_id}/cancel",
    summary="Annuler une sauvegarde en cours",
    dependencies=[CsrfProtected],
)
async def cancel_backup(
    backup_id: int, access: ServerAccess, service: BackupsDep
) -> dict[str, bool]:
    server, context = access
    return {"cancelled": await service.cancel_backup(server, backup_id, context=context)}


@router.delete(
    "/servers/{server_id}/backups/{backup_id}",
    summary="Supprimer une sauvegarde",
    dependencies=[CsrfProtected],
)
async def delete_backup(
    backup_id: int, access: ServerAccess, service: BackupsDep, ip: ClientIp
) -> dict[str, str]:
    server, context = access
    await service.delete(server, backup_id, context=context, ip_address=ip)
    return {"status": "deleted"}


# --------------------------------------------------------------------------- #
#  Métriques
# --------------------------------------------------------------------------- #
@router.get(
    "/servers/{server_id}/metrics",
    response_model=MetricsOut,
    summary="Historique des ressources",
)
async def server_metrics(
    access: ServerAccess,
    service: MetricsDep,
    range_key: Annotated[str, Query(alias="range", description="1h, 6h, 24h ou 7d")] = "24h",
) -> MetricsOut:
    """Points agrégés sur la période demandée, avec les pointes observées."""
    server, _ = access
    return MetricsOut.model_validate(await service.history(server, range_key=range_key))


@router.get("/metrics/ranges", summary="Périodes disponibles")
async def metric_ranges() -> list[str]:
    """Valeurs acceptées par le paramètre `range`."""
    return list(RANGES)
