"""Création de serveurs de zéro : listes de choix, lancement, suivi."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from msm.api.deps import (
    AppSettings,
    ClientIp,
    CsrfProtected,
    CurrentUser,
    DbSession,
    GlobalContext,
    SupervisorDep,
)
from msm.api.schemas import VersionOut
from msm.api.schemas.provisioning import (
    BuildOut,
    DistributionOut,
    ProvisioningDefaultsOut,
    ProvisioningJobOut,
    ProvisioningRequestIn,
)
from msm.core.permissions import Permission
from msm.downloads.sources import SOURCES, list_builds, list_versions
from msm.exceptions import ValidationError
from msm.i18n import tr
from msm.provisioning.jobs import JobRegistry
from msm.services.provisioning_service import ProvisioningRequest, ProvisioningService

router = APIRouter(prefix="/provisioning", tags=["provisioning"])


def _service(
    request: Request, session: DbSession, settings: AppSettings, supervisor: SupervisorDep
) -> ProvisioningService:
    registry: JobRegistry = request.app.state.provisioning_jobs
    return ProvisioningService(session, settings, supervisor, registry)


ProvisioningDep = Annotated[ProvisioningService, Depends(_service)]


def _require_known(key: str) -> None:
    if key not in SOURCES:
        raise ValidationError(
            tr("Unknown server type."),
            cause=tr("“{key}” is not a type MSM can install.", key=key),
            remediation=tr("Choose one of: {choices}.", choices=", ".join(SOURCES)),
        )


@router.get(
    "/distributions",
    response_model=list[DistributionOut],
    summary="Server types that can be created",
)
async def distributions(context: GlobalContext) -> list[DistributionOut]:
    context.require(Permission.SERVER_CREATE, action=tr("create a server"))
    return [
        DistributionOut(
            key=key,
            label=source["label"],
            server_type=source["server_type"].value,
            has_builds=source["builds"] is not None,
            kind=source["kind"],
        )
        for key, source in SOURCES.items()
    ]


@router.get(
    "/distributions/{key}/versions",
    response_model=list[VersionOut],
    summary="Minecraft versions of a server type",
)
async def versions(key: str, context: GlobalContext) -> list[VersionOut]:
    context.require(Permission.SERVER_CREATE, action=tr("create a server"))
    _require_known(key)
    return [VersionOut(**item.to_dict()) for item in await list_versions(key)]


@router.get(
    "/distributions/{key}/versions/{version}/builds",
    response_model=list[BuildOut],
    summary="Builds of a Minecraft version",
)
async def builds(key: str, version: str, context: GlobalContext) -> list[BuildOut]:
    context.require(Permission.SERVER_CREATE, action=tr("create a server"))
    _require_known(key)
    return [BuildOut(**item.to_dict()) for item in await list_builds(key, version)]


@router.get(
    "/defaults",
    response_model=ProvisioningDefaultsOut,
    summary="Suggested folder and port",
)
async def defaults(
    context: GlobalContext,
    service: ProvisioningDep,
    name: Annotated[str, Query(max_length=128)] = "",
) -> ProvisioningDefaultsOut:
    return ProvisioningDefaultsOut(**await service.defaults(name, context=context))


@router.post(
    "",
    response_model=ProvisioningJobOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a server from scratch",
    dependencies=[CsrfProtected],
)
async def create(
    payload: ProvisioningRequestIn,
    context: GlobalContext,
    user: CurrentUser,
    service: ProvisioningDep,
    ip: ClientIp,
) -> ProvisioningJobOut:
    """Valide la demande, puis crée le serveur en tâche de fond.

    La progression se suit sur `GET /provisioning/{id}`.
    """
    job = await service.start(
        ProvisioningRequest(**payload.model_dump()),
        context=context,
        actor=user,
        ip_address=ip,
    )
    return ProvisioningJobOut.model_validate(job.to_dict())


@router.get("/{job_id}", response_model=ProvisioningJobOut, summary="Creation progress")
async def progress(
    job_id: str, context: GlobalContext, service: ProvisioningDep
) -> ProvisioningJobOut:
    return ProvisioningJobOut.model_validate(service.get(job_id, context=context).to_dict())
