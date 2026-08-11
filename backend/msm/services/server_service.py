"""Gestion des serveurs : création, configuration, mise en service du runtime.

Ce service est la charnière entre la base de données (ce que l'administrateur a
configuré) et le runtime (ce qui tourne réellement). Il traduit un
:class:`Server` en :class:`ServerRuntimeConfig` et tient le superviseur à jour.

Le runtime, lui, ignore complètement l'existence de SQLAlchemy — c'est ce qui
permet de le tester avec un faux serveur, sans base.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from msm.config import Settings
from msm.core.restart_policy import AutoRestartMode, RestartPolicy
from msm.core.states import ServerState
from msm.db.models.audit import AuditAction
from msm.db.models.server import Server, ServerSettings
from msm.db.models.user import User
from msm.db.repositories import AuditRepository, ServerRepository, build_settings
from msm.exceptions import ConflictError, NotFoundError, ValidationError
from msm.launchers import LaunchContext
from msm.launchers import registry as launcher_registry
from msm.logging_conf import get_logger
from msm.minecraft import detector
from msm.minecraft.capabilities import detect_capabilities
from msm.minecraft.types import ServerType
from msm.runtime.server_runtime import ServerRuntimeConfig
from msm.runtime.supervisor import Supervisor

logger = get_logger(__name__)

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Identifiant lisible dans une URL, dérivé du nom du serveur."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    slug = _SLUG_STRIP_RE.sub("-", ascii_only).strip("-")
    return slug or "serveur"


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
    async def list_servers(self) -> list[Server]:
        return await self._servers.list_all()

    async def get_server(self, server_id: int) -> Server:
        server = await self._servers.get(server_id)
        if server is None:
            raise NotFoundError(
                "Serveur introuvable.",
                cause=f"Aucun serveur ne porte l'identifiant {server_id}.",
                remediation="Rafraîchir la liste des serveurs.",
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
                "Nom de serveur manquant.",
                cause="Le nom ne peut pas être vide.",
                remediation="Saisir un nom pour ce serveur.",
            )
        if await self._servers.get_by_name(clean_name) is not None:
            raise ConflictError(
                "Ce nom de serveur est déjà utilisé.",
                cause=f"Un serveur nommé « {clean_name} » existe déjà.",
                remediation="Choisir un autre nom.",
            )

        resolved = self._validate_directory(directory)
        if await self._servers.get_by_directory(str(resolved)) is not None:
            raise ConflictError(
                "Ce dossier est déjà géré.",
                cause=f"Un serveur pointe déjà vers {resolved}.",
                remediation="Choisir un autre dossier, ou modifier le serveur existant.",
            )

        launcher_registry.get(launcher_key)  # lève si la clé est inconnue

        server = Server(
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
            summary=f"Création du serveur « {server.name} » ({launcher_key}).",
            actor_id=actor.id,
            actor_username=actor.username,
            actor_role=actor.role.value,
            ip_address=ip_address,
            server_id=server.id,
            payload={"directory": server.directory, "launcher": launcher_key},
        )
        logger.info("server_created", server_id=server.id, server=server.name)
        return server

    async def update_server(
        self,
        server: Server,
        *,
        changes: dict[str, Any],
        settings_changes: dict[str, Any] | None = None,
        actor: User,
        ip_address: str | None = None,
    ) -> Server:
        """Modifie un serveur et resynchronise son runtime."""
        if server.id in self._supervisor:
            runtime = self._supervisor.get(server.id)
            if runtime.state.is_running:
                raise ConflictError(
                    "Modification impossible pendant l'exécution.",
                    cause=f"Le serveur « {server.name} » est actuellement {runtime.state.value}.",
                    remediation="Arrêter le serveur avant de modifier sa configuration.",
                )

        if changes.get("name"):
            new_name = str(changes["name"]).strip()
            if new_name.casefold() != server.name.casefold():
                existing = await self._servers.get_by_name(new_name)
                if existing is not None and existing.id != server.id:
                    raise ConflictError(
                        "Ce nom de serveur est déjà utilisé.",
                        cause=f"Un serveur nommé « {new_name} » existe déjà.",
                        remediation="Choisir un autre nom.",
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
            summary=f"Modification du serveur « {server.name} ».",
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
                "Suppression impossible pendant l'exécution.",
                cause=f"Le serveur « {server.name} » est actuellement {runtime.state.value}.",
                remediation="Arrêter le serveur avant de le retirer du panel.",
            )

        name, server_id, directory = server.name, server.id, server.directory
        await self._supervisor.unregister(server_id)
        await self._servers.delete(server)

        self._audit.record(
            action=AuditAction.SERVER_DELETED,
            summary=f"Suppression du serveur « {name} » du panel (fichiers conservés).",
            actor_id=actor.id,
            actor_username=actor.username,
            actor_role=actor.role.value,
            ip_address=ip_address,
            payload={"directory": directory},
        )
        logger.info("server_deleted", server_id=server_id, server=name)

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
            if runtime is None or state is None:
                continue

            if state.pid is None or not state.state.is_running:
                continue

            if await runtime.adopt(
                state.pid,
                group_id=state.group_id,
                create_time=state.process_create_time,
                started_at=state.started_at,
            ):
                adopted += 1
                logger.info(
                    "server_readopted",
                    server_id=server.id,
                    server=server.name,
                    pid=state.pid,
                )
            else:
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

            runtime = self._supervisor.find(server.id)
            if runtime is None or runtime.state.is_running:
                continue

            try:
                await runtime.start(actor="démarrage automatique")
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
    def _validate_directory(self, raw: str, *, must_exist: bool = True) -> Path:
        """Valide un dossier de serveur : absolu, existant, dans un périmètre autorisé."""
        value = (raw or "").strip()
        if not value:
            raise ValidationError(
                "Dossier du serveur manquant.",
                cause="Aucun chemin n'a été fourni.",
                remediation="Indiquer le dossier contenant le serveur Minecraft.",
            )

        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValidationError(
                "Chemin non absolu.",
                cause=f"« {value} » est un chemin relatif.",
                remediation="Indiquer le chemin complet du dossier du serveur.",
            )

        try:
            resolved = path.resolve()
        except OSError as exc:
            raise ValidationError(
                "Dossier inaccessible.",
                cause=str(exc),
                remediation="Vérifier le chemin et les droits d'accès.",
            ) from exc

        if must_exist and not resolved.is_dir():
            raise ValidationError(
                "Dossier introuvable.",
                cause=f"{resolved} n'existe pas ou n'est pas un dossier.",
                remediation="Créer le dossier ou corriger le chemin saisi.",
            )

        self._check_within_roots(resolved)
        return resolved

    def _check_within_roots(self, resolved: Path) -> None:
        """Vérifie que le dossier est sous une racine autorisée, si la liste existe.

        Sans cette restriction, un administrateur pourrait désigner ``/etc`` comme
        dossier de serveur et l'exposer à l'éditeur de configurations.
        """
        roots = self._settings.server_roots
        if not roots:
            return
        for root in roots:
            try:
                root_resolved = Path(root).expanduser().resolve()
            except OSError:  # pragma: no cover - racine mal configurée
                continue
            if resolved == root_resolved or root_resolved in resolved.parents:
                return

        allowed = ", ".join(str(Path(root)) for root in roots)
        raise ValidationError(
            "Dossier hors des emplacements autorisés.",
            cause=f"{resolved} n'est pas situé sous une racine autorisée.",
            remediation=f"Choisir un dossier situé sous : {allowed}.",
        )

    def _validate_launch(self, server: Server) -> None:
        """Vérifie que la configuration permettra effectivement un démarrage."""
        config = self.build_runtime_config(server)
        launcher_registry.get(config.launcher_key).validate(config.launch)

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
                    "Délai invalide.",
                    cause=f"« {key} » doit être strictement positif.",
                    remediation="Saisir une durée en secondes supérieure à zéro.",
                )
            if key == "log_history_lines" and value is not None and int(value) < 100:
                raise ValidationError(
                    "Historique de console trop court.",
                    cause="Un minimum de 100 lignes est nécessaire pour un diagnostic utile.",
                    remediation="Saisir au moins 100 lignes.",
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
