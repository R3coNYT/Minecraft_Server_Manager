"""Tâches programmées, langue de l'interface et installation de versions."""

from __future__ import annotations

from dataclasses import asdict
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
    LanguageOut,
    LanguageRequest,
    RegistrationSettings,
    ScheduleCreateRequest,
    ScheduleOut,
    ScheduleUpdateRequest,
    VersionOut,
)
from msm.api.schemas.hosting import HostingSettingsModel, QuotaModel
from msm.core.permissions import Permission
from msm.db.models.schedule import Schedule, ScheduleAction
from msm.exceptions import ValidationError
from msm.i18n import SUPPORTED_LANGUAGES, tr
from msm.schedule.rules import describe, parse_rule
from msm.services.account_service import AccountService, RegistrationMode
from msm.services.download_service import DownloadService
from msm.services.hosting_service import HostingService, HostingSettings, Quota
from msm.services.schedule_service import ScheduleService
from msm.services.settings_service import SettingsService

router = APIRouter(tags=["automation"])


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
            tr("Unknown action."),
            cause=tr("“{value}” is not a schedulable action.", value=value),
            remediation=tr(
                "Choose one of: {choices}.",
                choices=", ".join(item.value for item in ScheduleAction),
            ),
        ) from exc


# --------------------------------------------------------------------------- #
#  Tâches programmées
# --------------------------------------------------------------------------- #
@router.get(
    "/servers/{server_id}/schedules",
    response_model=list[ScheduleOut],
    summary="List scheduled tasks",
)
async def list_schedules(access: ServerAccess, service: SchedulesDep) -> list[ScheduleOut]:
    server, context = access
    context.require(Permission.SERVER_EDIT, action=tr("view scheduled tasks"))
    return [_to_out(item) for item in await service.list_schedules(server)]


@router.post(
    "/servers/{server_id}/schedules",
    response_model=ScheduleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a task",
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
    summary="Update a scheduled task",
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
    summary="Delete a scheduled task",
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
    summary="Run a task now",
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
#  Langue
# --------------------------------------------------------------------------- #
@router.put(
    "/settings/language",
    response_model=LanguageOut,
    summary="Change the interface language",
    dependencies=[CsrfProtected],
)
async def update_language(
    payload: LanguageRequest, context: GlobalContext, service: SettingsDep, ip: ClientIp
) -> LanguageOut:
    """Réglage global : il s'applique à tous les comptes et aux textes produits par MSM."""
    language = await service.update_language(payload.language, context=context, ip_address=ip)
    return LanguageOut(language=language, languages=list(SUPPORTED_LANGUAGES))


# --------------------------------------------------------------------------- #
#  Inscriptions
# --------------------------------------------------------------------------- #
@router.get(
    "/settings/registration", response_model=RegistrationSettings, summary="Registration mode"
)
async def registration_settings(
    settings: AppSettings, session: DbSession, context: GlobalContext
) -> RegistrationSettings:
    context.require(Permission.SETTINGS_MANAGE, action=tr("view the settings"))
    mode = await AccountService(session, settings).registration_mode()
    return RegistrationSettings(mode=mode.value)


@router.put(
    "/settings/registration",
    response_model=RegistrationSettings,
    summary="Change the registration mode",
    dependencies=[CsrfProtected],
)
async def update_registration(
    settings: AppSettings,
    payload: RegistrationSettings,
    session: DbSession,
    context: GlobalContext,
    ip: ClientIp,
) -> RegistrationSettings:
    """Fermée, sur invitation, ou ouverte à tous."""
    try:
        mode = RegistrationMode(payload.mode)
    except ValueError as exc:
        raise ValidationError(
            tr("Unknown registration mode."),
            cause=tr("“{mode}” is not a registration mode.", mode=payload.mode),
            remediation=tr("Choose one of: {choices}.", choices="closed, invite, open"),
        ) from exc
    await AccountService(session, settings).set_registration_mode(
        mode, context=context, ip_address=ip
    )
    return RegistrationSettings(mode=mode.value)


# --------------------------------------------------------------------------- #
#  Hébergement des comptes
# --------------------------------------------------------------------------- #
def _hosting_out(service: HostingService, hosting: HostingSettings) -> HostingSettingsModel:
    return HostingSettingsModel(
        users_root=hosting.users_root,
        port_min=hosting.port_min,
        port_max=hosting.port_max,
        quota=QuotaModel(**asdict(hosting.quota)),
        users_root_effective=str(service.users_root(hosting)),
    )


@router.get("/settings/hosting", response_model=HostingSettingsModel, summary="Hosting settings")
async def hosting_settings(
    session: DbSession, settings: AppSettings, context: GlobalContext
) -> HostingSettingsModel:
    context.require(Permission.SETTINGS_MANAGE, action=tr("view the settings"))
    service = HostingService(session, settings)
    return _hosting_out(service, await service.load())


@router.put(
    "/settings/hosting",
    response_model=HostingSettingsModel,
    summary="Change the hosting settings",
    dependencies=[CsrfProtected],
)
async def update_hosting(
    payload: HostingSettingsModel,
    session: DbSession,
    settings: AppSettings,
    context: GlobalContext,
    ip: ClientIp,
) -> HostingSettingsModel:
    """Dossier des comptes, plage de ports, quotas par défaut."""
    service = HostingService(session, settings)
    hosting = HostingSettings(
        users_root=payload.users_root or None,
        port_min=payload.port_min,
        port_max=payload.port_max,
        quota=Quota(**payload.quota.model_dump()),
    )
    await service.save(hosting, context=context, ip_address=ip)
    return _hosting_out(service, hosting)


# --------------------------------------------------------------------------- #
#  Téléchargements
# --------------------------------------------------------------------------- #
@router.get(
    "/downloads/sources",
    response_model=list[DownloadSourceOut],
    summary="Download sources",
)
async def download_sources(_: GlobalContext) -> list[DownloadSourceOut]:
    """Sources officielles reconnues. Aucune adresse arbitraire n'est acceptée."""
    return [DownloadSourceOut(**item) for item in DownloadService.sources()]


@router.get(
    "/downloads/{source}/versions",
    response_model=list[VersionOut],
    summary="Available versions",
)
async def download_versions(
    source: str, context: GlobalContext, service: DownloadsDep
) -> list[VersionOut]:
    return [VersionOut(**item) for item in await service.versions(source, context=context)]


@router.post(
    "/servers/{server_id}/install",
    response_model=InstallOut,
    summary="Install a version",
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
