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
from msm.logging_conf import get_logger
from msm.security.rbac import AccessContext
from msm.security.safe_path import resolve_within
from msm.security.uploads import check_size, sanitize_filename, strip_executable_bit
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
            "Dossier inconnu.",
            cause=f"« {key} » ne correspond à aucun dossier géré.",
            remediation=f"Dossiers disponibles : {', '.join(sorted(AREAS))}.",
        ) from None


class FileService:
    """Cas d'usage des dossiers de mods et de plugins."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._settings = settings
        self._audit = AuditRepository(session)

    # ------------------------------------------------------------------ #
    def _area_path(self, server: Server, area: FileArea, *, create: bool = False) -> Path:
        directory = resolve_within(Path(server.directory), area.directory)
        if not directory.is_dir():
            if not create:
                raise NotFoundError(
                    f"Dossier {area.label.lower()} absent.",
                    cause=f"Le dossier « {area.directory} » n'existe pas dans ce serveur.",
                    remediation=(
                        f"Ce serveur ne gère pas de {area.label.lower()}. "
                        "Le dossier sera créé au premier téléversement."
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
            "Fichier introuvable.",
            cause=f"« {safe_name} » n'existe pas dans le dossier {area.directory}.",
            remediation="Rafraîchir la liste des fichiers.",
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
        context.require(Permission.FILE_UPLOAD, action="téléverser un fichier")
        area = get_area(area_key)

        safe_name = sanitize_filename(filename, allowed_suffixes=area.allowed_suffixes)
        check_size(len(content), maximum=self._settings.upload_max_size_bytes)

        directory = self._area_path(server, area, create=True)
        target = resolve_within(directory, safe_name)

        if target.exists() and not overwrite:
            raise ConflictError(
                "Un fichier de ce nom existe déjà.",
                cause=f"« {safe_name} » est déjà présent dans {area.directory}.",
                remediation="Confirmer le remplacement, ou renommer le fichier avant l'envoi.",
            )

        atomic_write_bytes(target, content)
        strip_executable_bit(target)

        self._record(
            AuditAction.FILE_UPLOADED,
            f"Téléversement de « {safe_name} » dans {area.directory} de « {server.name} ».",
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
        context.require(Permission.FILE_DELETE, action="supprimer un fichier")
        area = get_area(area_key)
        path, _ = self._locate(server, area, name)

        path.unlink()

        self._record(
            AuditAction.FILE_DELETED,
            f"Suppression de « {path.name} » dans {area.directory} de « {server.name} ».",
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
        context.require(Permission.FILE_TOGGLE, action="activer ou désactiver un fichier")
        area = get_area(area_key)
        path, currently_enabled = self._locate(server, area, name)

        if currently_enabled == enabled:
            raise ConflictError(
                "Aucun changement à appliquer.",
                cause=f"« {name} » est déjà {'actif' if enabled else 'désactivé'}.",
                remediation="Rafraîchir la liste des fichiers.",
            )

        directory = path.parent
        # Activer restaure le nom d'origine ; désactiver lui ajoute le suffixe.
        target_name = name if enabled else name + DISABLED_SUFFIX
        target = resolve_within(directory, target_name)

        if target.exists():
            raise ConflictError(
                "Renommage impossible.",
                cause=f"« {target.name} » existe déjà dans {area.directory}.",
                remediation="Supprimer le doublon avant de réessayer.",
            )

        path.rename(target)

        action = AuditAction.FILE_ENABLED if enabled else AuditAction.FILE_DISABLED
        verb = "Activation" if enabled else "Désactivation"
        self._record(
            action,
            f"{verb} de « {name} » dans {area.directory} de « {server.name} ».",
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
            "Nom de fichier invalide.",
            cause="Le nom ne doit désigner qu'un fichier, sans chemin.",
            remediation="Sélectionner le fichier depuis la liste.",
        )
    return name
