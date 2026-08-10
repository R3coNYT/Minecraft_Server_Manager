"""Mods, plugins et fichiers de configuration.

Le chemin d'un fichier de configuration voyage en **paramètre de requête** et non
en segment d'URL : un chemin comporte des `/`, qui découperaient l'URL en autant
de segments et rendraient impossible la distinction entre `config/mod.toml` et un
sous-dossier `config` suivi d'une route `mod.toml`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile, status

from msm.api.deps import (
    AppSettings,
    ClientIp,
    CsrfProtected,
    DbSession,
    ServerAccess,
    SupervisorDep,
)
from msm.api.schemas import (
    ConfigEntryOut,
    ConfigFileOut,
    ConfigWriteOut,
    ConfigWriteRequest,
    ManagedFileOut,
    PropertiesOut,
    PropertiesUpdateOut,
    PropertiesUpdateRequest,
    PropertyOut,
    ToggleRequest,
)
from msm.core.permissions import Permission
from msm.db.models.audit import AuditAction
from msm.db.repositories import AuditRepository
from msm.minecraft import properties as properties_module
from msm.services.config_service import ConfigService
from msm.services.file_service import FileService

router = APIRouter(prefix="/servers/{server_id}", tags=["fichiers"])

#: Le nom d'un fichier géré ne contient jamais de séparateur de chemin.
FileNameParam = Annotated[str, Path(max_length=160, pattern=r"^[^/\\]+$")]


def _files(session: DbSession, settings: AppSettings) -> FileService:
    return FileService(session, settings)


def _configs(session: DbSession) -> ConfigService:
    return ConfigService(session)


FilesDep = Annotated[FileService, Depends(_files)]
ConfigsDep = Annotated[ConfigService, Depends(_configs)]


# --------------------------------------------------------------------------- #
#  Mods et plugins
# --------------------------------------------------------------------------- #
@router.get(
    "/files/{area}",
    response_model=list[ManagedFileOut],
    summary="Lister les fichiers d'un dossier",
)
async def list_files(area: str, access: ServerAccess, service: FilesDep) -> list[ManagedFileOut]:
    """Contenu du dossier `mods` ou `plugins`, désactivés compris."""
    server, context = access
    context.require(Permission.FILE_READ, action="consulter les fichiers")
    return [ManagedFileOut(**item.to_dict()) for item in service.list_files(server, area)]


@router.post(
    "/files/{area}",
    response_model=ManagedFileOut,
    status_code=status.HTTP_201_CREATED,
    summary="Téléverser un fichier",
    dependencies=[CsrfProtected],
)
async def upload_file(
    area: str,
    access: ServerAccess,
    service: FilesDep,
    ip: ClientIp,
    file: Annotated[UploadFile, File(description="Fichier .jar à déposer")],
    overwrite: Annotated[bool, Form()] = False,
) -> ManagedFileOut:
    """Dépose un fichier. Il n'est jamais exécuté par MSM."""
    server, context = access
    content = await file.read()
    result = await service.upload(
        server,
        area,
        filename=file.filename or "",
        content=content,
        overwrite=overwrite,
        context=context,
        ip_address=ip,
    )
    return ManagedFileOut(**result.to_dict())


@router.delete(
    "/files/{area}/{name}",
    summary="Supprimer un fichier",
    dependencies=[CsrfProtected],
)
async def delete_file(
    area: str, name: FileNameParam, access: ServerAccess, service: FilesDep, ip: ClientIp
) -> dict[str, str]:
    server, context = access
    await service.delete(server, area, name, context=context, ip_address=ip)
    return {"status": "supprimé", "name": name}


@router.post(
    "/files/{area}/{name}/toggle",
    response_model=ManagedFileOut,
    summary="Activer ou désactiver un fichier",
    dependencies=[CsrfProtected],
)
async def toggle_file(
    area: str,
    name: FileNameParam,
    payload: ToggleRequest,
    access: ServerAccess,
    service: FilesDep,
    ip: ClientIp,
) -> ManagedFileOut:
    """Renomme le fichier plutôt que de le supprimer : le retour arrière reste possible."""
    server, context = access
    result = await service.set_enabled(
        server, area, name, enabled=payload.enabled, context=context, ip_address=ip
    )
    return ManagedFileOut(**result.to_dict())


# --------------------------------------------------------------------------- #
#  Configurations
# --------------------------------------------------------------------------- #
@router.get(
    "/configs",
    response_model=list[ConfigEntryOut],
    summary="Parcourir les configurations",
)
async def browse_configs(
    access: ServerAccess,
    service: ConfigsDep,
    path: Annotated[str | None, Query(max_length=1024)] = None,
) -> list[ConfigEntryOut]:
    """Sous-dossiers et fichiers éditables du chemin demandé."""
    server, context = access
    context.require(Permission.CONFIG_READ, action="consulter les configurations")
    return [ConfigEntryOut(**entry.to_dict()) for entry in service.browse(server, path)]


@router.get("/configs/file", response_model=ConfigFileOut, summary="Lire un fichier")
async def read_config(
    access: ServerAccess,
    service: ConfigsDep,
    path: Annotated[str, Query(min_length=1, max_length=1024)],
) -> ConfigFileOut:
    server, context = access
    context.require(Permission.CONFIG_READ, action="lire une configuration")
    return ConfigFileOut(**service.read_file(server, path))


@router.put(
    "/configs/file",
    response_model=ConfigWriteOut,
    summary="Enregistrer un fichier",
    dependencies=[CsrfProtected],
)
async def write_config(
    payload: ConfigWriteRequest,
    access: ServerAccess,
    service: ConfigsDep,
    ip: ClientIp,
    path: Annotated[str, Query(min_length=1, max_length=1024)],
) -> ConfigWriteOut:
    """Valide la syntaxe puis écrit le contenu **tel quel**, commentaires compris."""
    server, context = access
    result = await service.write_file(server, path, payload.content, context=context, ip_address=ip)
    return ConfigWriteOut(**result)


# --------------------------------------------------------------------------- #
#  server.properties
# --------------------------------------------------------------------------- #
@router.get(
    "/properties",
    response_model=PropertiesOut,
    summary="Lire server.properties",
)
async def read_properties(access: ServerAccess) -> PropertiesOut:
    """Clés du fichier, enrichies de leur type quand il est connu."""
    server, context = access
    context.require(Permission.CONFIG_READ, action="consulter server.properties")

    from pathlib import Path as FsPath

    parsed = properties_module.read(FsPath(server.directory))
    return PropertiesOut(
        exists=parsed.exists,
        entries=[PropertyOut(**entry.to_dict()) for entry in parsed.entries],
    )


@router.put(
    "/properties",
    response_model=PropertiesUpdateOut,
    summary="Modifier server.properties",
    dependencies=[CsrfProtected],
)
async def update_properties(
    payload: PropertiesUpdateRequest,
    access: ServerAccess,
    session: DbSession,
    supervisor: SupervisorDep,
    ip: ClientIp,
) -> PropertiesUpdateOut:
    """Modifie les clés demandées en préservant commentaires et ordre du fichier."""
    server, context = access
    context.require(Permission.PROPERTIES_WRITE, action="modifier server.properties")

    from pathlib import Path as FsPath

    changes = {key: str(value) for key, value in payload.changes.items()}
    updated, needs_restart = properties_module.apply_changes(FsPath(server.directory), changes)

    if updated:
        AuditRepository(session).record(
            action=AuditAction.PROPERTIES_UPDATED,
            summary=(
                f"Modification de server.properties sur « {server.name} » : {', '.join(updated)}."
            ),
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip,
            server_id=server.id,
            target_type="properties",
            payload={"keys": updated},
        )

    runtime = supervisor.find(server.id)
    running = runtime is not None and runtime.state.is_running
    return PropertiesUpdateOut(updated=updated, requires_restart=needs_restart and running)
