"""Sources officielles de JAR de serveur.

Trois seulement, et **codées en dur** : Mojang, PaperMC, PurpurMC. Aucune URL ne
vient de l'utilisateur — un champ « adresse du JAR » ferait de MSM un outil de
téléchargement arbitraire tournant avec les droits du service, ce qui est
exactement ce qu'on évite.

Chaque source expose la même chose : une liste de versions, puis la résolution
d'une version en URL de téléchargement **accompagnée de son empreinte**. Sans
empreinte publiée, on refuse plutôt que d'installer un fichier non vérifié.

Forge et NeoForge sont volontairement absents : ils ne distribuent pas un JAR de
serveur mais un installateur à exécuter, qui télécharge lui-même ses dépendances.
Le lancer reviendrait à exécuter du code arbitraire à la place de l'utilisateur.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from msm.exceptions import MsmError, NotFoundError, ValidationError
from msm.logging_conf import get_logger
from msm.minecraft.types import ServerType

logger = get_logger(__name__)

REQUEST_TIMEOUT_S = 20.0

MOJANG_MANIFEST = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
PAPER_API = "https://api.papermc.io/v2/projects/paper"
PURPUR_API = "https://api.purpurmc.org/v2/purpur"

#: Hôtes dont un téléchargement peut provenir. La vérification a lieu juste avant
#: la requête : une API compromise ne pourrait pas nous faire tirer d'ailleurs.
ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "launchermeta.mojang.com",
        "piston-meta.mojang.com",
        "piston-data.mojang.com",
        "api.papermc.io",
        "api.purpurmc.org",
    }
)


class DownloadUnavailable(MsmError):
    """La source n'a pas répondu, ou pas comme prévu."""

    code = "DOWNLOAD_SOURCE_UNAVAILABLE"
    status_code = 502


@dataclass(frozen=True, slots=True)
class VersionInfo:
    """Une version proposée au téléchargement."""

    id: str
    #: `release` ou `snapshot` — l'interface met les instables en retrait.
    channel: str = "release"
    #: Version de Minecraft, quand elle diffère de l'identifiant (builds Paper).
    minecraft_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "channel": self.channel,
            "minecraft_version": self.minecraft_version or self.id,
        }


@dataclass(frozen=True, slots=True)
class DownloadTarget:
    """Où télécharger, et comment vérifier ce qui arrive."""

    url: str
    filename: str
    #: Empreinte publiée par la source ; `sha1` chez Mojang, `sha256` chez Paper.
    checksum: str
    algorithm: str
    size_bytes: int | None = None


def _check_host(url: str) -> None:
    host = httpx.URL(url).host
    if host not in ALLOWED_HOSTS:
        raise ValidationError(
            "Téléchargement refusé.",
            cause=f"L'adresse proposée pointe vers {host}, qui n'est pas une source officielle.",
            remediation="Signaler l'anomalie ; MSM ne télécharge que depuis les sources connues.",
        )


async def _get_json(client: httpx.AsyncClient, url: str) -> Any:
    _check_host(url)
    try:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DownloadUnavailable(
            "Source de téléchargement injoignable.",
            cause=f"{url} n'a pas répondu correctement : {exc}",
            remediation="Vérifier la connexion réseau de la machine, puis réessayer.",
        ) from exc


# --------------------------------------------------------------------------- #
#  Mojang (Vanilla)
# --------------------------------------------------------------------------- #
async def _vanilla_versions(client: httpx.AsyncClient) -> list[VersionInfo]:
    manifest = await _get_json(client, MOJANG_MANIFEST)
    versions = []
    for entry in manifest.get("versions", []):
        channel = "release" if entry.get("type") == "release" else "snapshot"
        versions.append(VersionInfo(id=str(entry["id"]), channel=channel))
    return versions


async def _vanilla_target(client: httpx.AsyncClient, version: str) -> DownloadTarget:
    manifest = await _get_json(client, MOJANG_MANIFEST)
    entry = next((item for item in manifest.get("versions", []) if item.get("id") == version), None)
    if entry is None:
        raise NotFoundError(
            "Version inconnue.",
            cause=f"Mojang ne publie pas de version « {version} ».",
            remediation="Choisir une version dans la liste proposée.",
        )

    detail = await _get_json(client, str(entry["url"]))
    server = (detail.get("downloads") or {}).get("server")
    if not server or not server.get("sha1"):
        raise NotFoundError(
            "Version sans serveur téléchargeable.",
            cause=f"Mojang ne publie pas de JAR de serveur pour « {version} ».",
            remediation="Choisir une version 1.2.5 ou plus récente.",
        )

    return DownloadTarget(
        url=str(server["url"]),
        filename=f"minecraft_server.{version}.jar",
        checksum=str(server["sha1"]),
        algorithm="sha1",
        size_bytes=server.get("size"),
    )


# --------------------------------------------------------------------------- #
#  PaperMC
# --------------------------------------------------------------------------- #
async def _paper_versions(client: httpx.AsyncClient) -> list[VersionInfo]:
    data = await _get_json(client, PAPER_API)
    # L'API liste de la plus ancienne à la plus récente ; l'inverse est plus utile.
    return [VersionInfo(id=str(version)) for version in reversed(data.get("versions", []))]


async def _paper_target(client: httpx.AsyncClient, version: str) -> DownloadTarget:
    builds = await _get_json(client, f"{PAPER_API}/versions/{version}/builds")
    stable = [
        build
        for build in builds.get("builds", [])
        if build.get("channel") in (None, "default", "stable")
    ]
    chosen = (stable or builds.get("builds") or [None])[-1]
    if not chosen:
        raise NotFoundError(
            "Aucun build disponible.",
            cause=f"PaperMC ne publie aucun build pour « {version} ».",
            remediation="Choisir une autre version.",
        )

    application = (chosen.get("downloads") or {}).get("application") or {}
    name = application.get("name")
    if not name or not application.get("sha256"):
        raise DownloadUnavailable(
            "Build sans fichier téléchargeable.",
            cause="PaperMC n'a pas publié d'empreinte pour ce build.",
            remediation="Réessayer plus tard, ou choisir une autre version.",
        )

    number = chosen["build"]
    return DownloadTarget(
        url=f"{PAPER_API}/versions/{version}/builds/{number}/downloads/{name}",
        filename=str(name),
        checksum=str(application["sha256"]),
        algorithm="sha256",
    )


# --------------------------------------------------------------------------- #
#  PurpurMC
# --------------------------------------------------------------------------- #
async def _purpur_versions(client: httpx.AsyncClient) -> list[VersionInfo]:
    data = await _get_json(client, PURPUR_API)
    return [VersionInfo(id=str(version)) for version in reversed(data.get("versions", []))]


async def _purpur_target(client: httpx.AsyncClient, version: str) -> DownloadTarget:
    latest = await _get_json(client, f"{PURPUR_API}/{version}/latest")
    checksum = (latest.get("md5") or "").strip()
    build = latest.get("build")
    if not build or not checksum:
        raise DownloadUnavailable(
            "Build sans empreinte publiée.",
            cause="PurpurMC n'a pas publié d'empreinte pour ce build.",
            remediation="Réessayer plus tard, ou choisir une autre version.",
        )
    return DownloadTarget(
        url=f"{PURPUR_API}/{version}/{build}/download",
        filename=f"purpur-{version}-{build}.jar",
        checksum=checksum,
        # MD5 ne vaut rien contre un adversaire, mais c'est ce que publie Purpur :
        # il détecte un téléchargement tronqué, ce qui est déjà son rôle ici.
        algorithm="md5",
    )


#: Sources disponibles, exposées telles quelles à l'interface.
SOURCES: dict[str, dict[str, Any]] = {
    "vanilla": {
        "label": "Vanilla (Mojang)",
        "server_type": ServerType.VANILLA,
        "versions": _vanilla_versions,
        "target": _vanilla_target,
    },
    "paper": {
        "label": "Paper",
        "server_type": ServerType.PAPER,
        "versions": _paper_versions,
        "target": _paper_target,
    },
    "purpur": {
        "label": "Purpur",
        "server_type": ServerType.PURPUR,
        "versions": _purpur_versions,
        "target": _purpur_target,
    },
}


def _source(key: str) -> dict[str, Any]:
    source = SOURCES.get(key)
    if source is None:
        raise ValidationError(
            "Source inconnue.",
            cause=f"« {key} » n'est pas une source de téléchargement reconnue.",
            remediation=f"Choisir parmi : {', '.join(SOURCES)}.",
        )
    return source


async def list_versions(
    source: str, *, client: httpx.AsyncClient | None = None
) -> list[VersionInfo]:
    """Versions proposées par une source."""
    handler = _source(source)["versions"]
    owned = client is None
    http = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S, follow_redirects=True)
    try:
        return await handler(http)
    finally:
        if owned:
            await http.aclose()


async def resolve(
    source: str, version: str, *, client: httpx.AsyncClient | None = None
) -> DownloadTarget:
    """Résout une version en URL vérifiable."""
    handler = _source(source)["target"]
    owned = client is None
    http = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S, follow_redirects=True)
    try:
        target = await handler(http, version)
    finally:
        if owned:
            await http.aclose()
    _check_host(target.url)
    return target
