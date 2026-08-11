"""Installation d'un JAR de serveur depuis une source officielle.

Quatre garde-fous, dans cet ordre :

1. **le serveur doit être arrêté.** Remplacer le JAR sous une JVM qui tourne
   produit des erreurs incompréhensibles au prochain chargement de classe ;
2. **le fichier est écrit à côté puis renommé.** Une coupure réseau laisse un
   `.part`, jamais un JAR tronqué qui refuserait de démarrer sans dire pourquoi ;
3. **l'empreinte est vérifiée avant le renommage.** Un téléchargement altéré est
   supprimé, pas installé ;
4. **le chemin est confiné au dossier du serveur**, comme tout ce que MSM écrit.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from msm.core.permissions import Permission
from msm.db.models.audit import AuditAction
from msm.db.models.server import Server
from msm.db.repositories import AuditRepository
from msm.downloads.sources import (
    REQUEST_TIMEOUT_S,
    SOURCES,
    DownloadTarget,
    list_versions,
    resolve,
)
from msm.exceptions import ServerAlreadyRunning, ValidationError
from msm.logging_conf import get_logger
from msm.runtime.supervisor import Supervisor
from msm.security.rbac import AccessContext
from msm.security.safe_path import resolve_within

logger = get_logger(__name__)

#: Au-delà, ce n'est plus un JAR de serveur ; refuser évite de remplir le disque
#: si une source renvoyait n'importe quoi.
MAX_JAR_BYTES = 512 * 1024 * 1024
_CHUNK = 512 * 1024


class DownloadService:
    """Cas d'usage du téléchargement de versions."""

    def __init__(self, session: AsyncSession, supervisor: Supervisor) -> None:
        self._session = session
        self._supervisor = supervisor
        self._audit = AuditRepository(session)

    @staticmethod
    def sources() -> list[dict[str, str]]:
        return [{"key": key, "label": source["label"]} for key, source in SOURCES.items()]

    async def versions(self, source: str, *, context: AccessContext) -> list[dict[str, Any]]:
        context.require(Permission.SERVER_EDIT, action="consulter les versions disponibles")
        return [version.to_dict() for version in await list_versions(source)]

    async def install(
        self,
        server: Server,
        *,
        source: str,
        version: str,
        context: AccessContext,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Télécharge un JAR dans le dossier du serveur et le sélectionne."""
        context.require(Permission.SERVER_EDIT, action="installer une version")

        runtime = self._supervisor.find(server.id)
        if runtime is not None and runtime.state.is_running:
            raise ServerAlreadyRunning(
                "Le serveur doit être arrêté pour changer de version.",
                cause="Remplacer le JAR d'un serveur en cours d'exécution le ferait échouer.",
                remediation="Arrêter le serveur, puis relancer l'installation.",
            )

        directory = Path(server.directory)
        if not directory.is_dir():
            raise ValidationError(
                "Dossier du serveur introuvable.",
                cause=f"{directory} n'existe pas ou n'est pas lisible.",
                remediation="Vérifier le chemin du serveur dans ses réglages.",
            )

        target = await resolve(source, version)
        destination = resolve_within(directory, target.filename)
        await _download(target, destination)

        settings = server.settings
        previous = settings.jar_path if settings else None
        if settings is not None:
            # Le serveur pointera sur le nouveau JAR au prochain démarrage : sans
            # cela, l'utilisateur téléchargerait une version qui ne sert à rien.
            settings.jar_path = str(destination)
        server.minecraft_version = version
        if (declared := SOURCES[source]["server_type"]) is not None:
            server.server_type = declared

        self._audit.record(
            action=AuditAction.SERVER_UPDATED,
            summary=(
                f"Installation de {SOURCES[source]['label']} {version} sur « {server.name} »."
            ),
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            server_id=server.id,
            target_type="download",
            payload={"source": source, "version": version, "file": destination.name},
        )
        logger.info(
            "version_installed",
            server_id=server.id,
            source=source,
            version=version,
            file=destination.name,
        )
        return {
            "file": destination.name,
            "path": str(destination),
            "previous_jar": previous,
            "size_bytes": destination.stat().st_size,
            "version": version,
        }


async def _download(
    target: DownloadTarget, destination: Path, *, client: httpx.AsyncClient | None = None
) -> None:
    """Télécharge en flux, vérifie l'empreinte, puis publie le fichier."""
    partial = destination.with_name(destination.name + ".part")
    digest = hashlib.new(target.algorithm)
    written = 0
    http = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S, follow_redirects=True)

    try:
        async with http.stream("GET", target.url) as response:
            response.raise_for_status()
            # Le fichier est écrit par morceaux : un JAR de 100 Mo n'a aucune
            # raison de transiter par la mémoire du processus.
            with partial.open("wb") as handle:
                async for chunk in response.aiter_bytes(_CHUNK):
                    written += len(chunk)
                    if written > MAX_JAR_BYTES:
                        raise ValidationError(
                            "Fichier trop volumineux.",
                            cause=(
                                f"Le téléchargement dépasse {MAX_JAR_BYTES // (1024 * 1024)} Mo."
                            ),
                            remediation="Signaler l'anomalie : ce n'est pas un JAR de serveur.",
                        )
                    digest.update(chunk)
                    await asyncio.to_thread(handle.write, chunk)
    except httpx.HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise ValidationError(
            "Téléchargement interrompu.",
            cause=f"La source n'a pas pu être lue jusqu'au bout : {exc}",
            remediation="Vérifier la connexion réseau de la machine, puis réessayer.",
        ) from exc
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        # Le client fourni par l'appelant lui appartient : il le referme lui-même.
        if client is None:
            await http.aclose()

    if digest.hexdigest().lower() != target.checksum.lower():
        partial.unlink(missing_ok=True)
        raise ValidationError(
            "Fichier téléchargé invalide.",
            cause=("L'empreinte du fichier ne correspond pas à celle publiée par la source."),
            remediation="Réessayer ; si l'erreur persiste, changer de version ou de source.",
        )

    partial.replace(destination)
