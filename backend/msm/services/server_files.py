"""Suppression des fichiers d'un serveur : supprimer un serveur l'efface du disque.

Le seul endroit de MSM qui efface un dossier entier : tout est vérifié **avant**
de toucher à quoi que ce soit, et la suppression elle-même ne suit que ce qui a
été vérifié.

Un dossier n'est effacé que s'il est sous une racine autorisée, n'est ni une
racine ni le dossier des comptes, ne contient rien de MSM (données, journaux,
sauvegardes, code), et ne contient ni n'est contenu dans le dossier d'un autre
serveur. Les archives de sauvegarde du serveur partent avec lui : sans lui,
plus rien ne permet de les restaurer. Le dossier du compte est retiré s'il
reste vide ; il renaît avec le prochain serveur.
"""

from __future__ import annotations

import contextlib
import shutil
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from msm.config import PROJECT_ROOT, Settings
from msm.db.models import Backup
from msm.db.models.server import Server
from msm.exceptions import PathTraversalError, ValidationError
from msm.i18n import tr
from msm.security.safe_path import resolve_within
from msm.services.hosting_service import HostingService


@dataclass(slots=True)
class FileDeletion:
    """Ce qui sera effacé, une fois tout vérifié."""

    directory: Path
    archives: list[Path] = field(default_factory=list)
    #: Dossier du compte, retiré seulement s'il se retrouve vide.
    home: Path | None = None

    def execute(self) -> list[str]:
        """Efface ; renvoie ce qui n'a pas pu l'être. Ne lève jamais."""
        failures: list[str] = []

        def retry(function: Any, path: str, _error: Any) -> None:
            # Fichier en lecture seule (Windows surtout) : on rend le droit, puis
            # on réessaie une fois.
            try:
                Path(path).chmod(stat.S_IWRITE | stat.S_IREAD)
                function(path)
            except OSError:
                failures.append(path)

        if self.directory.exists():
            if sys.version_info >= (3, 12):
                shutil.rmtree(self.directory, onexc=retry)
            else:  # pragma: no cover - Python 3.11
                shutil.rmtree(self.directory, onerror=retry)
        for archive in self.archives:
            try:
                archive.unlink(missing_ok=True)
            except OSError:
                failures.append(str(archive))
        if self.home is not None:
            with contextlib.suppress(OSError):
                self.home.rmdir()  # seulement s'il est vide
        return failures


def _refuse(cause: str) -> ValidationError:
    return ValidationError(
        tr("The server's files cannot be deleted."),
        cause=cause,
        remediation=tr("Give the server a folder of its own, then delete it again."),
    )


def _overlaps(one: Path, other: Path) -> bool:
    return one == other or one in other.parents or other in one.parents


async def plan_file_deletion(
    session: AsyncSession, settings: Settings, server: Server, others: list[Server]
) -> FileDeletion:
    """Vérifie que le dossier du serveur peut être effacé, et prépare l'effacement."""
    from msm.services.server_service import check_within_roots

    target = Path(server.directory).expanduser().resolve()
    check_within_roots(settings, target)

    hosting = HostingService(session, settings)
    users_root = hosting.users_root(await hosting.load()).expanduser().resolve()
    roots = {Path(root).expanduser().resolve() for root in settings.server_roots}
    if target in roots | {users_root} or target.parent == target:
        raise _refuse(tr("{path} is a servers root, not a server folder.", path=target))

    for protected in (settings.data_dir, settings.log_dir, settings.backups_root, PROJECT_ROOT):
        resolved = protected.expanduser().resolve()
        if resolved == target or target in resolved.parents:
            raise _refuse(tr("{path} holds MSM's own files.", path=target))

    for other in others:
        folder = Path(other.directory).expanduser().resolve()
        if other.id != server.id and _overlaps(target, folder):
            raise _refuse(
                tr(
                    "{path} overlaps the folder of server “{name}”.",
                    path=target,
                    name=other.name,
                )
            )

    archives: list[Path] = []
    rows = await session.execute(select(Backup.path).where(Backup.server_id == server.id))
    for (relative,) in rows:
        if not relative:
            continue
        with contextlib.suppress(PathTraversalError, OSError):
            archives.append(resolve_within(settings.backups_root, relative))

    home = (await hosting.home_of(server.owner)).expanduser().resolve()
    return FileDeletion(
        directory=target, archives=archives, home=home if target.parent == home else None
    )
