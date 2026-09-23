"""Échanges avec le serveur de fichiers du launcher.

Trois appels, tous **sortants** — MSM n'a pas besoin d'être joignable depuis
Internet :

* ``GET  <base>/manifest.json``   — ce que contient le modpack ;
* ``GET  <base>/files/<chemin>``  — le contenu d'un fichier ;
* ``PUT  <base>/msm/state``       — les mods désactivés, à répercuter aux joueurs.

Le jeton d'écriture n'est envoyé **qu'à l'adresse configurée** : les
redirections sont refusées sur l'envoi d'état, pour qu'un serveur compromis ne
puisse pas se le faire transmettre ailleurs. Et il n'est jamais envoyé en clair
hors du réseau local.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from msm import __version__
from msm.exceptions import MsmError, ValidationError
from msm.launcher_sync.manifest import MAX_FILE_BYTES

#: Version du contrat, envoyée avec l'état et documentée.
PROTOCOL_VERSION = 1
REQUEST_TIMEOUT_S = 30.0
_CHUNK = 512 * 1024
_USER_AGENT = f"MSM/{__version__} (+launcher-sync)"


class FileServerUnavailable(MsmError):
    """Le serveur de fichiers n'a pas répondu comme prévu."""

    code = "FILE_SERVER_UNAVAILABLE"
    status_code = 502


@dataclass(frozen=True, slots=True)
class ManifestFetch:
    """Résultat d'une lecture du manifest."""

    #: `None` : inchangé depuis la dernière lecture (HTTP 304).
    data: Any | None
    etag: str | None


def _is_local(host: str) -> bool:
    if host in ("localhost",) or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def normalize_base_url(raw: str) -> str:
    """Valide l'adresse du serveur de fichiers et la rend sans `/` final.

    HTTPS est exigé hors du réseau local : le jeton d'écriture voyage avec
    chaque envoi d'état, et en HTTP il circulerait en clair.
    """
    value = (raw or "").strip().rstrip("/")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValidationError(
            "Adresse du serveur de fichiers invalide.",
            cause=f"« {raw} » n'est pas une adresse HTTP ou HTTPS complète.",
            remediation="Indiquer l'adresse de base, par exemple https://frankumc.frankulin.fr",
        )
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ValidationError(
            "Adresse du serveur de fichiers invalide.",
            cause="L'adresse ne doit contenir ni paramètres, ni fragment, ni identifiants.",
            remediation="Indiquer uniquement l'adresse de base du serveur de fichiers.",
        )
    if parts.scheme == "http" and not _is_local(parts.hostname):
        raise ValidationError(
            "HTTPS requis.",
            cause=(
                "Le jeton d'écriture accompagne chaque envoi d'état : en HTTP, il "
                "traverserait Internet en clair."
            ),
            remediation="Utiliser l'adresse en https://, ou une adresse du réseau local.",
        )
    return value


def file_url(base: str, path: str) -> str:
    """Adresse d'un fichier, encodée comme le fait le launcher FrankuMC.

    Chaque segment est encodé séparément puis les `/` sont restaurés : espaces
    et accents dans les noms de mods sont ainsi transmis sans ambiguïté.
    """
    return f"{base}/files/" + "/".join(quote(part, safe="") for part in path.split("/"))


class FileServerClient:
    """Client du serveur de fichiers, injectable pour les tests."""

    def __init__(self, base_url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base = normalize_base_url(base_url)
        self._transport = transport

    @property
    def base_url(self) -> str:
        return self._base

    def _client(self, *, follow_redirects: bool) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_S,
            follow_redirects=follow_redirects,
            transport=self._transport,
            headers={"User-Agent": _USER_AGENT},
        )

    async def fetch_manifest(self, *, etag: str | None = None) -> ManifestFetch:
        """Lit le manifest. Renvoie ``data=None`` s'il n'a pas changé."""
        headers = {"Cache-Control": "no-cache"}
        if etag:
            headers["If-None-Match"] = etag
        try:
            async with self._client(follow_redirects=True) as client:
                response = await client.get(f"{self._base}/manifest.json", headers=headers)
        except httpx.HTTPError as exc:
            raise FileServerUnavailable(
                "Serveur de fichiers injoignable.",
                cause=f"{self._base} n'a pas répondu : {exc}",
                remediation="Vérifier l'adresse et que le serveur de fichiers est en ligne.",
            ) from exc

        if response.status_code == 304:
            return ManifestFetch(data=None, etag=etag)
        if response.status_code != 200:
            raise FileServerUnavailable(
                "Manifest indisponible.",
                cause=f"{self._base}/manifest.json a répondu {response.status_code}.",
                remediation="Vérifier que le manifest a été généré sur le serveur de fichiers.",
            )
        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            # Cas typique : manifest lu pendant sa réécriture, qui n'est pas atomique.
            raise FileServerUnavailable(
                "Manifest illisible.",
                cause=f"Le manifest n'est pas un JSON valide : {exc}",
                remediation=(
                    "Il est peut-être en cours de régénération : la synchronisation "
                    "suivante réessaiera. Rien n'a été modifié."
                ),
            ) from exc
        return ManifestFetch(data=data, etag=response.headers.get("etag"))

    async def download(self, path: str, sha256: str, size: int, destination: Path) -> None:
        """Télécharge un fichier et vérifie son empreinte avant de le garder.

        Un fichier dont l'empreinte ne correspond pas est supprimé : il n'a
        aucune chance d'arriver sur le serveur.
        """
        if destination.is_file() and await asyncio.to_thread(_sha256, destination) == sha256:
            return  # déjà préparé lors d'une tentative précédente

        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".part")
        digest = hashlib.sha256()
        received = 0
        limit = min(size, MAX_FILE_BYTES)

        try:
            async with (
                self._client(follow_redirects=True) as client,
                client.stream("GET", file_url(self._base, path)) as response,
            ):
                if response.status_code != 200:
                    raise FileServerUnavailable(
                        "Fichier indisponible.",
                        cause=f"« {path} » : réponse {response.status_code}.",
                        remediation="Régénérer le manifest : il annonce un fichier absent.",
                    )
                with partial.open("wb") as handle:
                    async for chunk in response.aiter_bytes(_CHUNK):
                        received += len(chunk)
                        if received > limit:
                            raise ValidationError(
                                "Fichier plus gros qu'annoncé.",
                                cause=f"« {path} » dépasse les {size} octets du manifest.",
                                remediation="Régénérer le manifest sur le serveur de fichiers.",
                            )
                        digest.update(chunk)
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            partial.unlink(missing_ok=True)
            raise FileServerUnavailable(
                "Téléchargement interrompu.",
                cause=f"« {path} » : {exc}",
                remediation="La synchronisation suivante reprendra où elle s'est arrêtée.",
            ) from exc
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

        if digest.hexdigest() != sha256:
            partial.unlink(missing_ok=True)
            raise ValidationError(
                "Fichier altéré.",
                cause=f"L'empreinte de « {path} » ne correspond pas à celle du manifest.",
                remediation=(
                    "Le fichier a peut-être changé sans que le manifest soit régénéré. "
                    "Régénérer le manifest, puis relancer la synchronisation."
                ),
            )
        partial.replace(destination)

    async def push_state(
        self, *, token: str, server_name: str, revision: int, disabled: list[str]
    ) -> None:
        """Envoie la liste des mods désactivés. Lève en cas de refus."""
        body = {
            "protocol": PROTOCOL_VERSION,
            "server": server_name,
            "revision": revision,
            "disabledFiles": disabled,
        }
        try:
            # Aucune redirection : le jeton ne doit partir qu'à l'adresse configurée.
            async with self._client(follow_redirects=False) as client:
                response = await client.put(
                    f"{self._base}/msm/state",
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError as exc:
            raise FileServerUnavailable(
                "Envoi de l'état impossible.",
                cause=f"{self._base} n'a pas répondu : {exc}",
                remediation="L'envoi sera retenté à la prochaine synchronisation.",
            ) from exc

        if response.status_code in (401, 403):
            raise ValidationError(
                "Jeton refusé par le serveur de fichiers.",
                cause=f"{self._base}/msm/state a répondu {response.status_code}.",
                remediation=(
                    "Vérifier que le jeton saisi dans MSM est identique à celui "
                    "configuré sur le serveur de fichiers."
                ),
            )
        if response.status_code == 404:
            raise ValidationError(
                "Route d'état absente du serveur de fichiers.",
                cause=f"{self._base}/msm/state n'existe pas.",
                remediation=(
                    "Ajouter la route `PUT /msm/state` au serveur de fichiers — voir "
                    "docs/LAUNCHER_INTEGRATION.md."
                ),
            )
        if not 200 <= response.status_code < 300:
            raise FileServerUnavailable(
                "Envoi de l'état refusé.",
                cause=f"{self._base}/msm/state a répondu {response.status_code}.",
                remediation="L'envoi sera retenté à la prochaine synchronisation.",
            )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
