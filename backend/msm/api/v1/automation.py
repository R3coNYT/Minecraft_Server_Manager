"""Tâches programmées, notifications Discord et installation de versions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from msm.api.deps import (
    AppSettings,
    ClientIp,
    CsrfProtected,
    DbSession,
    GlobalContext,
    ServerAccess,
    SupervisorDep,
)
from msm.api.schemas import (
    DownloadSourceOut,
    InstallOut,
    InstallRequest,
    NotificationEventOut,
    NotificationSettingsOut,
    NotificationSettingsRequest,
    ScheduleCreateRequest,
    ScheduleOut,
    ScheduleUpdateRequest,
    VersionOut,
)
from msm.db.models.schedule import Schedule, ScheduleAction
from msm.exceptions import ValidationError
from msm.schedule.rules import describe, parse_rule
from msm.services.download_service import DownloadService
from msm.services.notifier import LABELS, NotificationEvent, send_to_discord
from msm.services.schedule_service import ScheduleService
from msm.services.settings_service import SettingsService

router = APIRouter(tags=["automatisation"])


def _schedules(
    session: DbSession, supervisor: SupervisorDep, settings: AppSettings
) -> ScheduleService:
    return ScheduleService(session, supervisor, settings=settings)


def _downloads(session: DbSession, supervisor: SupervisorDep) -> DownloadService:
    return DownloadService(session, supervisor)


def _settings_service(session: DbSession) -> SettingsService:
    return SettingsService(session)


SchedulesDep = Annotated[ScheduleService, Depends(_schedules)]
DownloadsDep = Annotated[DownloadService, Depends(_downloads)]
SettingsDep = Annotated[SettingsService, Depends(_settings_service)]


def _to_out(schedule: Schedule) -> ScheduleOut:
    return ScheduleOut(
        id=schedule.id,
        server_id=schedule.server_id,
        name=schedule.name,
        action=schedule.action.value,
        payload=schedule.payload,
        rule=schedule.rule,
        summary=describe(parse_rule(schedule.rule)),
        enabled=schedule.enabled,
        next_run_at=schedule.next_run_at,
        last_run_at=schedule.last_run_at,
        last_status=schedule.last_status.value,
        last_error=schedule.last_error,
    )


def _action(value: str) -> ScheduleAction:
    try:
        return ScheduleAction(value.upper())
    except ValueError as exc:
        raise ValidationError(
            "Action inconnue.",
            cause=f"« {value} » n'est pas une action programmable.",
            remediation=f"Choisir parmi : {', '.join(item.value for item in ScheduleAction)}.",
        ) from exc


# --------------------------------------------------------------------------- #
#  Tâches programmées
# --------------------------------------------------------------------------- #
@router.get(
    "/servers/{server_id}/schedules",
    response_model=list[ScheduleOut],
    summary="Lister les tâches programmées",
)
async def list_schedules(access: ServerAccess, service: SchedulesDep) -> list[ScheduleOut]:
    server, _ = access
    return [_to_out(item) for item in await service.list_schedules(server)]


@router.post(
    "/servers/{server_id}/schedules",
    response_model=ScheduleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Programmer une tâche",
    dependencies=[CsrfProtected],
)
async def create_schedule(
    payload: ScheduleCreateRequest,
    access: ServerAccess,
    service: SchedulesDep,
    ip: ClientIp,
) -> ScheduleOut:
    """La permission de l'action est exigée dès la création.

    Programmer un redémarrage sans avoir le droit de redémarrer produirait une
    tâche qui échoue chaque nuit : autant refuser tout de suite.
    """
    server, context = access
    schedule = await service.create(
        server,
        name=payload.name,
        action=_action(payload.action),
        rule=payload.rule.model_dump(),
        payload=payload.payload,
        enabled=payload.enabled,
        context=context,
        ip_address=ip,
    )
    return _to_out(schedule)


@router.put(
    "/servers/{server_id}/schedules/{schedule_id}",
    response_model=ScheduleOut,
    summary="Modifier une tâche programmée",
    dependencies=[CsrfProtected],
)
async def update_schedule(
    schedule_id: int,
    payload: ScheduleUpdateRequest,
    access: ServerAccess,
    service: SchedulesDep,
    ip: ClientIp,
) -> ScheduleOut:
    server, context = access
    schedule = await service.get_schedule(server, schedule_id)
    updated = await service.update(
        server,
        schedule,
        name=payload.name,
        rule=payload.rule.model_dump() if payload.rule else None,
        payload=payload.payload,
        enabled=payload.enabled,
        context=context,
        ip_address=ip,
    )
    return _to_out(updated)


@router.delete(
    "/servers/{server_id}/schedules/{schedule_id}",
    summary="Supprimer une tâche programmée",
    dependencies=[CsrfProtected],
)
async def delete_schedule(
    schedule_id: int, access: ServerAccess, service: SchedulesDep, ip: ClientIp
) -> dict[str, str]:
    server, context = access
    schedule = await service.get_schedule(server, schedule_id)
    await service.delete(server, schedule, context=context, ip_address=ip)
    return {"status": "deleted"}


@router.post(
    "/servers/{server_id}/schedules/{schedule_id}/run",
    response_model=ScheduleOut,
    summary="Déclencher une tâche maintenant",
    dependencies=[CsrfProtected],
)
async def run_schedule_now(
    schedule_id: int, access: ServerAccess, service: SchedulesDep, ip: ClientIp
) -> ScheduleOut:
    """Exécute la tâche sans décaler sa prochaine occurrence."""
    server, context = access
    schedule = await service.get_schedule(server, schedule_id)
    return _to_out(await service.run_now(server, schedule, context=context, ip_address=ip))


# --------------------------------------------------------------------------- #
#  Notifications
# --------------------------------------------------------------------------- #
@router.get(
    "/notifications/events",
    response_model=list[NotificationEventOut],
    summary="Événements notifiables",
)
async def notification_events(_: GlobalContext) -> list[NotificationEventOut]:
    return [
        NotificationEventOut(key=event.value, label=LABELS[event]) for event in NotificationEvent
    ]


@router.get(
    "/notifications",
    response_model=NotificationSettingsOut,
    summary="Réglages des notifications",
)
async def get_notifications(
    context: GlobalContext, service: SettingsDep
) -> NotificationSettingsOut:
    return NotificationSettingsOut.model_validate(await service.notifications(context=context))


@router.put(
    "/notifications",
    response_model=NotificationSettingsOut,
    summary="Modifier les notifications",
    dependencies=[CsrfProtected],
)
async def update_notifications(
    payload: NotificationSettingsRequest,
    context: GlobalContext,
    service: SettingsDep,
    ip: ClientIp,
) -> NotificationSettingsOut:
    """L'adresse du webhook est chiffrée en base et n'est jamais renvoyée."""
    result = await service.update_notifications(
        enabled=payload.enabled,
        events=payload.events,
        webhook_url=payload.webhook_url,
        clear_webhook=payload.clear_webhook,
        context=context,
        ip_address=ip,
    )
    return NotificationSettingsOut.model_validate(result)


@router.post(
    "/notifications/test",
    summary="Envoyer un message de test",
    dependencies=[CsrfProtected],
)
async def test_notification(context: GlobalContext, service: SettingsDep) -> dict[str, bool]:
    """Vérifie la configuration en publiant réellement dans le salon."""
    url = await service.webhook_url(context=context)
    content = (
        "✅ **Minecraft Server Manager** · message de test — les notifications arriveront ici."
    )
    return {"sent": await send_to_discord(url, content)}


# --------------------------------------------------------------------------- #
#  Téléchargements
# --------------------------------------------------------------------------- #
@router.get(
    "/downloads/sources",
    response_model=list[DownloadSourceOut],
    summary="Sources de téléchargement",
)
async def download_sources(_: GlobalContext) -> list[DownloadSourceOut]:
    """Sources officielles reconnues. Aucune adresse arbitraire n'est acceptée."""
    return [DownloadSourceOut(**item) for item in DownloadService.sources()]


@router.get(
    "/downloads/{source}/versions",
    response_model=list[VersionOut],
    summary="Versions disponibles",
)
async def download_versions(
    source: str, context: GlobalContext, service: DownloadsDep
) -> list[VersionOut]:
    return [VersionOut(**item) for item in await service.versions(source, context=context)]


@router.post(
    "/servers/{server_id}/install",
    response_model=InstallOut,
    summary="Installer une version",
    dependencies=[CsrfProtected],
)
async def install_version(
    payload: InstallRequest,
    access: ServerAccess,
    service: DownloadsDep,
    ip: ClientIp,
) -> InstallOut:
    """Télécharge le JAR, vérifie son empreinte et le sélectionne."""
    server, context = access
    result = await service.install(
        server,
        source=payload.source,
        version=payload.version,
        context=context,
        ip_address=ip,
    )
    return InstallOut(**result)
