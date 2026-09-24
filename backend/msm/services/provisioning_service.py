"""Création d'un serveur de zéro : validation, puis tâche de fond.

La demande est **validée tout de suite**, dans la requête : un nom déjà pris, un
dossier hors des racines autorisées ou déjà rempli, une mémoire incohérente sont
refusés avant qu'un seul octet soit téléchargé. Seul ce qui prend du temps —
téléchargement, installeur — part en tâche de fond.

Une création échouée **ne laisse rien derrière elle** : le dossier qu'elle a créé
est supprimé ; un dossier qui existait déjà (vide) est vidé à nouveau.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from msm.bus import topics
from msm.config import Settings
from msm.core.permissions import Permission
from msm.db.models.user import User
from msm.db.repositories import ServerRepository
from msm.db.session import session_scope
from msm.downloads.sources import SOURCES, resolve
from msm.exceptions import ConflictError, NotFoundError, ValidationError
from msm.i18n import tr
from msm.logging_conf import get_logger
from msm.provisioning import files
from msm.provisioning.installer import find_java, run_installer
from msm.provisioning.jobs import JobRegistry, ProvisioningJob, Step, StepKey, StepStatus
from msm.runtime.supervisor import Supervisor
from msm.security.access import server_context
from msm.security.rbac import AccessContext
from msm.services.download_service import download_file
from msm.services.lifecycle_service import LifecycleService
from msm.services.server_service import ServerService, slugify

logger = get_logger(__name__)

#: Port par défaut de Minecraft, proposé au premier serveur.
DEFAULT_PORT = 25565
#: En deçà, même un serveur Vanilla vide peine à démarrer.
MIN_MEMORY_MB = 512

#: Sujet du bus sur lequel la progression est publiée.
PROVISIONING_TOPIC = topics.system_topic("provisioning")


@dataclass(frozen=True, slots=True)
class ProvisioningRequest:
    name: str
    directory: str
    distribution: str
    version: str
    build: str | None
    memory_min_mb: int
    memory_max_mb: int
    port: int
    accept_eula: bool
    start_after: bool


class ProvisioningService:
    """Valide une demande de création et lance la tâche correspondante."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        supervisor: Supervisor,
        registry: JobRegistry,
    ) -> None:
        self._session = session
        self._settings = settings
        self._supervisor = supervisor
        self._registry = registry
        self._servers = ServerRepository(session)

    # ------------------------------------------------------------------ #
    #  Valeurs proposées
    # ------------------------------------------------------------------ #
    async def defaults(self, name: str, *, context: AccessContext) -> dict[str, Any]:
        """Racines autorisées, dossier et port proposés pour un nouveau serveur."""
        context.require(Permission.SERVER_CREATE, action=tr("create a server"))
        roots = [str(Path(root).expanduser()) for root in self._settings.server_roots]
        folder = slugify(name) if name.strip() else ""
        directory = str(Path(roots[0]) / folder) if roots and folder else ""

        ports = [
            server.settings.port
            for server in await self._servers.list_all()
            if server.settings is not None and server.settings.port
        ]
        return {
            "roots": roots,
            "directory": directory,
            "port": max(ports) + 1 if ports else DEFAULT_PORT,
        }

    # ------------------------------------------------------------------ #
    #  Lancement
    # ------------------------------------------------------------------ #
    async def start(
        self,
        request: ProvisioningRequest,
        *,
        context: AccessContext,
        actor: User,
        ip_address: str | None = None,
    ) -> ProvisioningJob:
        context.require(Permission.SERVER_CREATE, action=tr("create a server"))
        directory = await self._validate(request, owner_id=actor.id)

        installer = SOURCES[request.distribution]["kind"] == "installer"
        steps = [Step(StepKey.FOLDER), Step(StepKey.DOWNLOAD)]
        if installer:
            steps.append(Step(StepKey.INSTALL))
        steps += [Step(StepKey.CONFIGURE), Step(StepKey.REGISTER)]
        if request.start_after:
            steps.append(Step(StepKey.START))

        job = ProvisioningJob(
            name=request.name.strip(),
            directory=str(directory),
            distribution=request.distribution,
            version=request.version,
            build=request.build,
            created_by=actor.id,
            steps=steps,
        )
        self._registry.add(job)
        job.task = asyncio.create_task(
            _run(
                job,
                request,
                settings=self._settings,
                supervisor=self._supervisor,
                ip_address=ip_address,
            ),
            name=f"msm-provisioning-{job.id}",
        )
        logger.info(
            "provisioning_started",
            job=job.id,
            name=job.name,
            distribution=job.distribution,
            version=job.version,
        )
        return job

    def get(self, job_id: str, *, context: AccessContext) -> ProvisioningJob:
        context.require(Permission.SERVER_CREATE, action=tr("create a server"))
        job = self._registry.get(job_id)
        # Chacun ne suit que ses propres créations.
        if job is None or job.created_by != context.user_id:
            raise NotFoundError(
                tr("Creation not found."),
                cause=tr("No server creation has the identifier {id}.", id=job_id),
                remediation=tr("Start the creation again from the dashboard."),
            )
        return job

    async def _validate(self, request: ProvisioningRequest, *, owner_id: int) -> Path:
        name = request.name.strip()
        if not name:
            raise ValidationError(
                tr("Missing server name."),
                cause=tr("The name cannot be empty."),
                remediation=tr("Enter a name for this server."),
            )
        if await self._servers.get_by_name(name, owner_id=owner_id) is not None or any(
            job.name == name and job.created_by == owner_id for job in self._registry.running()
        ):
            raise ConflictError(
                tr("This server name is already in use."),
                cause=tr("You already have a server named “{name}”.", name=name),
                remediation=tr("Choose another name."),
            )

        if request.distribution not in SOURCES:
            raise ValidationError(
                tr("Unknown server type."),
                cause=tr("“{key}” is not a type MSM can install.", key=request.distribution),
                remediation=tr("Choose one of: {choices}.", choices=", ".join(SOURCES)),
            )
        if not re.fullmatch(r"[\w.+-]{1,64}", request.version) or (
            request.build is not None and not re.fullmatch(r"[\w.+-]{1,64}", request.build)
        ):
            raise ValidationError(
                tr("Invalid version."),
                cause=tr("The version or build contains unexpected characters."),
                remediation=tr("Choose a version from the list offered."),
            )

        if request.memory_min_mb < MIN_MEMORY_MB or request.memory_max_mb < request.memory_min_mb:
            raise ValidationError(
                tr("Invalid memory."),
                cause=tr(
                    "Memory must be at least {minimum} MB, and the maximum not below the minimum.",
                    minimum=MIN_MEMORY_MB,
                ),
                remediation=tr("Correct the minimum and maximum memory."),
            )
        if not 1 <= request.port <= 65535:
            raise ValidationError(
                tr("Invalid port."),
                cause=tr("{port} is not a valid network port.", port=request.port),
                remediation=tr("Enter a port between 1 and 65535 (25565 by default)."),
            )

        service = ServerService(self._session, self._settings, self._supervisor)
        directory = service.check_directory(request.directory, must_exist=False)
        if not directory.parent.is_dir():
            raise ValidationError(
                tr("Parent folder not found."),
                cause=tr("{path} does not exist.", path=directory.parent),
                remediation=tr("Create the parent folder, or choose another location."),
            )
        if not files.is_empty_or_missing(directory):
            raise ConflictError(
                tr("This folder is not empty."),
                cause=tr("{path} already contains files.", path=directory),
                remediation=tr("Choose a new folder, or use “Add an existing server”."),
            )
        if await self._servers.get_by_directory(str(directory)) is not None or any(
            job.directory == str(directory) for job in self._registry.running()
        ):
            raise ConflictError(
                tr("This folder is already managed."),
                cause=tr("A server already points to {path}.", path=directory),
                remediation=tr("Choose another folder, or edit the existing server."),
            )
        return directory


# --------------------------------------------------------------------------- #
#  Tâche de fond
# --------------------------------------------------------------------------- #
async def _run(
    job: ProvisioningJob,
    request: ProvisioningRequest,
    *,
    settings: Settings,
    supervisor: Supervisor,
    ip_address: str | None,
) -> None:
    """Déroule la création. Ne lève jamais : l'issue est dans `job`."""
    directory = Path(job.directory)
    created_folder = not directory.exists()
    source = SOURCES[request.distribution]

    def publish() -> None:
        supervisor.bus.publish(PROVISIONING_TOPIC, job.to_dict())

    last_percent = -1

    def on_progress(written: int, total: int | None) -> None:
        nonlocal last_percent
        job.downloaded_bytes = written
        job.progress = min(written / total, 1.0) if total else None
        # Une publication par point de pourcentage, pas une par morceau.
        percent = int((job.progress or 0) * 100)
        if percent != last_percent:
            last_percent = percent
            publish()

    registered = False
    try:
        job.begin(StepKey.FOLDER)
        publish()
        directory.mkdir(exist_ok=True)
        job.finish(StepKey.FOLDER, str(directory))

        job.begin(StepKey.DOWNLOAD)
        publish()
        target = await resolve(request.distribution, request.version, request.build)
        destination = directory / target.filename
        await download_file(target, destination, on_progress=on_progress)
        job.finish(StepKey.DOWNLOAD, target.filename)

        launcher_key = "jar"
        overrides: dict[str, Any] = {
            "memory_min_mb": request.memory_min_mb,
            "memory_max_mb": request.memory_max_mb,
            "port": request.port,
            # Relatif au dossier du serveur : le lanceur refuse un chemin absolu.
            "jar_path": destination.name,
        }
        if target.kind == "installer":
            job.begin(StepKey.INSTALL, tr("This can take a few minutes."))
            publish()
            script = await run_installer(destination, directory, java=find_java())
            files.write_jvm_memory(directory, request.memory_min_mb, request.memory_max_mb)
            launcher_key = "batch" if script.suffix == ".bat" else "shell"
            overrides.pop("jar_path")
            overrides["script_path"] = script.name
            job.finish(StepKey.INSTALL, script.name)

        job.begin(StepKey.CONFIGURE)
        publish()
        if request.accept_eula:
            files.write_eula(directory)
        files.write_port(directory, request.port)
        job.finish(StepKey.CONFIGURE)

        job.begin(StepKey.REGISTER)
        publish()
        async with session_scope() as session:
            actor = await session.get(User, job.created_by)
            if actor is None:  # pragma: no cover - compte supprimé pendant la création
                raise NotFoundError(
                    tr("Account not found."),
                    cause=tr("The account that started the creation no longer exists."),
                    remediation=tr("Sign in again, then start the creation again."),
                )
            server = await ServerService(session, settings, supervisor).create_server(
                name=job.name,
                directory=str(directory),
                launcher_key=launcher_key,
                server_type=source["server_type"],
                minecraft_version=request.version,
                settings_overrides=overrides,
                actor=actor,
                ip_address=ip_address,
            )
            server_id = server.id
        registered = True
        job.finish(StepKey.REGISTER)

        if request.start_after:
            job.begin(StepKey.START)
            publish()
            await _start(server_id, job, supervisor=supervisor, ip_address=ip_address)

        job.complete(server_id)
        logger.info("provisioning_completed", job=job.id, server_id=server_id)
    except BaseException as exc:
        if not registered:
            await asyncio.to_thread(files.clear_directory, directory, remove_itself=created_folder)
        job.fail(exc)
        logger.warning("provisioning_failed", job=job.id, error=str(exc))
        if not isinstance(exc, Exception):
            publish()
            raise
    finally:
        publish()


async def _start(
    server_id: int, job: ProvisioningJob, *, supervisor: Supervisor, ip_address: str | None
) -> None:
    """Démarrage facultatif : son échec n'annule pas une création réussie."""
    try:
        async with session_scope() as session:
            actor = await session.get(User, job.created_by)
            server = await ServerRepository(session).get(server_id)
            if actor is None or server is None:  # pragma: no cover - supprimés entre-temps
                return
            await LifecycleService(session, supervisor).start(
                server,
                context=await server_context(session, actor, server),
                ip_address=ip_address,
            )
        job.finish(StepKey.START)
    except Exception as exc:
        # Le serveur existe : on le dit, et l'utilisateur le démarrera à la main.
        step = job.step(StepKey.START)
        step.status = StepStatus.FAILED
        step.detail = getattr(exc, "message", None) or str(exc)
