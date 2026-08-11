"""Écriture et lecture des archives de sauvegarde.

Format : ``tar.gz``. Un seul format sur toutes les plateformes — MSM tourne sous
Linux comme sous Windows, et deux formats voudraient dire deux chemins de code,
deux jeux de bogues, et des archives non interchangeables entre machines.

Trois exigences gouvernent ce module :

* **une archive incomplète n'existe jamais sous son nom définitif**. L'écriture
  passe par un fichier ``.part`` renommé à la toute fin ; une coupure de courant
  laisse un déchet identifiable, jamais une sauvegarde apparemment valide ;
* **l'extraction ne fait jamais confiance à l'archive**. Un membre nommé
  ``../../etc/cron.d/x`` ou un lien symbolique vers ``/`` écrirait hors du
  dossier cible : chaque membre est validé avant d'être écrit ;
* **l'opération est interruptible**. Une sauvegarde de plusieurs gigaoctets doit
  pouvoir être annulée sans attendre la fin.
"""

from __future__ import annotations

import io
import json
import os
import tarfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from msm.backup.selection import MANIFEST_NAME, ArchiveEntry
from msm.exceptions import MsmError, ValidationError
from msm.logging_conf import get_logger

logger = get_logger(__name__)

#: Extension du fichier en cours d'écriture.
PART_SUFFIX = ".part"

#: Progression signalée au plus tous les 2 % : au-delà, on inonde le bus.
_PROGRESS_STEP = 0.02

ProgressCallback = Callable[[int, int], None]
StopCheck = Callable[[], bool]


class BackupCancelled(MsmError):
    """L'opération a été interrompue à la demande."""

    code = "BACKUP_CANCELLED"
    status_code = 409


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    """Ce qu'a produit une écriture d'archive."""

    path: Path
    size_bytes: int
    file_count: int


def create_archive(
    entries: tuple[ArchiveEntry, ...] | list[ArchiveEntry],
    destination: Path,
    *,
    extra_files: dict[str, bytes] | None = None,
    on_progress: ProgressCallback | None = None,
    should_stop: StopCheck | None = None,
) -> ArchiveResult:
    """Écrit une archive ``tar.gz``. Bloquant : à exécuter dans un thread.

    Les fichiers disparus en cours de route sont ignorés, pas fatals : un serveur
    démarré réécrit ses fichiers en permanence, et abandonner une sauvegarde de
    quatre gigaoctets parce qu'un fichier temporaire s'est volatilisé serait
    absurde.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + PART_SUFFIX)
    total = sum(entry.size for entry in entries) or 1
    done = 0
    written = 0
    last_reported = 0.0

    try:
        with tarfile.open(partial, "w:gz") as archive:
            now = int(time.time())
            for name, payload in (extra_files or {}).items():
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mtime = now
                archive.addfile(info, io.BytesIO(payload))

            for entry in entries:
                if should_stop is not None and should_stop():
                    raise BackupCancelled(
                        "Sauvegarde annulée.",
                        cause="L'opération a été interrompue avant la fin.",
                        remediation="Relancer la sauvegarde si elle est toujours souhaitée.",
                    )
                try:
                    archive.add(entry.source, arcname=entry.arcname, recursive=False)
                except (FileNotFoundError, PermissionError, OSError) as exc:
                    logger.warning("backup_file_skipped", file=entry.arcname, error=str(exc))
                    done += entry.size
                    continue

                written += 1
                done += entry.size
                if on_progress is not None:
                    ratio = done / total
                    if ratio - last_reported >= _PROGRESS_STEP or done >= total:
                        last_reported = ratio
                        on_progress(done, total)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    size = partial.stat().st_size
    # `replace` est atomique : le nom définitif n'apparaît qu'une fois l'archive
    # complète et fermée.
    partial.replace(destination)
    return ArchiveResult(path=destination, size_bytes=size, file_count=written)


def read_manifest(archive_path: Path) -> dict[str, Any]:
    """Relit le manifeste d'une archive, sans rien extraire."""
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            member = archive.extractfile(MANIFEST_NAME)
            if member is None:
                raise KeyError(MANIFEST_NAME)
            return json.loads(member.read().decode("utf-8"))
    except (KeyError, tarfile.TarError, OSError, ValueError) as exc:
        raise ValidationError(
            "Archive illisible.",
            cause=f"{archive_path.name} n'est pas une sauvegarde MSM valide : {exc}",
            remediation="Vérifier que le fichier n'a pas été tronqué ou modifié.",
        ) from exc


def _validate_member(member: tarfile.TarInfo, target: Path) -> None:
    """Refuse tout membre susceptible d'écrire hors du dossier cible."""
    name = member.name
    if member.issym() or member.islnk():
        raise ValidationError(
            "Archive refusée.",
            cause=f"L'archive contient un lien ({name}), qui pourrait pointer hors du serveur.",
            remediation="Ne restaurer que des archives produites par MSM.",
        )
    if member.isdev() or member.ischr() or member.isblk() or member.isfifo():
        raise ValidationError(
            "Archive refusée.",
            cause=f"L'archive contient une entrée qui n'est ni un fichier ni un dossier ({name}).",
            remediation="Ne restaurer que des archives produites par MSM.",
        )

    candidate = Path(name)
    if candidate.is_absolute() or name.startswith(("/", "\\")) or ".." in candidate.parts:
        raise ValidationError(
            "Archive refusée.",
            cause=f"L'archive tente d'écrire hors du dossier du serveur ({name}).",
            remediation="Ne restaurer que des archives produites par MSM.",
        )

    resolved = os.path.normpath(target / name)
    root = os.path.normpath(target)
    if os.path.normcase(resolved) != os.path.normcase(root) and not os.path.normcase(
        resolved
    ).startswith(os.path.normcase(root) + os.sep):
        raise ValidationError(
            "Archive refusée.",
            cause=f"L'archive tente d'écrire hors du dossier du serveur ({name}).",
            remediation="Ne restaurer que des archives produites par MSM.",
        )


def extract_archive(
    archive_path: Path,
    target: Path,
    *,
    skip: frozenset[str] = frozenset(),
    on_progress: ProgressCallback | None = None,
) -> int:
    """Extrait une archive dans ``target``. Bloquant : à exécuter dans un thread.

    Renvoie le nombre de fichiers écrits. Les membres listés dans ``skip`` (les
    fichiers descriptifs de MSM) ne sont pas restaurés : ils décrivent la
    sauvegarde, ils ne font pas partie du serveur.
    """
    target.mkdir(parents=True, exist_ok=True)
    resolved_target = target.resolve()
    written = 0

    with tarfile.open(archive_path, "r:gz") as archive:
        members = [item for item in archive.getmembers() if item.name not in skip]
        for member in members:
            _validate_member(member, resolved_target)

        total = len(members) or 1
        for index, member in enumerate(members, start=1):
            # `filter="data"` est la seconde barrière : il neutralise droits,
            # propriétaires et chemins douteux que la première aurait manqués.
            archive.extract(member, resolved_target, filter="data")
            if member.isfile():
                written += 1
            if on_progress is not None and index % 50 == 0:
                on_progress(index, total)

    if on_progress is not None:
        on_progress(1, 1)
    return written
