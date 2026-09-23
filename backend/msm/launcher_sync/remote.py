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
from msm.i18n import tr
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
            tr("Invalid file server address."),
            cause=tr("“{address}” is not a complete HTTP or HTTPS address.", address=raw),
            remediation=tr("Enter the base address, for example https://files.example.com"),
        )
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ValidationError(
            tr("Invalid file server address."),
            cause=tr("The address must contain no parameters, fragment or credentials."),
            remediation=tr("Enter only the base address of the file server."),
        )
    if parts.scheme == "http" and not _is_local(parts.hostname):
        raise ValidationError(
            tr("HTTPS required."),
            cause=tr(
                "The write token goes with every state push: over HTTP it would cross the "
                "Internet in clear text."
            ),
            remediation=tr("Use the https:// address, or a local network address."),
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
                tr("File server unreachable."),
                cause=tr("{url} did not answer: {error}", url=self._base, error=exc),
                remediation=tr("Check the address and that the file server is online."),
            ) from exc

        if response.status_code == 304:
            return ManifestFetch(data=None, etag=etag)
        if response.status_code != 200:
            raise FileServerUnavailable(
                tr("Manifest unavailable."),
                cause=tr(
                    "{url}/manifest.json answered {status}.",
                    url=self._base,
                    status=response.status_code,
                ),
                remediation=tr("Check that the manifest has been generated on the file server."),
            )
        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            # Cas typique : manifest lu pendant sa réécriture, qui n'est pas atomique.
            raise FileServerUnavailable(
                tr("Unreadable manifest."),
                cause=tr("The manifest is not valid JSON: {error}", error=exc),
                remediation=tr(
                    "It may be being regenerated: the next synchronisation will try again. "
                    "Nothing was changed."
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
                        tr("File unavailable."),
                        cause=tr(
                            "“{path}”: answer {status}.", path=path, status=response.status_code
                        ),
                        remediation=tr("Regenerate the manifest: it lists a missing file."),
                    )
                with partial.open("wb") as handle:
                    async for chunk in response.aiter_bytes(_CHUNK):
                        received += len(chunk)
                        if received > limit:
                            raise ValidationError(
                                tr("File larger than announced."),
                                cause=tr(
                                    "“{path}” exceeds the {size} bytes of the manifest.",
                                    path=path,
                                    size=size,
                                ),
                                remediation=tr("Regenerate the manifest on the file server."),
                            )
                        digest.update(chunk)
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            partial.unlink(missing_ok=True)
            raise FileServerUnavailable(
                tr("Download interrupted."),
                cause=tr("“{path}”: {error}", path=path, error=exc),
                remediation=tr("The next synchronisation will pick up where it stopped."),
            ) from exc
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

        if digest.hexdigest() != sha256:
            partial.unlink(missing_ok=True)
            raise ValidationError(
                tr("Corrupted file."),
                cause=tr("The checksum of “{path}” does not match the manifest.", path=path),
                remediation=tr(
                    "The file may have changed without the manifest being regenerated. "
                    "Regenerate the manifest, then synchronise again."
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
                tr("Cannot push the state."),
                cause=tr("{url} did not answer: {error}", url=self._base, error=exc),
                remediation=tr("It will be sent again at the next synchronisation."),
            ) from exc

        if response.status_code in (401, 403):
            raise ValidationError(
                tr("Token refused by the file server."),
                cause=tr(
                    "{url}/msm/state answered {status}.",
                    url=self._base,
                    status=response.status_code,
                ),
                remediation=tr(
                    "Check that the token entered in MSM is the same as the one configured on "
                    "the file server."
                ),
            )
        if response.status_code == 404:
            raise ValidationError(
                tr("State route missing from the file server."),
                cause=tr("{url}/msm/state does not exist.", url=self._base),
                remediation=tr(
                    "Add the `PUT /msm/state` route to the file server — see "
                    "docs/LAUNCHER_INTEGRATION.md."
                ),
            )
        if not 200 <= response.status_code < 300:
            raise FileServerUnavailable(
                tr("State push refused."),
                cause=tr(
                    "{url}/msm/state answered {status}.",
                    url=self._base,
                    status=response.status_code,
                ),
                remediation=tr("It will be sent again at the next synchronisation."),
            )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
