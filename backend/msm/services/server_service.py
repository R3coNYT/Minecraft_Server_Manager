"""Gestion des serveurs : création, configuration, mise en service du runtime.

Ce service est la charnière entre la base de données (ce que l'administrateur a
configuré) et le runtime (ce qui tourne réellement). Il traduit un
:class:`Server` en :class:`ServerRuntimeConfig` et tient le superviseur à jour.

Le runtime, lui, ignore complètement l'existence de SQLAlchemy — c'est ce qui
permet de le tester avec un faux serveur, sans base.
"""

from __future__ import annotations

import asyncio
import re
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from msm.bus import topics
from msm.config import Settings
from msm.core.permissions import Permission, Role, sees_every_server
from msm.core.restart_policy import AutoRestartMode, RestartPolicy
from msm.core.states import ServerState
from msm.db.models.audit import AuditAction
from msm.db.models.server import Server, ServerSettings
from msm.db.models.user import User
from msm.db.repositories import AuditRepository, ServerRepository, build_settings
from msm.exceptions import ConflictError, NotFoundError, ValidationError
from msm.i18n import tr
from msm.launchers import LaunchContext
from msm.launchers import registry as launcher_registry
from msm.logging_conf import get_logger
from msm.minecraft import detector
from msm.minecraft.capabilities import detect_capabilities
from msm.minecraft.types import ServerType
from msm.runtime.backends.sandbox import SandboxSpec, sandbox_memory_limit
from msm.runtime.orphans import find_server_process
from msm.runtime.server_runtime import ServerRuntimeConfig
from msm.runtime.supervisor import Supervisor
from msm.security.rbac import AccessContext
from msm.services.hosting_service import HostingService

logger = get_logger(__name__)

#: Réglages qui décident de ce qui s'exécute sur la machine, et du port qu'elle
#: ouvre : réservés aux admins de MSM, même sur un serveur dont on est propriétaire.
LAUNCH_SETTINGS: frozenset[str] = frozenset(
    {"java_path", "jar_path", "script_path", "custom_argv", "jvm_args", "extra_args", "env", "port"}
)

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Identifiant lisible dans une URL, dérivé du nom du serveur."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    slug = _SLUG_STRIP_RE.sub("-", ascii_only).strip("-")
    return slug or "serveur"


def check_within_roots(settings: Settings, resolved: Path) -> Path:
    """Vérifie que le dossier est sous une racine autorisée, si la liste existe.

    Sans cette restriction, un administrateur pourrait désigner ``/etc`` comme
    dossier de serveur et l'exposer à l'éditeur de configurations.
    """
    roots = settings.server_roots
    if not roots:
        return resolved
    for root in roots:
        try:
            root_resolved = Path(root).expanduser().resolve()
        except OSError:  # pragma: no cover - racine mal configurée
            continue
        if resolved == root_resolved or root_resolved in resolved.parents:
            return resolved

    allowed = ", ".join(str(Path(root)) for root in roots)
    raise ValidationError(
        tr("Folder outside the allowed locations."),
        cause=tr("{path} is not under an allowed root.", path=resolved),
        remediation=tr("Choose a folder under: {roots}.", roots=allowed),
    )


class ServerService:
    """Cas d'usage liés aux serveurs gérés."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        supervisor: Supervisor,
    ) -> None:
        self._session = session
        self._settings = settings
        self._supervisor = supervisor
        self._servers = ServerRepository(session)
        self._audit = AuditRepository(session)

    # ------------------------------------------------------------------ #
    #  Lecture
    # ------------------------------------------------------------------ #
    async def list_servers(self, user: User) -> list[Server]:
        """Serveurs visibles par ce compte, les siens en premier.

        Un admin ou un modérateur voit tous les serveurs : les siens d'abord, puis
        ceux des autres regroupés par propriétaire. Un user ne voit que les siens
        et ceux qu'on lui a partagés.
        """
        if sees_every_server(user.role):
            servers = await self._servers.list_all()
        else:
            servers = await self._servers.list_visible_to(user.id)
        # Tri stable : l'ordre choisi (sort_order, nom) est conservé dans chaque groupe.
        return sorted(
            servers,
            key=lambda server: (
                server.owner_id != user.id,
                server.owner.username.casefold() if server.owner_id != user.id else "",
            ),
        )

    async def get_server(self, server_id: int) -> Server:
        server = await self._servers.get(server_id)
        if server is None:
            raise NotFoundError(
                tr("Server not found."),
                cause=tr("No server has the identifier {server_id}.", server_id=server_id),
                remediation=tr("Refresh the server list."),
            )
        return server

    def detect_directory(self, raw_directory: str) -> detector.DetectionResult:
        """Analyse un dossier avant création, sans rien enregistrer."""
        return detector.detect(self._validate_directory(raw_directory, must_exist=False))

    async def capabilities(self, server: Server) -> list[str]:
        """Onglets à afficher, déduits du contenu réel du dossier."""
        return sorted(
            capability.value for capability in detect_capabilities(Path(server.directory))
        )

    # ------------------------------------------------------------------ #
    #  Création et modification
    # ------------------------------------------------------------------ #
    async def create_server(
        self,
        *,
        name: str,
        directory: str,
        launcher_key: str,
        server_type: ServerType = ServerType.UNKNOWN,
        minecraft_version: str | None = None,
        description: str | None = None,
        settings_overrides: dict[str, Any] | None = None,
        actor: User,
        ip_address: str | None = None,
    ) -> Server:
        """Enregistre un nouveau serveur et le met sous supervision."""
        clean_name = name.strip()
        if not clean_name:
            raise ValidationError(
                tr("Missing server name."),
                cause=tr("The name cannot be empty."),
                remediation=tr("Enter a name for this server."),
            )
        if await self._servers.get_by_name(clean_name, owner_id=actor.id) is not None:
            raise ConflictError(
                tr("This server name is already in use."),
                cause=tr("You already have a server named “{name}”.", name=clean_name),
                remediation=tr("Choose another name."),
            )

        resolved = self._validate_directory(directory)
        if await self._servers.get_by_directory(str(resolved)) is not None:
            raise ConflictError(
                tr("This folder is already managed."),
                cause=tr("A server already points to {path}.", path=resolved),
                remediation=tr("Choose another folder, or edit the existing server."),
            )

        launcher_registry.get(launcher_key)  # lève si la clé est inconnue

        server = Server(
            owner_id=actor.id,
            # Relation posée d'emblée : la réponse affiche le propriétaire, et un
            # chargement paresseux est impossible en asynchrone.
            owner=actor,
            name=clean_name,
            slug=await self._unique_slug(clean_name),
            description=description,
            directory=str(resolved),
            server_type=server_type,
            minecraft_version=minecraft_version,
            launcher_key=launcher_key,
            enabled=True,
        )
        server.settings = build_settings(**(settings_overrides or {}))
        self._servers.add(server)
        await self._servers.flush()

        # La configuration doit être valide avant d'être acceptée : mieux vaut
        # refuser à la création qu'échouer au premier démarrage.
        self._validate_launch(server)

        self._supervisor.register(self.build_runtime_config(server))

        self._audit.record(
            action=AuditAction.SERVER_CREATED,
            summary=tr(
                "Server “{name}” created ({launcher}).", name=server.name, launcher=launcher_key
            ),
            actor_id=actor.id,
            actor_username=actor.username,
            actor_role=actor.role.value,
            ip_address=ip_address,
            server_id=server.id,
            payload={"directory": server.directory, "launcher": launcher_key},
        )
        logger.info("server_created", server_id=server.id, server=server.name)
        self._supervisor.bus.publish(
            topics.system_topic(topics.SERVER_CREATED),
            {"server_id": server.id, "server": server.name, "actor": actor.username},
        )
        return server

    async def update_server(
        self,
        server: Server,
        *,
        changes: dict[str, Any],
        settings_changes: dict[str, Any] | None = None,
        context: AccessContext,
        actor: User,
        ip_address: str | None = None,
    ) -> Server:
        """Modifie un serveur et resynchronise son runtime.

        Chaque champ réellement modifié exige son droit : le dossier et les
        réglages de lancement sont réservés aux admins de MSM, le démarrage avec
        MSM aussi, le reste demande le droit de modifier le serveur. Un champ
        renvoyé à l'identique n'exige rien — un formulaire complet reste
        enregistrable par qui n'en modifie qu'une partie.
        """
        changes = self._actual_changes(server, changes)
        settings_changes = self._actual_settings_changes(server, settings_changes or {})
        for permission, action in self._required(changes, settings_changes):
            context.require(permission, action=action)
        if "memory_max_mb" in settings_changes:
            await HostingService(self._session, self._settings).check_memory_setting(
                server.owner, settings_changes["memory_max_mb"]
            )
        if not changes and not settings_changes:
            return server

        # Le démarrage avec MSM n'est lu qu'au lancement de MSM : le changer ne
        # touche pas au serveur en cours, et peut donc se faire à tout moment.
        if not changes and set(settings_changes) == {"autostart_on_boot"}:
            server.settings.autostart_on_boot = bool(settings_changes["autostart_on_boot"])
            await self._servers.flush()
            self._audit.record(
                action=AuditAction.SERVER_UPDATED,
                summary=tr(
                    "Server “{name}”: starts with MSM set to {value}.",
                    name=server.name,
                    value=tr("yes") if server.settings.autostart_on_boot else tr("no"),
                ),
                actor_id=actor.id,
                actor_username=actor.username,
                actor_role=actor.role.value,
                ip_address=ip_address,
                server_id=server.id,
                payload={"changes": [], "settings": ["autostart_on_boot"]},
            )
            return server

        if server.id in self._supervisor:
            runtime = self._supervisor.get(server.id)
            if runtime.state.is_running:
                raise ConflictError(
                    tr("Cannot edit while running."),
                    cause=tr(
                        "Server “{name}” is currently {state}.",
                        name=server.name,
                        state=runtime.state.value,
                    ),
                    remediation=tr("Stop the server before changing its configuration."),
                )

        if changes.get("name"):
            new_name = str(changes["name"]).strip()
            if new_name.casefold() != server.name.casefold():
                existing = await self._servers.get_by_name(new_name, owner_id=server.owner_id)
                if existing is not None and existing.id != server.id:
                    raise ConflictError(
                        tr("This server name is already in use."),
                        cause=tr("Its owner already has a server named “{name}”.", name=new_name),
                        remediation=tr("Choose another name."),
                    )
                server.slug = await self._unique_slug(new_name, exclude_id=server.id)
            server.name = new_name

        if changes.get("directory"):
            server.directory = str(self._validate_directory(str(changes["directory"])))

        for field in ("description", "minecraft_version", "enabled", "sort_order", "color"):
            if field in changes:
                setattr(server, field, changes[field])
        if "server_type" in changes and changes["server_type"] is not None:
            server.server_type = ServerType(changes["server_type"])
        if changes.get("launcher_key"):
            launcher_registry.get(str(changes["launcher_key"]))
            server.launcher_key = str(changes["launcher_key"])

        if settings_changes:
            self._apply_settings(server.settings, settings_changes)

        await self._servers.flush()
        self._validate_launch(server)
        await self.resync(server)

        self._audit.record(
            action=AuditAction.SERVER_UPDATED,
            summary=tr("Server “{name}” updated.", name=server.name),
            actor_id=actor.id,
            actor_username=actor.username,
            actor_role=actor.role.value,
            ip_address=ip_address,
            server_id=server.id,
            payload={"changes": sorted(changes), "settings": sorted(settings_changes or {})},
        )
        return server

    async def delete_server(
        self, server: Server, *, actor: User, ip_address: str | None = None
    ) -> None:
        """Retire un serveur du panel. **Ne supprime aucun fichier sur le disque.**"""
        runtime = self._supervisor.find(server.id)
        if runtime is not None and runtime.state.is_running:
            raise ConflictError(
                tr("Cannot delete while running."),
                cause=tr(
                    "Server “{name}” is currently {state}.",
                    name=server.name,
                    state=runtime.state.value,
                ),
                remediation=tr("Stop the server before removing it from the panel."),
            )

        name, server_id, directory = server.name, server.id, server.directory
        await self._supervisor.unregister(server_id)
        await self._servers.delete(server)

        self._audit.record(
            action=AuditAction.SERVER_DELETED,
            summary=tr("Server “{name}” removed from the panel (files kept).", name=name),
            actor_id=actor.id,
            actor_username=actor.username,
            actor_role=actor.role.value,
            ip_address=ip_address,
            payload={"directory": directory},
        )
        logger.info("server_deleted", server_id=server_id, server=name)
        self._supervisor.bus.publish(
            topics.system_topic(topics.SERVER_DELETED),
            {"server_id": server_id, "server": name, "actor": actor.username},
        )

    # ------------------------------------------------------------------ #
    #  Synchronisation avec le runtime
    # ------------------------------------------------------------------ #
    def build_runtime_config(self, server: Server) -> ServerRuntimeConfig:
        """Traduit un enregistrement de base en configuration de runtime."""
        settings = server.settings or build_settings()
        directory = Path(server.directory)

        return ServerRuntimeConfig(
            id=server.id,
            name=server.name,
            directory=directory,
            launcher_key=server.launcher_key,
            launch=LaunchContext(
                name=server.name,
                directory=directory,
                java_path=settings.java_path,
                jar_path=settings.jar_path,
                script_path=settings.script_path,
                custom_argv=tuple(settings.custom_argv or ()),
                jvm_args=tuple(settings.jvm_args or ()),
                extra_args=tuple(settings.extra_args or ()),
                memory_min_mb=settings.memory_min_mb,
                memory_max_mb=settings.memory_max_mb,
                env=dict(settings.env or {}),
            ),
            stop_command=settings.stop_command or "stop",
            stop_timeout_s=settings.stop_timeout_s,
            kill_timeout_s=settings.kill_timeout_s,
            start_timeout_s=settings.start_timeout_s,
            log_history_lines=settings.log_history_lines,
            stats_interval_s=self._settings.stats_interval_s,
            auto_accept_eula=settings.auto_accept_eula,
            restart_policy=RestartPolicy(
                mode=settings.auto_restart or AutoRestartMode.NEVER,
                delay_s=settings.restart_delay_s,
                max_consecutive_crashes=settings.max_consecutive_crashes,
            ),
            isolation=self._isolation_for(server, settings),
        )

    def _isolation_for(self, server: Server, settings: ServerSettings) -> SandboxSpec | None:
        """Confinement d'un serveur de compte ; ceux des administrateurs tournent sous MSM."""
        owner = server.owner
        if self._settings.isolation != "systemd" or sys.platform != "linux":
            return None
        if owner.role == Role.ADMIN:
            return None
        return SandboxSpec(
            account=owner.storage_id,
            memory_limit_mb=sandbox_memory_limit(settings.memory_max_mb),
            cpu_percent=self._settings.isolation_cpu_percent,
            socket_path=self._settings.isolation_socket,
            status_dir=self._settings.isolation_status_dir,
        )

    async def resync(self, server: Server) -> None:
        """Réenregistre le runtime après une modification de configuration."""
        await self._supervisor.unregister(server.id)
        if server.enabled:
            self._supervisor.register(self.build_runtime_config(server))

    async def register_all(self) -> int:
        """Met tous les serveurs actifs sous supervision. Appelé au démarrage."""
        registered = 0
        for server in await self._servers.list_all(only_enabled=True):
            if server.id in self._supervisor:
                continue
            try:
                self._supervisor.register(self.build_runtime_config(server))
                registered += 1
            except Exception as exc:  # pragma: no cover - configuration corrompue
                # Un serveur mal configuré ne doit pas empêcher les autres de
                # fonctionner : on l'ignore en le signalant.
                logger.error(
                    "server_registration_failed",
                    server_id=server.id,
                    server=server.name,
                    error=str(exc),
                )
        logger.info("servers_registered", count=registered)
        return registered

    async def adopt_running(self) -> int:
        """Réadopte les serveurs qui ont survécu à un redémarrage de MSM.

        Appelée une fois au démarrage, après :meth:`register_all`. Un serveur
        dont le processus est toujours vivant reprend son suivi ; les autres
        voient leur état remis à zéro, sans quoi le tableau de bord afficherait
        indéfiniment « en ligne » des serveurs éteints depuis longtemps.
        """
        adopted = 0
        for server in await self._servers.list_all(only_enabled=True):
            state = server.runtime_state
            runtime = self._supervisor.find(server.id)
            if runtime is None:
                continue

            recorded = state if state is not None and state.state.is_running else None
            if (
                recorded is not None
                and recorded.pid is not None
                and await runtime.adopt(
                    recorded.pid,
                    group_id=recorded.group_id,
                    create_time=recorded.process_create_time,
                    started_at=recorded.started_at,
                )
            ):
                adopted += 1
                logger.info(
                    "server_readopted",
                    server_id=server.id,
                    server=server.name,
                    pid=recorded.pid,
                )
                continue

            # Filet de sécurité : sans PID exploitable (MSM arrêté en plein
            # démarrage, base restaurée…), le serveur peut tourner quand même.
            # Il se retrouve par son dossier — sinon il resterait orphelin, et
            # sa prochaine relance échouerait sur le verrou du monde.
            found = await asyncio.to_thread(find_server_process, Path(server.directory))
            if found is not None and await runtime.adopt(
                found.pid,
                group_id=found.group_id,
                create_time=found.create_time,
                started_at=datetime.fromtimestamp(found.create_time, UTC),
            ):
                adopted += 1
                logger.warning(
                    "server_readopted_by_directory",
                    server_id=server.id,
                    server=server.name,
                    pid=found.pid,
                )
                continue

            if recorded is not None:
                # Le processus a disparu, ou son PID a été réattribué à un
                # programme sans rapport : l'état persistant est périmé.
                await self._servers.save_runtime_state(server.id, state=ServerState.OFFLINE)
                logger.info(
                    "server_state_reset",
                    server_id=server.id,
                    server=server.name,
                    stale_pid=state.pid,
                )

        if adopted:
            logger.info("servers_readopted", count=adopted)
        return adopted

    async def autostart(self) -> int:
        """Démarre les serveurs marqués « démarrer au démarrage de la machine ».

        Appelée après :meth:`adopt_running`, et jamais avant : un serveur qui a
        survécu à un simple redémarrage du panneau est déjà en ligne, le relancer
        lui ferait perdre son port et couperait les joueurs connectés.

        Un échec est **isolé** : trois serveurs sur quatre doivent démarrer même
        si le quatrième a un JAR manquant. La cause est journalisée et reste
        visible dans l'interface.
        """
        started = 0
        for server in await self._servers.list_all(only_enabled=True):
            settings = server.settings
            if settings is None or not settings.autostart_on_boot:
                continue
            if server.owner is not None and server.owner.is_banned:
                logger.info("server_autostart_skipped_banned", server_id=server.id)
                continue

            runtime = self._supervisor.find(server.id)
            if runtime is None or runtime.state.is_running:
                continue

            try:
                await runtime.start(actor=tr("autostart"))
            except Exception as exc:  # un serveur en panne ne bloque pas les autres
                logger.error(
                    "server_autostart_failed",
                    server_id=server.id,
                    server=server.name,
                    error=str(getattr(exc, "cause", None) or exc),
                )
                continue

            started += 1
            logger.info("server_autostarted", server_id=server.id, server=server.name)

        if started:
            logger.info("servers_autostarted", count=started)
        return started

    # ------------------------------------------------------------------ #
    #  Validation
    # ------------------------------------------------------------------ #
    def check_directory(self, raw: str, *, must_exist: bool = True) -> Path:
        """Valide un dossier de serveur (absolu, dans une racine autorisée) et le résout."""
        return self._validate_directory(raw, must_exist=must_exist)

    def _validate_directory(self, raw: str, *, must_exist: bool = True) -> Path:
        """Valide un dossier de serveur : absolu, existant, dans un périmètre autorisé."""
        value = (raw or "").strip()
        if not value:
            raise ValidationError(
                tr("Missing server folder."),
                cause=tr("No path was provided."),
                remediation=tr("Enter the folder containing the Minecraft server."),
            )

        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValidationError(
                tr("Path is not absolute."),
                cause=tr("“{path}” is a relative path.", path=value),
                remediation=tr("Enter the full path of the server folder."),
            )

        try:
            resolved = path.resolve()
        except OSError as exc:
            raise ValidationError(
                tr("Folder not accessible."),
                cause=str(exc),
                remediation=tr("Check the path and the access rights."),
            ) from exc

        if must_exist and not resolved.is_dir():
            raise ValidationError(
                tr("Folder not found."),
                cause=tr("{path} does not exist or is not a folder.", path=resolved),
                remediation=tr("Create the folder or correct the path."),
            )

        self._check_within_roots(resolved)
        return resolved

    def _check_within_roots(self, resolved: Path) -> None:
        check_within_roots(self._settings, resolved)

    def _validate_launch(self, server: Server) -> None:
        """Vérifie que la configuration permettra effectivement un démarrage."""
        config = self.build_runtime_config(server)
        launcher_registry.get(config.launcher_key).validate(config.launch)

    @staticmethod
    def _actual_changes(server: Server, changes: dict[str, Any]) -> dict[str, Any]:
        """Les champs dont la valeur diffère vraiment de celle enregistrée."""
        actual: dict[str, Any] = {}
        for key, value in changes.items():
            current = getattr(server, key, None)
            if key == "name" and value is not None:
                if str(value).strip() == server.name:
                    continue
            elif key == "directory" and value is not None:
                if str(value).strip() == server.directory:
                    continue
            elif key == "server_type" and value is not None:
                if ServerType(value) == server.server_type:
                    continue
            elif value == current:
                continue
            actual[key] = value
        return actual

    @staticmethod
    def _actual_settings_changes(server: Server, changes: dict[str, Any]) -> dict[str, Any]:
        settings = server.settings
        actual: dict[str, Any] = {}
        for key, value in changes.items():
            current = getattr(settings, key, None) if settings is not None else None
            if isinstance(current, (list, tuple)) and isinstance(value, (list, tuple)):
                if list(current) == list(value):
                    continue
            elif key == "auto_restart" and value is not None and current is not None:
                if AutoRestartMode(value) == current:
                    continue
            elif value == current:
                continue
            actual[key] = value
        return actual

    @staticmethod
    def _required(
        changes: dict[str, Any], settings_changes: dict[str, Any]
    ) -> list[tuple[Permission, str]]:
        """Droits exigés par une modification, avec l'action à nommer en cas de refus."""
        required: list[tuple[Permission, str]] = []
        ordinary = set(changes) - {"directory", "launcher_key"}
        ordinary |= set(settings_changes) - LAUNCH_SETTINGS - {"autostart_on_boot"}
        if ordinary:
            required.append((Permission.SERVER_EDIT, tr("edit this server")))
        if "directory" in changes:
            required.append((Permission.SERVER_EDIT, tr("edit this server")))
            required.append((Permission.SERVER_REGISTER, tr("change the server's folder")))
        if "launcher_key" in changes or set(settings_changes) & LAUNCH_SETTINGS:
            required.append((Permission.SERVER_EDIT, tr("edit this server")))
            required.append((Permission.SERVER_LAUNCH, tr("change how the server is launched")))
        if "autostart_on_boot" in settings_changes:
            required.append((Permission.SERVER_AUTOSTART, tr("choose whether it starts with MSM")))
        return required

    @staticmethod
    def _apply_settings(settings: ServerSettings, changes: dict[str, Any]) -> None:
        """Applique les modifications de réglages, en validant les valeurs sensibles."""
        for key, value in changes.items():
            if not hasattr(settings, key) or key in ("server_id", "server"):
                continue
            if key == "auto_restart" and value is not None:
                value = AutoRestartMode(value)
            timeouts = ("stop_timeout_s", "kill_timeout_s", "start_timeout_s")
            if key in timeouts and value is not None and float(value) <= 0:
                raise ValidationError(
                    tr("Invalid timeout."),
                    cause=tr("“{key}” must be strictly positive.", key=key),
                    remediation=tr("Enter a duration in seconds greater than zero."),
                )
            if key == "log_history_lines" and value is not None and int(value) < 100:
                raise ValidationError(
                    tr("Console history too short."),
                    cause=tr("At least 100 lines are needed for a useful diagnosis."),
                    remediation=tr("Enter at least 100 lines."),
                )
            setattr(settings, key, value)

    async def _unique_slug(self, name: str, *, exclude_id: int | None = None) -> str:
        """Slug unique, suffixé si nécessaire."""
        base = slugify(name)
        candidate = base
        suffix = 2
        while True:
            existing = await self._servers.get_by_slug(candidate)
            if existing is None or existing.id == exclude_id:
                return candidate
            candidate = f"{base}-{suffix}"
            suffix += 1
