"""Sources officielles de serveurs Minecraft.

Toutes **codées en dur** : Mojang, PaperMC, PurpurMC, FabricMC, NeoForged et
MohistMC. Aucune URL ne vient de l'utilisateur — un champ « adresse du JAR »
ferait de MSM un outil de téléchargement arbitraire tournant avec les droits du
service, ce qui est exactement ce qu'on évite.

Chaque source expose la même chose : une liste de versions de Minecraft, parfois
une liste de builds pour une version (loader Fabric, version NeoForge, build
Mohist), puis la résolution en URL de téléchargement **accompagnée de son
empreinte** quand la source en publie une. Seul Fabric n'en publie pas : son
JAR de lancement vient alors de son hôte officiel, en HTTPS, sans autre garantie.

Deux sortes de fichiers :

* ``jar`` — un JAR de serveur, lancé tel quel ;
* ``installer`` — l'installeur officiel de NeoForge, que la création d'un
  serveur exécute une fois pour produire ``run.sh`` et les bibliothèques.
  L'installeur de version d'un serveur existant ne le propose pas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from msm.exceptions import MsmError, NotFoundError, ValidationError
from msm.i18n import tr
from msm.logging_conf import get_logger
from msm.minecraft.types import ServerType

logger = get_logger(__name__)

REQUEST_TIMEOUT_S = 20.0

#: PaperMC exige un `User-Agent` qui identifie le client et un moyen de contact ;
#: les autres sources l'apprécient autant.
USER_AGENT = "MinecraftServerManager (+https://github.com/R3coNYT/Minecraft_Server_Manager)"


def new_client() -> httpx.AsyncClient:
    """Client HTTP de MSM vers les sources officielles."""
    return httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT_S,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


MOJANG_MANIFEST = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
#: API « Fill » de PaperMC ; l'ancienne API v2 a été arrêtée (410 « sunset »).
PAPER_API = "https://fill.papermc.io/v3/projects/paper"
PURPUR_API = "https://api.purpurmc.org/v2/purpur"
FABRIC_META = "https://meta.fabricmc.net/v2/versions"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge"
NEOFORGE_VERSIONS = "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"
MOHIST_API = "https://api.mohistmc.com/project"

#: Hôtes dont un téléchargement peut provenir. La vérification a lieu juste avant
#: la requête : une API compromise ne pourrait pas nous faire tirer d'ailleurs.
ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "launchermeta.mojang.com",
        "piston-meta.mojang.com",
        "piston-data.mojang.com",
        "fill.papermc.io",
        "fill-data.papermc.io",
        "api.purpurmc.org",
        "meta.fabricmc.net",
        "maven.neoforged.net",
        "api.mohistmc.com",
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
class BuildInfo:
    """Un build proposé pour une version : loader Fabric, version NeoForge…"""

    id: str
    label: str
    #: `release` ou `beta` — l'interface propose le plus récent stable.
    channel: str = "release"

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "channel": self.channel}


@dataclass(frozen=True, slots=True)
class DownloadTarget:
    """Où télécharger, et comment vérifier ce qui arrive."""

    url: str
    filename: str
    #: Empreinte publiée par la source ; `sha1` chez Mojang, `sha256` chez Paper.
    #: `None` seulement pour Fabric, qui n'en publie pas.
    checksum: str | None
    algorithm: str | None
    size_bytes: int | None = None
    #: `jar` : lancé tel quel ; `installer` : exécuté une fois pour installer.
    kind: str = "jar"


def _check_host(url: str) -> None:
    host = httpx.URL(url).host
    if host not in ALLOWED_HOSTS:
        raise ValidationError(
            tr("Download refused."),
            cause=tr(
                "The address given points to {host}, which is not an official source.", host=host
            ),
            remediation=tr("Report the anomaly; MSM only downloads from known sources."),
        )


async def _get_json(client: httpx.AsyncClient, url: str) -> Any:
    _check_host(url)
    try:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DownloadUnavailable(
            tr("Download source unreachable."),
            cause=tr("{url} did not answer correctly: {error}", url=url, error=exc),
            remediation=tr("Check the machine's network connection, then try again."),
        ) from exc


async def _get_text(client: httpx.AsyncClient, url: str) -> str:
    _check_host(url)
    try:
        response = await client.get(url)
        response.raise_for_status()
        return response.text
    except httpx.HTTPError as exc:
        raise DownloadUnavailable(
            tr("Download source unreachable."),
            cause=tr("{url} did not answer correctly: {error}", url=url, error=exc),
            remediation=tr("Check the machine's network connection, then try again."),
        ) from exc


def version_key(value: str) -> tuple[int, ...]:
    """Clé de tri numérique : « 1.21.10 » après « 1.21.9 »."""
    return tuple(int(part) for part in re.findall(r"\d+", value))


def _unknown_version(source: str, version: str) -> NotFoundError:
    return NotFoundError(
        tr("Unknown version."),
        cause=tr("{source} publishes nothing for “{version}”.", source=source, version=version),
        remediation=tr("Choose a version from the list offered."),
    )


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


async def _vanilla_target(
    client: httpx.AsyncClient, version: str, build: str | None = None
) -> DownloadTarget:
    manifest = await _get_json(client, MOJANG_MANIFEST)
    entry = next((item for item in manifest.get("versions", []) if item.get("id") == version), None)
    if entry is None:
        raise NotFoundError(
            tr("Unknown version."),
            cause=tr("Mojang publishes no version “{version}”.", version=version),
            remediation=tr("Choose a version from the list offered."),
        )

    detail = await _get_json(client, str(entry["url"]))
    server = (detail.get("downloads") or {}).get("server")
    if not server or not server.get("sha1"):
        raise NotFoundError(
            tr("Version without a downloadable server."),
            cause=tr("Mojang publishes no server JAR for “{version}”.", version=version),
            remediation=tr("Choose version 1.2.5 or newer."),
        )

    return DownloadTarget(
        url=str(server["url"]),
        filename=f"minecraft_server.{version}.jar",
        checksum=str(server["sha1"]),
        algorithm="sha1",
        size_bytes=server.get("size"),
    )


# --------------------------------------------------------------------------- #
#  PaperMC (API « Fill » v3)
# --------------------------------------------------------------------------- #
async def _paper_versions(client: httpx.AsyncClient) -> list[VersionInfo]:
    data = await _get_json(client, PAPER_API)
    # Versions groupées par famille (« 1.21 » → [« 1.21.11 », …]), plus récentes
    # d'abord ; les release candidates et pré-versions restent en retrait.
    versions: list[VersionInfo] = []
    for group in (data.get("versions") or {}).values():
        for value in group:
            unstable = "-rc" in value or "-pre" in value
            versions.append(
                VersionInfo(id=str(value), channel="snapshot" if unstable else "release")
            )
    return versions


async def _paper_target(
    client: httpx.AsyncClient, version: str, build: str | None = None
) -> DownloadTarget:
    builds = await _get_json(client, f"{PAPER_API}/versions/{version}/builds")
    if not isinstance(builds, list) or not builds:
        raise NotFoundError(
            tr("No build available."),
            cause=tr("PaperMC publishes no build for “{version}”.", version=version),
            remediation=tr("Choose another version."),
        )
    # La liste arrive du plus récent au plus ancien : le premier stable l'emporte.
    chosen = next((item for item in builds if item.get("channel") == "STABLE"), builds[0])

    download = (chosen.get("downloads") or {}).get("server:default") or {}
    checksum = (download.get("checksums") or {}).get("sha256")
    if not download.get("url") or not download.get("name") or not checksum:
        raise DownloadUnavailable(
            tr("Build without a downloadable file."),
            cause=tr("PaperMC has published no checksum for this build."),
            remediation=tr("Try again later, or choose another version."),
        )
    return DownloadTarget(
        url=str(download["url"]),
        filename=str(download["name"]),
        checksum=str(checksum),
        algorithm="sha256",
        size_bytes=download.get("size"),
    )


# --------------------------------------------------------------------------- #
#  PurpurMC
# --------------------------------------------------------------------------- #
async def _purpur_versions(client: httpx.AsyncClient) -> list[VersionInfo]:
    data = await _get_json(client, PURPUR_API)
    return [VersionInfo(id=str(version)) for version in reversed(data.get("versions", []))]


async def _purpur_target(
    client: httpx.AsyncClient, version: str, build: str | None = None
) -> DownloadTarget:
    latest = await _get_json(client, f"{PURPUR_API}/{version}/latest")
    checksum = (latest.get("md5") or "").strip()
    build = latest.get("build")
    if not build or not checksum:
        raise DownloadUnavailable(
            tr("Build without a published checksum."),
            cause=tr("PurpurMC has published no checksum for this build."),
            remediation=tr("Try again later, or choose another version."),
        )
    return DownloadTarget(
        url=f"{PURPUR_API}/{version}/{build}/download",
        filename=f"purpur-{version}-{build}.jar",
        checksum=checksum,
        # MD5 ne vaut rien contre un adversaire, mais c'est ce que publie Purpur :
        # il détecte un téléchargement tronqué, ce qui est déjà son rôle ici.
        algorithm="md5",
    )


# --------------------------------------------------------------------------- #
#  FabricMC
# --------------------------------------------------------------------------- #
async def _fabric_versions(client: httpx.AsyncClient) -> list[VersionInfo]:
    data = await _get_json(client, f"{FABRIC_META}/game")
    return [
        VersionInfo(
            id=str(entry["version"]), channel="release" if entry.get("stable") else "snapshot"
        )
        for entry in data
        if entry.get("version")
    ]


async def _fabric_builds(client: httpx.AsyncClient, version: str) -> list[BuildInfo]:
    # Loaders compatibles avec *cette* version de Minecraft.
    data = await _get_json(client, f"{FABRIC_META}/loader/{version}")
    builds = []
    for entry in data:
        loader = entry.get("loader") or {}
        if loader.get("version"):
            builds.append(
                BuildInfo(
                    id=str(loader["version"]),
                    label=tr("Loader {version}", version=loader["version"]),
                    channel="release" if loader.get("stable") else "beta",
                )
            )
    return builds


async def _fabric_target(
    client: httpx.AsyncClient, version: str, build: str | None = None
) -> DownloadTarget:
    builds = await _fabric_builds(client, version)
    if not builds:
        raise _unknown_version("Fabric", version)
    loader = build or next((item.id for item in builds if item.channel == "release"), builds[0].id)
    if loader not in {item.id for item in builds}:
        raise _unknown_version("Fabric", f"{version} / {loader}")

    installers = await _get_json(client, f"{FABRIC_META}/installer")
    installer = next(
        (str(item["version"]) for item in installers if item.get("stable")),
        str(installers[0]["version"]) if installers else None,
    )
    if installer is None:
        raise DownloadUnavailable(
            tr("Build without a downloadable file."),
            cause=tr("FabricMC publishes no installer version."),
            remediation=tr("Try again later, or choose another version."),
        )
    return DownloadTarget(
        url=f"{FABRIC_META}/loader/{version}/{loader}/{installer}/server/jar",
        filename=f"fabric-server-mc.{version}-loader.{loader}-launcher.{installer}.jar",
        checksum=None,
        algorithm=None,
    )


# --------------------------------------------------------------------------- #
#  NeoForge
# --------------------------------------------------------------------------- #
def neoforge_minecraft_version(neoforge: str) -> str | None:
    """Version de Minecraft visée par une version de NeoForge.

    Deux numérotations coexistent :

    * ``21.1.77`` → MC 1.21.1, ``21.0.3`` → MC 1.21, ``20.4.80`` → MC 1.20.4 ;
    * ``26.3.0.8`` → MC 26.3, ``26.1.1.2`` → MC 26.1.1 (Minecraft numérote
      désormais par année).

    `None` pour ce qui n'en suit aucune (versions expérimentales en ``0.``).
    """
    parts = neoforge.split("-", 1)[0].split(".")
    if len(parts) < 3 or not all(part.isdigit() for part in parts):
        return None
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    if major == 0:
        return None
    if major >= 26 and len(parts) >= 4:
        return f"{major}.{minor}" + (f".{patch}" if patch else "")
    return f"1.{major}" + (f".{minor}" if minor else "")


async def _neoforge_catalogue(client: httpx.AsyncClient) -> dict[str, list[str]]:
    """Versions de NeoForge regroupées par version de Minecraft, plus récentes d'abord."""
    data = await _get_json(client, NEOFORGE_VERSIONS)
    grouped: dict[str, list[str]] = {}
    for value in data.get("versions", []):
        minecraft = neoforge_minecraft_version(str(value))
        if minecraft:
            grouped.setdefault(minecraft, []).append(str(value))
    for values in grouped.values():
        values.sort(key=version_key, reverse=True)
    return grouped


def _neoforge_channel(value: str) -> str:
    return "beta" if "-" in value else "release"


async def _neoforge_versions(client: httpx.AsyncClient) -> list[VersionInfo]:
    catalogue = await _neoforge_catalogue(client)
    return [
        VersionInfo(
            id=minecraft,
            # Une version de Minecraft sans aucun NeoForge stable reste en retrait.
            channel="release"
            if any(_neoforge_channel(value) == "release" for value in values)
            else "snapshot",
        )
        for minecraft, values in sorted(
            catalogue.items(), key=lambda item: version_key(item[0]), reverse=True
        )
    ]


async def _neoforge_builds(client: httpx.AsyncClient, version: str) -> list[BuildInfo]:
    catalogue = await _neoforge_catalogue(client)
    return [
        BuildInfo(id=value, label=f"NeoForge {value}", channel=_neoforge_channel(value))
        for value in catalogue.get(version, [])
    ]


async def _neoforge_target(
    client: httpx.AsyncClient, version: str, build: str | None = None
) -> DownloadTarget:
    builds = await _neoforge_builds(client, version)
    if not builds:
        raise _unknown_version("NeoForge", version)
    chosen = build or next((item.id for item in builds if item.channel == "release"), builds[0].id)
    if chosen not in {item.id for item in builds}:
        raise _unknown_version("NeoForge", f"{version} / {chosen}")

    url = f"{NEOFORGE_MAVEN}/{chosen}/neoforge-{chosen}-installer.jar"
    published = (await _get_text(client, f"{url}.sha256")).split()
    if not published or not re.fullmatch(r"[0-9a-fA-F]{64}", published[0]):
        raise DownloadUnavailable(
            tr("Build without a published checksum."),
            cause=tr("NeoForged has published no checksum for this build."),
            remediation=tr("Try again later, or choose another version."),
        )
    return DownloadTarget(
        url=url,
        filename=f"neoforge-{chosen}-installer.jar",
        checksum=published[0],
        algorithm="sha256",
        kind="installer",
    )


# --------------------------------------------------------------------------- #
#  MohistMC — Mohist (Forge) et Youer (NeoForge)
# --------------------------------------------------------------------------- #
def _mohist_source(project: str, label: str) -> dict[str, Any]:
    async def versions(client: httpx.AsyncClient) -> list[VersionInfo]:
        data = await _get_json(client, f"{MOHIST_API}/{project}/versions")
        names = [str(entry["name"]) for entry in data if entry.get("name")]
        return [VersionInfo(id=name) for name in sorted(names, key=version_key, reverse=True)]

    async def raw_builds(client: httpx.AsyncClient, version: str) -> list[dict[str, Any]]:
        data = await _get_json(client, f"{MOHIST_API}/{project}/{version}/builds")
        entries = [entry for entry in data if isinstance(entry, dict) and entry.get("id")]
        return sorted(entries, key=lambda entry: int(entry["id"]), reverse=True)

    async def builds(client: httpx.AsyncClient, version: str) -> list[BuildInfo]:
        return [
            BuildInfo(
                id=str(entry["id"]),
                label=f"#{entry['id']} · {str(entry.get('build_date') or '')[:10]}".rstrip(" ·"),
            )
            for entry in await raw_builds(client, version)
        ]

    async def target(
        client: httpx.AsyncClient, version: str, build: str | None = None
    ) -> DownloadTarget:
        entries = await raw_builds(client, version)
        if not entries:
            raise _unknown_version(label, version)
        chosen = (
            entries[0]
            if build is None
            else next((entry for entry in entries if str(entry["id"]) == build), None)
        )
        if chosen is None:
            raise _unknown_version(label, f"{version} / {build}")
        checksum = str(chosen.get("file_sha256") or "")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
            raise DownloadUnavailable(
                tr("Build without a published checksum."),
                cause=tr("MohistMC has published no checksum for this build."),
                remediation=tr("Try again later, or choose another version."),
            )
        number = chosen["id"]
        return DownloadTarget(
            url=f"{MOHIST_API}/{project}/{version}/builds/{number}/download",
            filename=f"{project}-{version}-{number}-server.jar",
            checksum=checksum,
            algorithm="sha256",
        )

    return {"versions": versions, "builds": builds, "target": target}


#: Sources disponibles, exposées telles quelles à l'interface.
SOURCES: dict[str, dict[str, Any]] = {
    "vanilla": {
        "label": "Vanilla (Mojang)",
        "server_type": ServerType.VANILLA,
        "kind": "jar",
        "versions": _vanilla_versions,
        "builds": None,
        "target": _vanilla_target,
    },
    "paper": {
        "label": "Paper",
        "server_type": ServerType.PAPER,
        "kind": "jar",
        "versions": _paper_versions,
        "builds": None,
        "target": _paper_target,
    },
    "purpur": {
        "label": "Purpur",
        "server_type": ServerType.PURPUR,
        "kind": "jar",
        "versions": _purpur_versions,
        "builds": None,
        "target": _purpur_target,
    },
    "fabric": {
        "label": "Fabric",
        "server_type": ServerType.FABRIC,
        "kind": "jar",
        "versions": _fabric_versions,
        "builds": _fabric_builds,
        "target": _fabric_target,
    },
    "neoforge": {
        "label": "NeoForge",
        "server_type": ServerType.NEOFORGE,
        "kind": "installer",
        "versions": _neoforge_versions,
        "builds": _neoforge_builds,
        "target": _neoforge_target,
    },
    "mohist": {
        "label": "Mohist (Forge)",
        "server_type": ServerType.MOHIST,
        "kind": "jar",
        **_mohist_source("mohist", "Mohist"),
    },
    "youer": {
        "label": "Youer (NeoForge)",
        "server_type": ServerType.YOUER,
        "kind": "jar",
        **_mohist_source("youer", "Youer"),
    },
}


def _source(key: str) -> dict[str, Any]:
    source = SOURCES.get(key)
    if source is None:
        raise ValidationError(
            tr("Unknown source."),
            cause=tr("“{key}” is not a recognised download source.", key=key),
            remediation=tr("Choose one of: {choices}.", choices=", ".join(SOURCES)),
        )
    return source


async def list_versions(
    source: str, *, client: httpx.AsyncClient | None = None
) -> list[VersionInfo]:
    """Versions proposées par une source."""
    handler = _source(source)["versions"]
    owned = client is None
    http = client or new_client()
    try:
        return await handler(http)
    finally:
        if owned:
            await http.aclose()


async def list_builds(
    source: str, version: str, *, client: httpx.AsyncClient | None = None
) -> list[BuildInfo]:
    """Builds proposés pour une version ; vide pour une source sans builds."""
    handler = _source(source)["builds"]
    if handler is None:
        return []
    owned = client is None
    http = client or new_client()
    try:
        return await handler(http, version)
    finally:
        if owned:
            await http.aclose()


async def resolve(
    source: str,
    version: str,
    build: str | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> DownloadTarget:
    """Résout une version (et un build, sinon le plus récent stable) en URL vérifiable."""
    handler = _source(source)["target"]
    owned = client is None
    http = client or new_client()
    try:
        target = await handler(http, version, build)
    finally:
        if owned:
            await http.aclose()
    _check_host(target.url)
    return target
