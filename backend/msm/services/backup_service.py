"""Sauvegardes : création, restauration, purge.

Une sauvegarde peut durer plusieurs minutes ; elle s'exécute donc en tâche de
fond, comme un événement, avec sa progression publiée sur le bus. La requête HTTP
répond immédiatement avec une ligne d'historique à suivre.

Trois refus délibérés, parce qu'ils protègent des données irremplaçables :

* **restaurer sur un serveur démarré est impossible.** Le serveur écrirait
  par-dessus les fichiers restaurés, ou lirait un monde à moitié remplacé ;
* **restaurer écrase des mondes.** Une sauvegarde de sécurité est donc prise
  automatiquement avant, sans quoi une erreur de manipulation serait définitive ;
* **une archive est vérifiée avant d'être appliquée.** Son manifeste est relu et
  chacun de ses membres validé : une archive étrangère ou trafiquée ne peut pas
  écrire hors du dossier du serveur.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from msm import __version__
from msm.backup.archive import (
    BackupCancelled,
    create_archive,
    extract_archive,
    read_manifest,
)
from msm.backup.hot import frozen_world
from msm.backup.selection import (
    INVENTORY_NAME,
    MANIFEST_NAME,
    build_manifest,
    select_content,
)
from msm.bus import EventBus, get_event_bus, topics
from msm.config import Settings, get_settings
from msm.core.permissions import Permission
from msm.db.models.audit import AuditAction
from msm.db.models.misc import Backup, BackupStatus
from msm.db.models.server import Server
from msm.db.repositories import AuditRepository
from msm.db.session import session_scope
from msm.exceptions import (
    ConfirmationRequired,
    NotFoundError,
    ServerAlreadyRunning,
    ValidationError,
)
from msm.logging_conf import get_logger
from msm.runtime.supervisor import Supervisor
from msm.security.rbac import AccessContext
from msm.security.safe_path import resolve_within

logger = get_logger(__name__)

#: Sauvegardes en cours, par identifiant — permet l'annulation.
_ACTIVE_BACKUPS: dict[int, asyncio.Task[None]] = {}

#: Sauvegarde prise automatiquement avant une restauration.
KIND_PRE_RESTORE = "pre-restore"
KIND_MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class BackupProgress:
    """Avancement publié sur le bus pendant une sauvegarde ou une restauration."""

    backup_id: int
    server_id: int
    status: str
    phase: str
    done: int = 0
    total: int = 1
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "server_id": self.server_id,
            "status": self.status,
            "phase": self.phase,
            "done": self.done,
            "total": self.total,
            "percent": round(100 * self.done / self.total) if self.total else 0,
            "error": self.error,
        }


def archive_name(server_name: str, moment: datetime, kind: str) -> str:
    """Nom de fichier lisible et triable : `survie-20260811-174205-manual.tar.gz`."""
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in server_name.lower())[:48]
    return f"{slug or 'serveur'}-{moment.strftime('%Y%m%d-%H%M%S')}-{kind}.tar.gz"


class BackupService:
    """Cas d'usage des sauvegardes d'un serveur."""

    def __init__(
        self,
        session: AsyncSession,
        supervisor: Supervisor,
        bus: EventBus | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._supervisor = supervisor
        self._bus = bus or get_event_bus()
        self._settings = settings or get_settings()
        self._audit = AuditRepository(session)

    # ------------------------------------------------------------------ #
    #  Consultation
    # ------------------------------------------------------------------ #
    async def list_backups(self, server: Server, *, limit: int = 50) -> list[Backup]:
        statement = (
            select(Backup)
            .where(Backup.server_id == server.id)
            .order_by(Backup.created_at.desc(), Backup.id.desc())
            .limit(limit)
        )
        return list((await self._session.execute(statement)).scalars())

    async def get_backup(self, server: Server, backup_id: int) -> Backup:
        backup = await self._session.get(Backup, backup_id)
        if backup is None or backup.server_id != server.id:
            raise NotFoundError(
                "Sauvegarde introuvable.",
                cause=f"Aucune sauvegarde n'a l'identifiant {backup_id} sur ce serveur.",
                remediation="Rafraîchir la liste des sauvegardes.",
            )
        return backup

    def archive_path(self, backup: Backup) -> Path:
        """Chemin de l'archive, confiné au dossier des sauvegardes.

        Le chemin stocké en base est relu à travers `resolve_within` : même si la
        base était altérée, la lecture ne sortirait pas du dossier prévu.
        """
        return resolve_within(self._settings.backups_root, backup.path, must_exist=True)

    async def describe(self, server: Server, backup_id: int) -> dict[str, Any]:
        """Manifeste d'une sauvegarde : mondes, mods et plugins inventoriés."""
        backup = await self.get_backup(server, backup_id)
        if backup.status is not BackupStatus.COMPLETED:
            raise ValidationError(
                "Sauvegarde incomplète.",
                cause=f"Cette sauvegarde est à l'état {backup.status.value}.",
                remediation="Attendre la fin de la sauvegarde, ou en créer une nouvelle.",
            )
        return await asyncio.to_thread(read_manifest, self.archive_path(backup))

    # ------------------------------------------------------------------ #
    #  Création
    # ------------------------------------------------------------------ #
    async def start_backup(
        self,
        server: Server,
        *,
        context: AccessContext,
        kind: str = KIND_MANUAL,
        ip_address: str | None = None,
    ) -> Backup:
        context.require(Permission.BACKUP_CREATE, action="créer une sauvegarde")

        directory = Path(server.directory)
        if not directory.is_dir():
            raise ValidationError(
                "Dossier du serveur introuvable.",
                cause=f"{directory} n'existe pas ou n'est pas lisible.",
                remediation="Vérifier le chemin du serveur dans ses réglages.",
            )

        self._check_backup_root(directory)
        moment = datetime.now(UTC)
        name = archive_name(server.name, moment, kind)

        backup = Backup(
            server_id=server.id,
            path=name,
            kind=kind,
            status=BackupStatus.RUNNING,
            created_at=moment,
            created_by=context.user_id,
        )
        self._session.add(backup)
        await self._session.flush()

        self._record(
            AuditAction.BACKUP_CREATED,
            f"Sauvegarde de « {server.name} » lancée.",
            server,
            context,
            ip_address,
            payload={"backup_id": backup.id, "kind": kind},
        )
        # La transaction est validée avant de lancer la tâche : celle-ci écrira
        # dans ses propres sessions et doit pouvoir relire cette ligne.
        await self._session.commit()

        task = asyncio.create_task(
            _run_backup(
                backup_id=backup.id,
                server_id=server.id,
                server_name=server.name,
                server_type=server.server_type.value,
                minecraft_version=server.minecraft_version,
                directory=directory,
                destination=self._settings.backups_root / name,
                settings=self._settings,
                supervisor=self._supervisor,
                bus=self._bus,
                actor=context.username,
            ),
            name=f"msm-backup-{backup.id}",
        )
        _ACTIVE_BACKUPS[backup.id] = task
        task.add_done_callback(lambda _: _forget(backup.id, task))
        return backup

    def _check_backup_root(self, server_directory: Path) -> None:
        """Interdit d'écrire les archives dans le dossier sauvegardé.

        Une archive écrite sous le serveur serait emportée par la sauvegarde
        suivante : la taille doublerait à chaque fois, jusqu'à saturer le disque.
        """
        root = self._settings.backups_root.resolve()
        try:
            root.relative_to(server_directory.resolve())
        except ValueError:
            return
        raise ValidationError(
            "Emplacement de sauvegarde invalide.",
            cause=f"Le dossier des sauvegardes ({root}) est à l'intérieur du serveur.",
            remediation=(
                "Choisir un autre dossier via MSM_BACKUP_DIR, hors des dossiers de serveur."
            ),
        )

    async def cancel_backup(
        self, server: Server, backup_id: int, *, context: AccessContext
    ) -> bool:
        context.require(Permission.BACKUP_CREATE, action="annuler une sauvegarde")
        await self.get_backup(server, backup_id)

        task = _ACTIVE_BACKUPS.get(backup_id)
        if task is None or task.done():
            return False
        task.cancel()
        logger.info("backup_cancelled", backup_id=backup_id, actor=context.username)
        return True

    # ------------------------------------------------------------------ #
    #  Restauration
    # ------------------------------------------------------------------ #
    async def restore(
        self,
        server: Server,
        backup_id: int,
        *,
        context: AccessContext,
        confirm: bool = False,
        ip_address: str | None = None,
    ) -> Backup:
        """Remplace mondes et configurations par ceux d'une sauvegarde."""
        context.require(Permission.BACKUP_RESTORE, action="restaurer une sauvegarde")
        backup = await self.get_backup(server, backup_id)

        if backup.status is not BackupStatus.COMPLETED:
            raise ValidationError(
                "Sauvegarde inutilisable.",
                cause=f"Cette sauvegarde est à l'état {backup.status.value}.",
                remediation="Choisir une sauvegarde terminée.",
            )

        runtime = self._supervisor.find(server.id)
        if runtime is not None and runtime.state.is_running:
            raise ServerAlreadyRunning(
                "Le serveur doit être arrêté pour être restauré.",
                cause=(
                    "Restaurer sous un serveur en cours d'exécution écraserait des fichiers "
                    "qu'il est en train de lire et d'écrire."
                ),
                remediation="Arrêter le serveur, puis relancer la restauration.",
            )

        if not confirm:
            raise ConfirmationRequired(
                "Confirmation requise.",
                cause=(
                    "La restauration remplace les mondes et les configurations actuels "
                    f"par ceux du {backup.created_at:%d/%m/%Y à %H:%M}."
                ),
                remediation=(
                    "Renvoyer la requête avec `confirm: true`. "
                    "Une sauvegarde de sécurité sera prise automatiquement avant."
                ),
                context={"backup_id": backup.id},
            )

        # Filet de sécurité : l'état actuel est archivé avant d'être écrasé.
        safety = await self.start_backup(
            server, context=context, kind=KIND_PRE_RESTORE, ip_address=ip_address
        )
        await _await_backup(safety.id)

        # Remplacement et extraction dans un seul appel : entre les deux, le
        # serveur n'aurait plus de monde du tout. Un client qui raccroche ne doit
        # pas pouvoir laisser les choses dans cet état.
        restored = await asyncio.to_thread(
            _apply_restore, self.archive_path(backup), Path(server.directory)
        )

        self._record(
            AuditAction.BACKUP_RESTORED,
            f"Restauration de « {server.name} » depuis la sauvegarde du "
            f"{backup.created_at:%d/%m/%Y %H:%M}.",
            server,
            context,
            ip_address,
            payload={"backup_id": backup.id, "files": restored, "safety_backup_id": safety.id},
        )
        logger.info(
            "backup_restored",
            server_id=server.id,
            backup_id=backup.id,
            files=restored,
            actor=context.username,
        )
        return backup

    # ------------------------------------------------------------------ #
    #  Suppression
    # ------------------------------------------------------------------ #
    async def delete(
        self,
        server: Server,
        backup_id: int,
        *,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> None:
        context.require(Permission.BACKUP_CREATE, action="supprimer une sauvegarde")
        backup = await self.get_backup(server, backup_id)

        if backup.id in _ACTIVE_BACKUPS:
            raise ValidationError(
                "Sauvegarde en cours.",
                cause="Cette sauvegarde est en train d'être écrite.",
                remediation="L'annuler d'abord, ou attendre qu'elle se termine.",
            )

        try:
            self.archive_path(backup).unlink(missing_ok=True)
        except (OSError, ValidationError) as exc:
            # L'archive est déjà absente ou illisible : la ligne d'historique
            # n'a plus de raison de survivre à son fichier.
            logger.warning("backup_file_delete_failed", backup_id=backup.id, error=str(exc))

        await self._session.delete(backup)
        self._record(
            AuditAction.BACKUP_DELETED,
            f"Sauvegarde du {backup.created_at:%d/%m/%Y %H:%M} supprimée.",
            server,
            context,
            ip_address,
            payload={"backup_id": backup.id},
        )

    async def record_download(
        self,
        server: Server,
        backup: Backup,
        *,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> None:
        self._record(
            AuditAction.BACKUP_DOWNLOADED,
            f"Téléchargement de la sauvegarde du {backup.created_at:%d/%m/%Y %H:%M}.",
            server,
            context,
            ip_address,
            payload={"backup_id": backup.id},
        )

    # ------------------------------------------------------------------ #
    async def mark_interrupted(self) -> int:
        """Clôt les sauvegardes restées « en cours » après un arrêt de MSM."""
        statement = select(Backup).where(Backup.status == BackupStatus.RUNNING)
        interrupted = list((await self._session.execute(statement)).scalars())
        for backup in interrupted:
            backup.status = BackupStatus.FAILED
            backup.error = "Sauvegarde interrompue par un redémarrage de MSM."
        if interrupted:
            logger.info("backups_marked_interrupted", count=len(interrupted))
        return len(interrupted)

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
            target_type="backup",
            payload=payload,
        )


# --------------------------------------------------------------------------- #
#  Exécution en tâche de fond
# --------------------------------------------------------------------------- #
def _forget(backup_id: int, task: asyncio.Task[None]) -> None:
    """Retire la tâche du registre, si c'est bien celle qui s'y trouve."""
    if _ACTIVE_BACKUPS.get(backup_id) is task:
        _ACTIVE_BACKUPS.pop(backup_id, None)


async def _await_backup(backup_id: int) -> None:
    """Attend la fin d'une sauvegarde lancée en tâche de fond."""
    task = _ACTIVE_BACKUPS.get(backup_id)
    if task is not None:
        await asyncio.shield(task)


def _apply_restore(archive: Path, directory: Path) -> int:
    """Remplace les mondes puis extrait l'archive. Bloquant, exécuté en thread."""
    manifest = read_manifest(archive)
    _replace_worlds(directory, manifest.get("content", {}))
    return extract_archive(archive, directory, skip=frozenset({MANIFEST_NAME, INVENTORY_NAME}))


def _replace_worlds(directory: Path, content: dict[str, Any]) -> None:
    """Supprime les mondes que l'archive va réécrire.

    Extraire par-dessus ne suffit pas : un monde ayant grandi depuis la
    sauvegarde garderait ses régions récentes, mélangées à celles d'hier. Le
    résultat serait un monde qui n'a jamais existé.
    """
    for name in content.get("worlds", []):
        if not isinstance(name, str) or not name or "/" in name or "\\" in name or ".." in name:
            continue
        target = directory / name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target, ignore_errors=True)


def _check_free_space(destination: Path, needed: int, margin_mb: int) -> None:
    # Le dossier est créé d'abord : interroger l'espace libre d'un chemin
    # inexistant échoue, et c'est exactement le cas de la première sauvegarde.
    destination.parent.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(destination.parent).free
    required = needed + margin_mb * 1024 * 1024
    if free < required:
        raise ValidationError(
            "Espace disque insuffisant.",
            cause=(
                f"{free / 1_073_741_824:.1f} Go disponibles pour "
                f"{required / 1_073_741_824:.1f} Go nécessaires."
            ),
            remediation="Libérer de l'espace ou supprimer d'anciennes sauvegardes.",
        )


async def _run_backup(
    *,
    backup_id: int,
    server_id: int,
    server_name: str,
    server_type: str,
    minecraft_version: str | None,
    directory: Path,
    destination: Path,
    settings: Settings,
    supervisor: Supervisor,
    bus: EventBus,
    actor: str,
) -> None:
    """Écrit une archive et tient l'historique à jour."""
    topic = topics.server_topic(server_id, topics.BACKUP)
    loop = asyncio.get_running_loop()
    cancelled = False

    def publish(progress: BackupProgress) -> None:
        bus.publish(topic, progress.to_dict())

    publish(BackupProgress(backup_id, server_id, "RUNNING", "Analyse du contenu"))

    try:
        selection = await asyncio.to_thread(select_content, directory)
        if not selection.entries:
            raise ValidationError(
                "Rien à sauvegarder.",
                cause=f"Aucun monde ni fichier de configuration trouvé dans {directory}.",
                remediation="Démarrer le serveur une première fois pour qu'il crée son monde.",
            )
        _check_free_space(destination, selection.total_bytes, settings.backup_free_space_margin_mb)

        extra = build_manifest(
            selection,
            server_name=server_name,
            server_type=server_type,
            minecraft_version=minecraft_version,
            msm_version=__version__,
        )

        def on_progress(done: int, total: int) -> None:
            # Appelé depuis le thread d'écriture : le bus n'est pas conçu pour
            # être touché depuis un autre thread.
            loop.call_soon_threadsafe(
                publish,
                BackupProgress(backup_id, server_id, "RUNNING", "Copie des fichiers", done, total),
            )

        runtime = supervisor.find(server_id)
        write = _write(selection.entries, destination, extra, on_progress, lambda: cancelled)

        if runtime is not None and runtime.state.is_running:
            publish(BackupProgress(backup_id, server_id, "RUNNING", "Suspension des écritures"))
            async with frozen_world(runtime, bus):
                result = await write
        else:
            result = await write

        await _finish(backup_id, BackupStatus.COMPLETED, size=result.size_bytes)
        publish(BackupProgress(backup_id, server_id, "COMPLETED", "Sauvegarde terminée", 1, 1))
        logger.info(
            "backup_completed",
            server_id=server_id,
            backup_id=backup_id,
            files=result.file_count,
            size_bytes=result.size_bytes,
            actor=actor,
        )
        await _purge_old(server_id, settings)

    except asyncio.CancelledError:
        # Le thread d'écriture ne s'arrête pas sur une annulation asyncio : le
        # drapeau lui dit de s'interrompre au prochain fichier.
        cancelled = True
        destination.unlink(missing_ok=True)
        # Bouclier : sans lui, l'écriture du statut serait à son tour annulée et
        # la sauvegarde resterait « en cours » pour toujours.
        await asyncio.shield(_finish(backup_id, BackupStatus.FAILED, error="Sauvegarde annulée."))
        publish(
            BackupProgress(backup_id, server_id, "FAILED", "Annulée", error="Sauvegarde annulée.")
        )
        raise
    except BackupCancelled:
        destination.unlink(missing_ok=True)
        await _finish(backup_id, BackupStatus.FAILED, error="Sauvegarde annulée.")
        publish(BackupProgress(backup_id, server_id, "FAILED", "Annulée"))
    except Exception as exc:
        # Message **et** cause : cette chaîne est tout ce que l'interface montre
        # de l'échec, « Rien à sauvegarder. » seul n'aiderait personne.
        message = " ".join(
            part
            for part in (getattr(exc, "message", None) or str(exc), getattr(exc, "cause", None))
            if part
        )
        destination.unlink(missing_ok=True)
        await _finish(backup_id, BackupStatus.FAILED, error=message)
        publish(BackupProgress(backup_id, server_id, "FAILED", "Échec", error=message))
        logger.warning("backup_failed", server_id=server_id, backup_id=backup_id, error=message)


def _write(
    entries: Any,
    destination: Path,
    extra: dict[str, bytes],
    on_progress: Any,
    should_stop: Any,
) -> Any:
    """Écrit l'archive dans un thread : `tarfile` est bloquant."""
    return asyncio.to_thread(
        create_archive,
        entries,
        destination,
        extra_files=extra,
        on_progress=on_progress,
        should_stop=should_stop,
    )


async def _finish(
    backup_id: int, status: BackupStatus, *, size: int | None = None, error: str | None = None
) -> None:
    try:
        async with session_scope() as session:
            backup = await session.get(Backup, backup_id)
            if backup is None:  # pragma: no cover - supprimée entre-temps
                return
            backup.status = status
            backup.size_bytes = size
            backup.error = error
    except Exception as exc:
        logger.warning("backup_status_persist_failed", backup_id=backup_id, error=str(exc))


async def _purge_old(server_id: int, settings: Settings) -> None:
    """Supprime les sauvegardes au-delà du nombre conservé.

    Les purges se font après une réussite : tant qu'une nouvelle sauvegarde n'a
    pas abouti, on ne détruit pas les anciennes.
    """
    try:
        async with session_scope() as session:
            statement = (
                select(Backup)
                .where(Backup.server_id == server_id, Backup.status == BackupStatus.COMPLETED)
                .order_by(Backup.created_at.desc(), Backup.id.desc())
            )
            backups = list((await session.execute(statement)).scalars())
            for backup in backups[settings.backup_retention :]:
                try:
                    resolve_within(settings.backups_root, backup.path).unlink(missing_ok=True)
                except (OSError, ValidationError) as exc:
                    logger.warning("backup_purge_file_failed", backup_id=backup.id, error=str(exc))
                await session.delete(backup)
                logger.info("backup_purged", backup_id=backup.id, server_id=server_id)
    except Exception as exc:
        logger.warning("backup_purge_failed", server_id=server_id, error=str(exc))
