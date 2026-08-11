"""Navigation et édition des fichiers de configuration.

Deux principes gouvernent ce service :

**On valide, on ne reformate jamais.** Le contenu soumis est vérifié
syntaxiquement puis écrit **tel quel**. Passer par un analyseur puis un
sérialiseur détruirait les commentaires et l'ordre des clés — ce dont dépendent
la plupart des fichiers de configuration de mods.

**On expose ce qui est éditable, pas tout le disque.** L'arborescence liste les
dossiers et les fichiers dont l'extension est reconnue ; les mondes, archives et
données binaires n'ont pas à transiter par un éditeur de texte.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from msm.core.permissions import Permission
from msm.db.models.audit import AuditAction
from msm.db.models.server import Server
from msm.db.repositories import AuditRepository
from msm.exceptions import ValidationError
from msm.logging_conf import get_logger
from msm.security.rbac import AccessContext
from msm.security.safe_path import relative_to_root, resolve_within
from msm.utils.files import atomic_write_text, read_text_guessing_encoding

logger = get_logger(__name__)

#: Extensions éditables et format associé, pour la coloration et la validation.
EDITABLE_FORMATS: dict[str, str] = {
    ".json": "json",
    ".json5": "text",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".properties": "properties",
    ".cfg": "text",
    ".conf": "text",
    ".ini": "text",
    ".txt": "text",
    ".md": "markdown",
    ".snbt": "text",
    ".mcmeta": "json",
}

#: Dossiers systématiquement masqués : volumineux, binaires ou sans intérêt.
HIDDEN_DIRECTORIES = frozenset({".git", "cache", "libraries", "versions", "crash-reports"})

#: Au-delà, le fichier n'est pas ouvert dans l'éditeur : il n'est plus de la
#: configuration mais des données, et le navigateur ne les afficherait pas bien.
MAX_EDITABLE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ConfigEntry:
    """Une entrée de l'arborescence de configuration."""

    name: str
    path: str
    is_directory: bool
    size_bytes: int = 0
    modified_at: str = ""
    format: str = ""
    editable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "is_directory": self.is_directory,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "format": self.format,
            "editable": self.editable,
        }


def detect_format(path: Path) -> str:
    return EDITABLE_FORMATS.get(path.suffix.lower(), "")


class ConfigService:
    """Cas d'usage de l'éditeur de configurations."""

    def __init__(self, session: AsyncSession) -> None:
        self._audit = AuditRepository(session)

    # ------------------------------------------------------------------ #
    def browse(self, server: Server, relative: str | None = None) -> list[ConfigEntry]:
        """Contenu d'un dossier : sous-dossiers puis fichiers éditables."""
        root = Path(server.directory)
        directory = resolve_within(root, relative, must_exist=True)

        if not directory.is_dir():
            raise ValidationError(
                "Ce chemin n'est pas un dossier.",
                cause=f"« {relative} » désigne un fichier.",
                remediation="Ouvrir le fichier plutôt que de le parcourir.",
            )

        entries: list[ConfigEntry] = []
        for child in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
            if child.name.startswith(".") or child.name.casefold() in HIDDEN_DIRECTORIES:
                continue

            try:
                stat = child.stat()
            except OSError:  # pragma: no cover - fichier supprimé entre-temps
                continue

            path_value = relative_to_root(root, child)
            modified = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()

            if child.is_dir():
                entries.append(
                    ConfigEntry(
                        name=child.name,
                        path=path_value,
                        is_directory=True,
                        modified_at=modified,
                    )
                )
                continue

            file_format = detect_format(child)
            if not file_format:
                continue

            entries.append(
                ConfigEntry(
                    name=child.name,
                    path=path_value,
                    is_directory=False,
                    size_bytes=stat.st_size,
                    modified_at=modified,
                    format=file_format,
                    editable=stat.st_size <= MAX_EDITABLE_BYTES,
                )
            )

        return entries

    def read_file(self, server: Server, relative: str) -> dict[str, Any]:
        """Contenu d'un fichier de configuration."""
        root = Path(server.directory)
        path = resolve_within(root, relative, must_exist=True)

        if path.is_dir():
            raise ValidationError(
                "Ce chemin est un dossier.",
                cause=f"« {relative} » ne peut pas être ouvert dans l'éditeur.",
                remediation="Sélectionner un fichier.",
            )

        file_format = detect_format(path)
        if not file_format:
            raise ValidationError(
                "Type de fichier non éditable.",
                cause=f"L'extension « {path.suffix or 'aucune'} » n'est pas prise en charge.",
                remediation=("Formats éditables : " + ", ".join(sorted(EDITABLE_FORMATS)) + "."),
            )

        stat = path.stat()
        if stat.st_size > MAX_EDITABLE_BYTES:
            raise ValidationError(
                "Fichier trop volumineux pour l'éditeur.",
                cause=f"{stat.st_size / 1024 / 1024:.1f} Mo pour une limite de 2 Mo.",
                remediation="Modifier ce fichier directement sur le serveur.",
            )

        content, encoding = read_text_guessing_encoding(path)
        return {
            "path": relative_to_root(root, path),
            "name": path.name,
            "format": file_format,
            "content": content,
            "encoding": encoding,
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        }

    # ------------------------------------------------------------------ #
    async def write_file(
        self,
        server: Server,
        relative: str,
        content: str,
        *,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Valide puis écrit un fichier, sans en modifier la mise en forme."""
        context.require(Permission.CONFIG_WRITE, action="modifier une configuration")

        root = Path(server.directory)
        path = resolve_within(root, relative, must_exist=True)

        file_format = detect_format(path)
        if not file_format:
            raise ValidationError(
                "Type de fichier non éditable.",
                cause=f"L'extension « {path.suffix or 'aucune'} » n'est pas prise en charge.",
                remediation="Modifier ce fichier directement sur le serveur.",
            )

        if len(content.encode("utf-8")) > MAX_EDITABLE_BYTES:
            raise ValidationError(
                "Contenu trop volumineux.",
                cause="Le fichier dépasse la limite de 2 Mo.",
                remediation="Réduire le contenu ou modifier le fichier sur le serveur.",
            )

        validate_syntax(content, file_format)
        atomic_write_text(path, content, encoding="utf-8")

        self._audit.record(
            action=AuditAction.CONFIG_UPDATED,
            summary=f"Modification de « {relative_to_root(root, path)} » sur « {server.name} ».",
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            server_id=server.id,
            target_type="config",
            target_id=relative_to_root(root, path),
            payload={"format": file_format, "size": len(content)},
        )
        logger.info(
            "config_updated",
            server_id=server.id,
            file=relative_to_root(root, path),
            actor=context.username,
        )

        stat = path.stat()
        return {
            "path": relative_to_root(root, path),
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        }


def validate_syntax(content: str, file_format: str) -> None:
    """Vérifie la syntaxe avant écriture, sans modifier le contenu.

    Une configuration invalide empêcherait le serveur de démarrer, et l'erreur
    n'apparaîtrait qu'au prochain lancement — bien après la modification. Mieux
    vaut refuser tout de suite, en indiquant la ligne fautive.
    """
    try:
        if file_format == "json":
            json.loads(content)
        elif file_format == "yaml":
            yaml.safe_load(content)
        elif file_format == "toml":
            tomllib.loads(content)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            "Syntaxe JSON invalide.",
            cause=f"Ligne {exc.lineno}, colonne {exc.colno} : {exc.msg}.",
            remediation="Corriger la syntaxe avant d'enregistrer.",
        ) from exc
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        cause = (
            f"Ligne {mark.line + 1}, colonne {mark.column + 1} : {getattr(exc, 'problem', exc)}."
            if mark is not None
            else str(exc)
        )
        raise ValidationError(
            "Syntaxe YAML invalide.",
            cause=cause,
            remediation="Corriger la syntaxe avant d'enregistrer.",
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValidationError(
            "Syntaxe TOML invalide.",
            cause=str(exc),
            remediation="Corriger la syntaxe avant d'enregistrer.",
        ) from exc
