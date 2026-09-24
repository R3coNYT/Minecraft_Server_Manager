"""Gestion des mods et des plugins.

Deux dossiers, une seule logique : lister, téléverser, activer, désactiver,
supprimer. Les traiter séparément aurait dupliqué le code sans rien apporter —
la seule différence est le dossier visé et le libellé affiché.

**Désactiver ne supprime pas.** Le fichier est renommé en `<nom>.disabled` : le
serveur l'ignore, mais l'administrateur peut revenir en arrière sans avoir à
retrouver le fichier d'origine. C'est la convention utilisée par la plupart des
lanceurs, donc un fichier déjà désactivé à la main est reconnu tel quel.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from msm.config import Settings
from msm.core.permissions import Permission
from msm.db.models.audit import AuditAction
from msm.db.models.server import Server
from msm.db.repositories import AuditRepository
from msm.exceptions import ConflictError, NotFoundError, ValidationError
from msm.i18n import tr
from msm.logging_conf import get_logger
from msm.security.rbac import AccessContext
from msm.security.safe_path import resolve_within
from msm.security.uploads import check_size, sanitize_filename, strip_executable_bit
from msm.services.hosting_service import HostingService
from msm.utils.files import atomic_write_bytes

logger = get_logger(__name__)

#: Suffixe marquant un fichier désactivé.
DISABLED_SUFFIX = ".disabled"


@dataclass(frozen=True, slots=True)
class FileArea:
    """Un dossier géré par le panneau."""

    key: str
    directory: str
    label: str
    allowed_suffixes: frozenset[str]


AREAS: dict[str, FileArea] = {
    "mods": FileArea("mods", "mods", "Mods", frozenset({".jar"})),
    "plugins": FileArea("plugins", "plugins", "Plugins", frozenset({".jar"})),
}


@dataclass(frozen=True, slots=True)
class ManagedFile:
    """Un fichier du dossier, tel qu'affiché."""

    name: str
    size_bytes: int
    modified_at: str
    enabled: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "enabled": self.enabled,
        }


def get_area(key: str) -> FileArea:
    try:
        return AREAS[key]
    except KeyError:
        raise NotFoundError(
            tr("Unknown folder."),
            cause=tr("“{key}” matches no managed folder.", key=key),
            remediation=tr("Available folders: {folders}.", folders=", ".join(sorted(AREAS))),
        ) from None


class FileService:
    """Cas d'usage des dossiers de mods et de plugins."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._audit = AuditRepository(session)

    # ------------------------------------------------------------------ #
    def _area_path(self, server: Server, area: FileArea, *, create: bool = False) -> Path:
        directory = resolve_within(Path(server.directory), area.directory)
        if not directory.is_dir():
            if not create:
                raise NotFoundError(
                    tr("Folder “{folder}” missing.", folder=area.directory),
                    cause=tr(
                        "The folder “{folder}” does not exist in this server.",
                        folder=area.directory,
                    ),
                    remediation=tr(
                        "This server has no {folder} yet. The folder will be created on the "
                        "first upload.",
                        folder=area.directory,
                    ),
                )
            directory.mkdir(parents=True, exist_ok=True)
        return directory

    def list_files(self, server: Server, area_key: str) -> list[ManagedFile]:
        """Fichiers du dossier, actifs et désactivés confondus."""
        area = get_area(area_key)
        try:
            directory = self._area_path(server, area)
        except NotFoundError:
            # Un dossier absent est une liste vide, pas une erreur d'affichage.
            return []

        files: list[ManagedFile] = []
        for entry in sorted(directory.iterdir(), key=lambda p: p.name.casefold()):
            if not entry.is_file():
                continue

            enabled = not entry.name.endswith(DISABLED_SUFFIX)
            display_name = entry.name[: -len(DISABLED_SUFFIX)] if not enabled else entry.name
            if Path(display_name).suffix.lower() not in area.allowed_suffixes:
                continue

            try:
                stat = entry.stat()
            except OSError:  # pragma: no cover - fichier supprimé entre-temps
                continue

            files.append(
                ManagedFile(
                    name=display_name,
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                    enabled=enabled,
                )
            )
        return files

    def _locate(self, server: Server, area: FileArea, name: str) -> tuple[Path, bool]:
        """Retrouve un fichier par son nom d'affichage. Renvoie ``(chemin, actif)``."""
        safe_name = sanitize_filename(name, allowed_suffixes=area.allowed_suffixes)
        directory = self._area_path(server, area)

        enabled_path = resolve_within(directory, safe_name)
        if enabled_path.is_file():
            return enabled_path, True

        disabled_path = resolve_within(directory, safe_name + DISABLED_SUFFIX)
        if disabled_path.is_file():
            return disabled_path, False

        raise NotFoundError(
            tr("File not found."),
            cause=tr(
                "“{name}” does not exist in the {folder} folder.",
                name=safe_name,
                folder=area.directory,
            ),
            remediation=tr("Refresh the file list."),
        )

    # ------------------------------------------------------------------ #
    #  Actions
    # ------------------------------------------------------------------ #
    async def upload(
        self,
        server: Server,
        area_key: str,
        *,
        filename: str,
        content: bytes,
        overwrite: bool = False,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> ManagedFile:
        """Dépose un fichier dans le dossier, sans jamais l'exécuter."""
        context.require(Permission.FILE_UPLOAD, action=tr("upload a file"))
        await HostingService(self._session, self._settings).check_disk(server.owner)
        area = get_area(area_key)

        safe_name = sanitize_filename(filename, allowed_suffixes=area.allowed_suffixes)
        check_size(len(content), maximum=self._settings.upload_max_size_bytes)

        directory = self._area_path(server, area, create=True)
        target = resolve_within(directory, safe_name)

        if target.exists() and not overwrite:
            raise ConflictError(
                tr("A file with this name already exists."),
                cause=tr(
                    "“{name}” is already present in {folder}.",
                    name=safe_name,
                    folder=area.directory,
                ),
                remediation=tr("Confirm the replacement, or rename the file before uploading."),
            )

        atomic_write_bytes(target, content)
        strip_executable_bit(target)

        self._record(
            AuditAction.FILE_UPLOADED,
            tr(
                "“{file}” uploaded to {folder} of “{server}”.",
                file=safe_name,
                folder=area.directory,
                server=server.name,
            ),
            server,
            context,
            ip_address,
            payload={"area": area.key, "file": safe_name, "size": len(content)},
        )
        logger.info(
            "file_uploaded",
            server_id=server.id,
            area=area.key,
            file=safe_name,
            size=len(content),
            actor=context.username,
        )

        stat = target.stat()
        return ManagedFile(
            name=safe_name,
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            enabled=True,
        )

    async def delete(
        self,
        server: Server,
        area_key: str,
        name: str,
        *,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> None:
        context.require(Permission.FILE_DELETE, action=tr("delete a file"))
        area = get_area(area_key)
        path, _ = self._locate(server, area, name)

        path.unlink()

        self._record(
            AuditAction.FILE_DELETED,
            tr(
                "“{file}” deleted from {folder} of “{server}”.",
                file=path.name,
                folder=area.directory,
                server=server.name,
            ),
            server,
            context,
            ip_address,
            payload={"area": area.key, "file": path.name},
        )
        logger.info(
            "file_deleted",
            server_id=server.id,
            area=area.key,
            file=path.name,
            actor=context.username,
        )

    async def set_enabled(
        self,
        server: Server,
        area_key: str,
        name: str,
        *,
        enabled: bool,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> ManagedFile:
        """Active ou désactive un fichier par simple renommage."""
        context.require(Permission.FILE_TOGGLE, action=tr("enable or disable a file"))
        area = get_area(area_key)
        path, currently_enabled = self._locate(server, area, name)

        if currently_enabled == enabled:
            raise ConflictError(
                tr("Nothing to change."),
                cause=(
                    tr("“{name}” is already enabled.", name=name)
                    if enabled
                    else tr("“{name}” is already disabled.", name=name)
                ),
                remediation=tr("Refresh the file list."),
            )

        directory = path.parent
        # Activer restaure le nom d'origine ; désactiver lui ajoute le suffixe.
        target_name = name if enabled else name + DISABLED_SUFFIX
        target = resolve_within(directory, target_name)

        if target.exists():
            raise ConflictError(
                tr("Cannot rename."),
                cause=tr(
                    "“{name}” already exists in {folder}.", name=target.name, folder=area.directory
                ),
                remediation=tr("Delete the duplicate before trying again."),
            )

        path.rename(target)

        action = AuditAction.FILE_ENABLED if enabled else AuditAction.FILE_DISABLED
        template = (
            "“{file}” enabled in {folder} of “{server}”."
            if enabled
            else "“{file}” disabled in {folder} of “{server}”."
        )
        self._record(
            action,
            tr(template, file=name, folder=area.directory, server=server.name),
            server,
            context,
            ip_address,
            payload={"area": area.key, "file": name, "enabled": enabled},
        )

        stat = target.stat()
        return ManagedFile(
            name=name,
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            enabled=enabled,
        )

    # ------------------------------------------------------------------ #
    def _record(
        self,
        action: AuditAction,
        summary: str,
        server: Server,
        context: AccessContext,
        ip_address: str | None,
        *,
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
            target_type="file",
            target_id=str(payload.get("file")) if payload else None,
            payload=payload,
        )


def validate_area_name(name: str) -> str:
    """Vérifie qu'un nom de fichier est exploitable avant toute résolution."""
    if not name or "/" in name or "\\" in name:
        raise ValidationError(
            tr("Invalid file name."),
            cause=tr("The name must designate a single file, without a path."),
            remediation=tr("Select the file from the list."),
        )
    return name
