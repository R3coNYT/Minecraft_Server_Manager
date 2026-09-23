"""Intégration avec le serveur de fichiers d'un launcher personnalisé.

Trois mécanismes, qui partagent un verrou par serveur pour ne jamais se croiser :

* la **synchronisation** lit le manifest du serveur de fichiers, calcule ce qui
  change et l'installe — immédiatement si le serveur est arrêté, sinon au
  prochain démarrage ;
* l'**application différée** met en place les changements en attente, juste
  avant que le processus ne démarre (point d'accroche du superviseur) ou dès que
  le serveur est trouvé arrêté ;
* la **publication** envoie au serveur de fichiers la liste des mods désactivés
  dans MSM, pour que les joueurs la reçoivent à leur prochaine synchronisation.

Tout part de MSM vers le serveur de fichiers : MSM n'a jamais besoin d'être
joignable depuis Internet.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from msm.config import Settings, get_settings
from msm.core.permissions import Permission
from msm.db.models.audit import AuditAction
from msm.db.models.launcher import LauncherFile, LauncherIntegration, SyncStatus
from msm.db.models.server import Server
from msm.db.repositories import AuditRepository
from msm.db.session import session_scope
from msm.exceptions import MsmError, NotFoundError, ValidationError
from msm.i18n import tr
from msm.launcher_sync import disk
from msm.launcher_sync.manifest import (
    SIDE_ALIASES,
    Manifest,
    ManifestEntry,
    parse_manifest,
    validate_path,
)
from msm.launcher_sync.plan import (
    Plan,
    compute_plan,
    is_mass_deletion,
    plan_from_dict,
    plan_to_dict,
)
from msm.launcher_sync.remote import FileServerClient
from msm.launcher_sync.sides import BOTH, CLIENT, detect_side
from msm.logging_conf import get_logger
from msm.runtime.server_runtime import PreStartHook
from msm.runtime.supervisor import Supervisor
from msm.security.crypto import decrypt_secret, encrypt_secret
from msm.security.rbac import AccessContext

logger = get_logger(__name__)

DEFAULT_SYNC_PATHS: tuple[str, ...] = ("mods/",)
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 7 * 24 * 60
#: Après un échec d'envoi, on attend avant de réessayer : un serveur de fichiers
#: en panne n'a pas à être sollicité toutes les trente secondes.
PUSH_RETRY_DELAY = timedelta(minutes=2)

#: Un verrou par serveur : synchronisation, application et publication
#: s'excluent mutuellement.
_LOCKS: dict[int, asyncio.Lock] = {}
_PUSH_NOT_BEFORE: dict[int, datetime] = {}
#: Tâches de fond lancées hors de la boucle, gardées en vie jusqu'à leur fin.
_BACKGROUND: set[asyncio.Task[Any]] = set()


_LOCKS_LOOP: asyncio.AbstractEventLoop | None = None


def _lock(server_id: int) -> asyncio.Lock:
    """Verrou du serveur, propre à la boucle d'événements en cours.

    Un `asyncio.Lock` se lie à la première boucle qui l'attend ; réutilisé dans
    une autre — une application recréée, chaque test — il lève une erreur. Les
    verrous, comme l'attente après un échec d'envoi, appartiennent donc à la
    boucle qui les a créés et sont oubliés quand elle change.
    """
    global _LOCKS_LOOP
    loop = asyncio.get_running_loop()
    if _LOCKS_LOOP is not loop:
        _LOCKS.clear()
        _PUSH_NOT_BEFORE.clear()
        _LOCKS_LOOP = loop
    return _LOCKS.setdefault(server_id, asyncio.Lock())


def staging_dir(settings: Settings, server_id: int) -> Path:
    """Dossier des fichiers préparés. Hors du serveur : jamais chargé par erreur."""
    return settings.data_dir / "launcher-staging" / str(server_id)


def normalize_sync_paths(raw: list[str] | None) -> list[str]:
    """Dossiers synchronisés, validés et terminés par `/`."""
    values = raw or list(DEFAULT_SYNC_PATHS)
    result: list[str] = []
    for value in values:
        cleaned = str(value).strip().strip("/")
        if not cleaned:
            continue
        validate_path(cleaned)
        result.append(cleaned + "/")
    if not result:
        raise ValidationError(
            tr("No folder to synchronise."),
            cause=tr("The list of synchronised folders is empty."),
            remediation=tr("Enter at least one folder, for example mods/."),
        )
    return sorted(set(result))


# --------------------------------------------------------------------------- #
#  Cas d'usage appelés par l'API
# --------------------------------------------------------------------------- #
class LauncherService:
    """Réglages et déclenchements manuels de l'intégration d'un serveur."""

    def __init__(
        self,
        session: AsyncSession,
        supervisor: Supervisor,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._session = session
        self._supervisor = supervisor
        self._settings = settings or get_settings()
        self._transport = transport
        self._audit = AuditRepository(session)

    async def get(self, server: Server) -> LauncherIntegration | None:
        return (
            await self._session.execute(
                select(LauncherIntegration).where(LauncherIntegration.server_id == server.id)
            )
        ).scalar_one_or_none()

    async def configure(
        self,
        server: Server,
        *,
        file_server_url: str,
        sync_paths: list[str] | None = None,
        interval_minutes: int = 30,
        enabled: bool = True,
        push_token: str | None = None,
        clear_push_token: bool = False,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> LauncherIntegration:
        context.require(Permission.SERVER_EDIT, action=tr("configure the launcher integration"))

        # Valide l'adresse avant tout enregistrement.
        base = FileServerClient(file_server_url, transport=self._transport).base_url
        paths = normalize_sync_paths(sync_paths)
        if not MIN_INTERVAL_MINUTES <= interval_minutes <= MAX_INTERVAL_MINUTES:
            raise ValidationError(
                tr("Interval out of range."),
                cause=tr("{minutes} minutes requested.", minutes=interval_minutes),
                remediation=tr(
                    "Choose between {minimum} minutes and one week.", minimum=MIN_INTERVAL_MINUTES
                ),
            )

        integration = await self.get(server)
        created = integration is None
        if integration is None:
            integration = LauncherIntegration(server_id=server.id, file_server_url=base)
            self._session.add(integration)

        changed_source = integration.file_server_url != base
        integration.file_server_url = base
        integration.sync_paths = paths
        integration.interval_minutes = interval_minutes
        integration.enabled = enabled
        if clear_push_token:
            integration.push_token_encrypted = None
        elif push_token:
            integration.push_token_encrypted = encrypt_secret(push_token.strip())
            # Un nouveau jeton : l'état doit être republié avec lui.
            integration.pushed_revision = 0
            _PUSH_NOT_BEFORE.pop(server.id, None)

        if created or changed_source:
            # Nouvelle source : rien de ce qu'on savait d'elle ne vaut plus.
            integration.manifest_etag = None
            integration.manifest_entries = []
            integration.side_cache = {}
            integration.pack_version = None
        if not enabled:
            # Désactivée : rien ne doit plus toucher au serveur, même au démarrage.
            integration.pending_plan = None
        # Une intégration (ré)activée se synchronise sans attendre l'intervalle.
        integration.next_sync_at = datetime.now(UTC) if enabled else None

        await self._session.flush()
        self._audit.record(
            action=AuditAction.LAUNCHER_UPDATED,
            summary=tr("Launcher integration of “{name}”: {url}.", name=server.name, url=base),
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            server_id=server.id,
            target_type="launcher",
            # Jamais le jeton : le journal est lisible par d'autres.
            payload={"url": base, "paths": paths, "interval": interval_minutes, "enabled": enabled},
        )
        return integration

    async def remove(
        self, server: Server, *, context: AccessContext, ip_address: str | None = None
    ) -> None:
        """Retire l'intégration. Les mods installés restent en place."""
        context.require(Permission.SERVER_EDIT, action=tr("remove the launcher integration"))
        integration = await self._require(server)
        await self._session.delete(integration)
        await self._session.execute(delete(LauncherFile).where(LauncherFile.server_id == server.id))
        self._audit.record(
            action=AuditAction.LAUNCHER_UPDATED,
            summary=tr("Launcher integration of “{name}” removed.", name=server.name),
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            server_id=server.id,
            target_type="launcher",
        )
        disk.clear_staging(staging_dir(self._settings, server.id))

    async def set_side(
        self,
        server: Server,
        path: str,
        side: str | None,
        *,
        context: AccessContext,
    ) -> LauncherIntegration:
        """Force le côté d'un mod, ou revient à la détection automatique."""
        context.require(Permission.SERVER_EDIT, action=tr("change a mod's side"))
        integration = await self._require(server)
        clean = validate_path(path)
        overrides = dict(integration.side_overrides or {})
        if side is None:
            overrides.pop(clean, None)
        else:
            resolved = SIDE_ALIASES.get(side.lower())
            if resolved is None:
                raise ValidationError(
                    tr("Unknown side."),
                    cause=tr("“{side}” is neither client, server nor both.", side=side),
                    remediation=tr("Choose client, server or both."),
                )
            overrides[clean] = resolved
        integration.side_overrides = overrides
        # La correction prend effet à la synchronisation suivante, lancée aussitôt.
        integration.next_sync_at = datetime.now(UTC)
        return integration

    async def sync_now(
        self,
        server: Server,
        *,
        allow_mass_delete: bool = False,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> LauncherIntegration:
        context.require(Permission.SERVER_EDIT, action=tr("synchronise with the launcher"))
        await self._require(server)
        self._audit.record(
            action=AuditAction.LAUNCHER_SYNCED,
            summary=tr("Launcher synchronisation of “{name}” started by hand.", name=server.name),
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            server_id=server.id,
            target_type="launcher",
            payload={"allow_mass_delete": allow_mass_delete},
        )
        # La synchronisation ouvre ses propres sessions : elle doit voir l'audit
        # et les réglages déjà validés.
        await self._session.commit()
        await run_sync(
            server.id,
            supervisor=self._supervisor,
            settings=self._settings,
            transport=self._transport,
            allow_mass_delete=allow_mass_delete,
        )
        await publish_state(
            server.id, settings=self._settings, transport=self._transport, force=True
        )
        return await self._refreshed(server)

    async def publish_now(self, server: Server, *, context: AccessContext) -> LauncherIntegration:
        context.require(Permission.SERVER_EDIT, action=tr("publish the mod state"))
        await self._require(server)
        await self._session.commit()
        _PUSH_NOT_BEFORE.pop(server.id, None)
        await publish_state(
            server.id, settings=self._settings, transport=self._transport, force=True
        )
        return await self._refreshed(server)

    async def _refreshed(self, server: Server) -> LauncherIntegration:
        integration = await self._require(server)
        await self._session.refresh(integration)
        return integration

    async def _require(self, server: Server) -> LauncherIntegration:
        integration = await self.get(server)
        if integration is None:
            raise NotFoundError(
                tr("No launcher integration."),
                cause=tr("“{name}” is not linked to any file server.", name=server.name),
                remediation=tr("Enter the address of the launcher's file server."),
            )
        return integration


def describe(integration: LauncherIntegration, *, running: bool) -> dict[str, Any]:
    """État de l'intégration tel qu'exposé à l'interface — sans le jeton."""
    token = (
        decrypt_secret(integration.push_token_encrypted)
        if integration.push_token_encrypted
        else None
    )
    pending = plan_from_dict(integration.pending_plan) if integration.pending_plan else None
    overrides = integration.side_overrides or {}

    return {
        "enabled": integration.enabled,
        "file_server_url": integration.file_server_url,
        "sync_paths": integration.sync_paths,
        "interval_minutes": integration.interval_minutes,
        "push_configured": token is not None,
        "push_token_hint": f"…{token[-4:]}" if token and len(token) > 4 else None,
        "push_token_unreadable": integration.push_token_encrypted is not None and token is None,
        "next_sync_at": integration.next_sync_at,
        "last_sync_at": integration.last_sync_at,
        "last_sync_status": integration.last_sync_status.value,
        "last_sync_error": integration.last_sync_error,
        "last_sync_summary": integration.last_sync_summary or {},
        "pack_version": integration.pack_version,
        "pending": (
            {
                "installs": len(pending.installs),
                "removes": len(pending.removes),
                "download_bytes": pending.download_bytes,
                "server_running": running,
            }
            if pending is not None and not pending.empty
            else None
        ),
        "publish": {
            "state_revision": integration.state_revision,
            "pushed_revision": integration.pushed_revision,
            "up_to_date": integration.pushed_revision >= integration.state_revision,
            "disabled_files": integration.disabled_files or [],
            "last_push_at": integration.last_push_at,
            "last_push_error": integration.last_push_error,
        },
        "mods": [
            {**entry, "override": overrides.get(entry["path"])}
            for entry in (integration.manifest_entries or [])
        ],
    }


# --------------------------------------------------------------------------- #
#  Synchronisation
# --------------------------------------------------------------------------- #
def _entries_from_cache(raw: list[dict[str, Any]]) -> Manifest:
    """Manifest reconstitué depuis la dernière lecture, quand il n'a pas changé."""
    return Manifest(
        pack_version=None,
        mc_version=None,
        loader=None,
        entries=tuple(
            ManifestEntry(
                path=item["path"],
                sha256=item["sha256"],
                size=int(item["size"]),
                side=item.get("declared_side"),
                disabled=bool(item.get("disabled_upstream", False)),
            )
            for item in raw
        ),
    )


async def _tracked(session: AsyncSession, server_id: int) -> dict[str, LauncherFile]:
    rows = (
        await session.execute(select(LauncherFile).where(LauncherFile.server_id == server_id))
    ).scalars()
    return {row.path: row for row in rows}


def _known_hashes(tracked: dict[str, LauncherFile]) -> dict[str, tuple[int, int, str]]:
    """Empreintes connues, sous les deux noms qu'un fichier suivi peut porter."""
    known: dict[str, tuple[int, int, str]] = {}
    for row in tracked.values():
        value = (row.size, row.mtime_ns, row.sha256)
        known[row.path] = value
        known[row.path + disk.DISABLED_SUFFIX] = value
    return known


async def _record_files(
    session: AsyncSession,
    server_id: int,
    server_dir: Path,
    tracked: dict[str, LauncherFile],
    plan: Plan,
    *,
    include_installs: bool,
) -> None:
    """Met à jour la liste de ce que MSM a installé."""
    now = datetime.now(UTC)

    def upsert(path: str, sha256: str) -> None:
        stat = None
        for name in (path, path + disk.DISABLED_SUFFIX):
            candidate = server_dir / name
            if candidate.is_file():
                stat = candidate.stat()
                break
        row = tracked.get(path)
        if row is None:
            row = LauncherFile(server_id=server_id, path=path, installed_at=now, sha256=sha256)
            session.add(row)
            tracked[path] = row
        row.sha256 = sha256
        row.size = stat.st_size if stat else 0
        row.mtime_ns = stat.st_mtime_ns if stat else 0
        row.installed_at = now

    for path, sha256 in plan.adopts:
        upsert(path, sha256)
    if include_installs:
        for item in plan.installs:
            upsert(item.path, item.sha256)
        for path in plan.removes:
            row = tracked.pop(path, None)
            if row is not None:
                await session.delete(row)


def _mark_applied(integration: LauncherIntegration, plan: Plan) -> None:
    """Reporte un plan appliqué sur l'état affiché des mods du modpack."""
    installed = {item.path: item.disabled for item in plan.installs}
    entries = []
    for entry in integration.manifest_entries or []:
        if entry["path"] in installed:
            entry = {
                **entry,
                "on_server": "disabled" if installed[entry["path"]] else "enabled",
            }
        entries.append(entry)
    # Nouvelle liste : SQLAlchemy ne voit pas les modifications en place d'un JSON.
    integration.manifest_entries = entries


def _fail(integration: LauncherIntegration, exc: Exception) -> None:
    message = getattr(exc, "message", None) or str(exc)
    cause = getattr(exc, "cause", None)
    integration.last_sync_status = SyncStatus.FAILED
    integration.last_sync_error = f"{message} {cause}" if cause else message


async def run_sync(
    server_id: int,
    *,
    supervisor: Supervisor,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
    allow_mass_delete: bool = False,
) -> SyncStatus | None:
    """Synchronise un serveur avec son serveur de fichiers. Ne lève jamais."""
    async with _lock(server_id), session_scope() as session:
        integration = (
            await session.execute(
                select(LauncherIntegration).where(LauncherIntegration.server_id == server_id)
            )
        ).scalar_one_or_none()
        server = await session.get(Server, server_id)
        if integration is None or server is None or not integration.enabled:
            return None

        now = datetime.now(UTC)
        integration.last_sync_at = now
        integration.next_sync_at = now + timedelta(minutes=integration.interval_minutes)

        try:
            await _synchronize(
                session,
                integration,
                server,
                supervisor=supervisor,
                settings=settings,
                transport=transport,
                allow_mass_delete=allow_mass_delete,
            )
        except MsmError as exc:
            _fail(integration, exc)
            logger.warning(
                "launcher_sync_failed", server_id=server_id, error=integration.last_sync_error
            )
        except Exception as exc:  # une synchronisation ne doit rien faire tomber
            _fail(integration, exc)
            logger.exception("launcher_sync_crashed", server_id=server_id)
        return integration.last_sync_status


async def _synchronize(
    session: AsyncSession,
    integration: LauncherIntegration,
    server: Server,
    *,
    supervisor: Supervisor,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None,
    allow_mass_delete: bool,
) -> None:
    client = FileServerClient(integration.file_server_url, transport=transport)
    server_dir = Path(server.directory)
    prefixes = tuple(integration.sync_paths or DEFAULT_SYNC_PATHS)
    staging = staging_dir(settings, server.id)

    fetched = await client.fetch_manifest(
        etag=integration.manifest_etag if integration.manifest_entries else None
    )
    if fetched.data is None:
        manifest = _entries_from_cache(integration.manifest_entries)
    else:
        manifest = parse_manifest(fetched.data)
        integration.manifest_etag = fetched.etag
        integration.pack_version = manifest.pack_version

    candidates = manifest.under(prefixes)
    tracked = await _tracked(session, server.id)
    local = await asyncio.to_thread(disk.scan_local, server_dir, prefixes, _known_hashes(tracked))

    # --- Côté de chaque fichier ------------------------------------------------
    overrides = integration.side_overrides or {}
    cache = dict(integration.side_cache or {})
    sides: dict[str, tuple[str, str]] = {}
    for entry in candidates:
        if entry.path in overrides:
            sides[entry.path] = (overrides[entry.path], "override")
        elif entry.side is not None:
            sides[entry.path] = (entry.side, "manifest")
        elif entry.sha256 in cache:
            sides[entry.path] = (cache[entry.sha256], "detected")
        elif not entry.path.endswith(".jar"):
            sides[entry.path] = (BOTH, "default")
        else:
            # Inspecter le JAR : sur place s'il est déjà là, sinon après
            # téléchargement — une seule fois par empreinte, grâce au cache.
            current = local.get(entry.path)
            if current is not None and current.sha256 == entry.sha256:
                name = entry.path + (disk.DISABLED_SUFFIX if current.disabled else "")
                source = server_dir / name
            else:
                source = disk.staged_path(staging, entry.sha256)
                await client.download(entry.path, entry.sha256, entry.size, source)
            side = await asyncio.to_thread(detect_side, source)
            cache[entry.sha256] = side
            sides[entry.path] = (side, "detected")
    integration.side_cache = cache

    server_entries = [entry for entry in candidates if sides[entry.path][0] != CLIENT]
    client_only = [entry for entry in candidates if sides[entry.path][0] == CLIENT]

    integration.manifest_entries = [
        {
            "path": entry.path,
            "sha256": entry.sha256,
            "size": entry.size,
            "declared_side": entry.side,
            "disabled_upstream": entry.disabled,
            "side": sides[entry.path][0],
            "side_source": sides[entry.path][1],
            "on_server": (
                "disabled"
                if local.get(entry.path) and local[entry.path].disabled
                else "enabled"
                if entry.path in local
                else "absent"
            ),
        }
        for entry in candidates
    ]

    # --- Plan --------------------------------------------------------------------
    plan = compute_plan(server_entries, local, {path: row.sha256 for path, row in tracked.items()})

    if is_mass_deletion(plan, len(tracked)) and not allow_mass_delete:
        integration.last_sync_status = SyncStatus.BLOCKED
        integration.last_sync_error = tr(
            "The manifest would remove {removed} of the {tracked} installed files: mass "
            "deletion blocked as a precaution. Check the file server, then confirm from MSM "
            "if this is intended.",
            removed=len(plan.removes),
            tracked=len(tracked),
        )
        integration.pending_plan = None
        return

    # Les fichiers déjà corrects sont pris en charge sans attendre.
    await _record_files(session, server.id, server_dir, tracked, plan, include_installs=False)

    for item in plan.installs:
        await client.download(
            item.path, item.sha256, item.size, disk.staged_path(staging, item.sha256)
        )

    summary: dict[str, Any] = {
        "installs": len(plan.installs),
        "removes": len(plan.removes),
        "adopted": len(plan.adopts),
        "unchanged": plan.unchanged,
        "client_only": len(client_only),
        "download_bytes": plan.download_bytes,
        "notes": list(plan.notes),
    }
    integration.last_sync_summary = summary
    integration.last_sync_error = None

    if plan.empty:
        integration.pending_plan = None
        integration.last_sync_status = SyncStatus.UP_TO_DATE
        await asyncio.to_thread(disk.clear_staging, staging)
        return

    runtime = supervisor.find(server.id)
    if runtime is not None and runtime.state.is_running:
        # Remplacer un JAR sous une JVM en marche ne servirait à rien avant le
        # redémarrage, et Windows le refuserait : on attend.
        integration.pending_plan = plan_to_dict(plan)
        integration.last_sync_status = SyncStatus.PENDING_RESTART
        return

    await asyncio.to_thread(disk.apply_plan, server_dir, staging, plan)
    await _record_files(session, server.id, server_dir, tracked, plan, include_installs=True)
    await asyncio.to_thread(disk.clear_staging, staging)
    _mark_applied(integration, plan)
    integration.pending_plan = None
    integration.last_sync_status = SyncStatus.APPLIED
    logger.info(
        "launcher_sync_applied",
        server_id=server.id,
        **{k: v for k, v in summary.items() if k != "notes"},
    )


async def apply_pending(
    server_id: int, *, settings: Settings, trigger: str, defer_bookkeeping: bool = False
) -> list[str]:
    """Applique les changements en attente. Renvoie les messages pour la console.

    L'appelant garantit qu'aucun processus ne tourne : le point d'accroche avant
    démarrage est appelé alors que l'état est déjà « démarrage » mais que rien
    n'est encore lancé, et la boucle vérifie l'état avant d'appeler.

    :param defer_bookkeeping: n'écrire en base qu'après coup, dans une tâche de
        fond. Indispensable au point d'accroche : il s'exécute pendant la requête
        de démarrage, dont la transaction tient déjà le verrou d'écriture SQLite
        — écrire depuis une autre session attendrait qu'elle se termine, donc
        indéfiniment. La lecture, elle, n'est jamais bloquée (WAL).
    """
    async with _lock(server_id):
        async with session_scope() as session:
            integration = (
                await session.execute(
                    select(LauncherIntegration).where(LauncherIntegration.server_id == server_id)
                )
            ).scalar_one_or_none()
            server = await session.get(Server, server_id)
            if integration is None or server is None or not integration.pending_plan:
                return []
            plan = plan_from_dict(integration.pending_plan)
            server_dir = Path(server.directory)
        staging = staging_dir(settings, server_id)

        missing = [
            item.path
            for item in plan.installs
            if not disk.staged_path(staging, item.sha256).is_file()
        ]
        if missing:
            # Préparation perdue (dossier nettoyé, disque changé) : on n'applique
            # pas un plan incomplet, la synchronisation suivante le refera.
            applied: Plan | None = None
            messages = [
                tr(
                    "Launcher synchronisation: prepared files not found, they will be "
                    "downloaded again at the next synchronisation."
                )
            ]
        else:
            applied = plan
            changes = await asyncio.to_thread(disk.apply_plan, server_dir, staging, plan)
            messages = [
                tr("Launcher synchronisation applied: {change}", change=change)
                for change in changes
            ]
            logger.info("launcher_pending_applied", server_id=server_id, trigger=trigger)

    bookkeeping = _record_applied(server_id, server_dir, applied, settings=settings)
    if defer_bookkeeping:
        task = asyncio.create_task(bookkeeping, name=f"msm-launcher-applied-{server_id}")
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)
    else:
        await bookkeeping
    return messages


async def _record_applied(
    server_id: int, server_dir: Path, plan: Plan | None, *, settings: Settings
) -> None:
    """Consigne un plan appliqué — ou abandonné si ``plan`` vaut ``None``.

    Si cette écriture échoue, rien n'est perdu : la synchronisation suivante
    retrouve les fichiers en place, les reprend en charge et vide le plan.
    """
    try:
        async with _lock(server_id), session_scope() as session:
            integration = (
                await session.execute(
                    select(LauncherIntegration).where(LauncherIntegration.server_id == server_id)
                )
            ).scalar_one_or_none()
            if integration is None:
                return
            integration.pending_plan = None
            if plan is None:
                integration.next_sync_at = datetime.now(UTC)
                return
            tracked = await _tracked(session, server_id)
            await _record_files(
                session, server_id, server_dir, tracked, plan, include_installs=True
            )
            await asyncio.to_thread(disk.clear_staging, staging_dir(settings, server_id))
            _mark_applied(integration, plan)
            integration.last_sync_status = SyncStatus.APPLIED
    except Exception:
        logger.exception("launcher_bookkeeping_failed", server_id=server_id)


# --------------------------------------------------------------------------- #
#  Publication de l'état aux joueurs
# --------------------------------------------------------------------------- #
async def publish_state(
    server_id: int,
    *,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
    force: bool = False,
) -> bool:
    """Publie la liste des mods désactivés si elle a changé. Ne lève jamais.

    Renvoie ``True`` si un envoi a réussi.
    """
    now = datetime.now(UTC)
    if not force and _PUSH_NOT_BEFORE.get(server_id, now) > now:
        return False

    async with _lock(server_id), session_scope() as session:
        integration = (
            await session.execute(
                select(LauncherIntegration).where(LauncherIntegration.server_id == server_id)
            )
        ).scalar_one_or_none()
        server = await session.get(Server, server_id)
        if integration is None or server is None or not integration.enabled:
            return False

        prefixes = tuple(integration.sync_paths or DEFAULT_SYNC_PATHS)
        disabled = await asyncio.to_thread(disk.disabled_paths, Path(server.directory), prefixes)
        # La révision suit la liste, quelle que soit la cause du changement :
        # bascule dans MSM, fichier renommé à la main, synchronisation.
        if disabled != (integration.disabled_files or []):
            integration.state_revision += 1
            integration.disabled_files = disabled
        elif integration.state_revision == 0:
            # Premier passage : l'état initial est publié, même vide, pour que le
            # serveur de fichiers ne garde pas un état hérité d'ailleurs.
            integration.state_revision = 1

        if integration.pushed_revision >= integration.state_revision:
            return False

        token = (
            decrypt_secret(integration.push_token_encrypted)
            if integration.push_token_encrypted
            else None
        )
        if token is None:
            # Publication non configurée : l'état reste connu de MSM, il partira
            # dès qu'un jeton sera renseigné.
            return False

        client = FileServerClient(integration.file_server_url, transport=transport)
        try:
            await client.push_state(
                token=token,
                server_name=server.name,
                revision=integration.state_revision,
                disabled=disabled,
            )
        except MsmError as exc:
            cause = getattr(exc, "cause", None)
            integration.last_push_error = f"{exc.message} {cause}" if cause else exc.message
            _PUSH_NOT_BEFORE[server_id] = now + PUSH_RETRY_DELAY
            logger.warning(
                "launcher_push_failed", server_id=server_id, error=integration.last_push_error
            )
            return False

        integration.pushed_revision = integration.state_revision
        integration.last_push_at = now
        integration.last_push_error = None
        _PUSH_NOT_BEFORE.pop(server_id, None)
        logger.info(
            "launcher_state_pushed",
            server_id=server_id,
            revision=integration.state_revision,
            disabled=len(disabled),
        )
        return True


# --------------------------------------------------------------------------- #
#  Boucle de fond et point d'accroche
# --------------------------------------------------------------------------- #
def make_pre_start_hook(settings: Settings) -> PreStartHook:
    """Applique les changements en attente juste avant qu'un serveur démarre."""

    async def hook(server_id: int) -> list[str]:
        return await apply_pending(
            server_id, settings=settings, trigger="start", defer_bookkeeping=True
        )

    return hook


class LauncherSyncer:
    """Boucle de fond : synchronisations dues, changements en attente, publication."""

    def __init__(
        self,
        supervisor: Supervisor,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._supervisor = supervisor
        self._settings = settings or get_settings()
        #: Transport HTTP partagé par la boucle et l'API ; remplaçable en test.
        self.transport = transport
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._background: set[asyncio.Task[Any]] = set()

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="msm-launcher-sync")

    async def stop(self, *, timeout: float = 10.0) -> None:
        self._stop.set()
        for task in list(self._background):
            task.cancel()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError):  # pragma: no cover
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        finally:
            self._task = None

    def notify_mods_changed(self, server_id: int) -> None:
        """Un mod vient d'être activé ou désactivé : publier sans attendre la boucle."""
        task = asyncio.create_task(
            publish_state(server_id, settings=self._settings, transport=self.transport, force=True),
            name=f"msm-launcher-publish-{server_id}",
        )
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _run(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._settings.launcher_tick_s)
            if self._stop.is_set():
                break
            try:
                await self.tick()
            except Exception:  # la boucle survit à tout
                logger.exception("launcher_tick_failed")

    async def tick(self, *, now: datetime | None = None) -> None:
        moment = now or datetime.now(UTC)
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(
                        LauncherIntegration.server_id,
                        LauncherIntegration.next_sync_at,
                        LauncherIntegration.pending_plan,
                    ).where(LauncherIntegration.enabled.is_(True))
                )
            ).all()

        for server_id, next_sync_at, pending in rows:
            if next_sync_at is not None and next_sync_at <= moment:
                await run_sync(
                    server_id,
                    supervisor=self._supervisor,
                    settings=self._settings,
                    transport=self.transport,
                )
            elif pending:
                runtime = self._supervisor.find(server_id)
                if runtime is None or not runtime.state.is_running:
                    # Serveur arrêté entre-temps : autant appliquer maintenant
                    # que d'attendre qu'on le redémarre.
                    await apply_pending(
                        server_id, settings=self._settings, trigger="server stopped"
                    )
            await publish_state(server_id, settings=self._settings, transport=self.transport)
