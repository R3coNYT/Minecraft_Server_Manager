"""Tests des sources de téléchargement et de la vérification des fichiers."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from msm.downloads.sources import (
    ALLOWED_HOSTS,
    SOURCES,
    DownloadTarget,
    DownloadUnavailable,
    list_versions,
    resolve,
)
from msm.exceptions import ValidationError
from msm.services.download_service import _download

MOJANG_MANIFEST_BODY = {
    "versions": [
        {"id": "1.21.1", "type": "release", "url": "https://piston-meta.mojang.com/v1/1211.json"},
        {"id": "24w14a", "type": "snapshot", "url": "https://piston-meta.mojang.com/v1/24w.json"},
    ]
}
MOJANG_DETAIL_BODY = {
    "downloads": {
        "server": {
            "url": "https://piston-data.mojang.com/v1/server.jar",
            "sha1": "0" * 40,
            "size": 51_000_000,
        }
    }
}


def transport(routes: dict[str, httpx.Response]) -> httpx.MockTransport:
    """Faux réseau : chaque chemin renvoie la réponse prévue."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url)
        if key not in routes:
            return httpx.Response(404, json={"error": "inattendu"})
        return routes[key]

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
class TestSources:
    async def test_vanilla_versions_are_labelled_by_channel(self) -> None:
        client = httpx.AsyncClient(
            transport=transport(
                {
                    "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json": (
                        httpx.Response(200, json=MOJANG_MANIFEST_BODY)
                    )
                }
            )
        )

        versions = await list_versions("vanilla", client=client)

        assert [(v.id, v.channel) for v in versions] == [
            ("1.21.1", "release"),
            ("24w14a", "snapshot"),
        ]
        await client.aclose()

    async def test_vanilla_resolves_to_a_verifiable_target(self) -> None:
        client = httpx.AsyncClient(
            transport=transport(
                {
                    "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json": (
                        httpx.Response(200, json=MOJANG_MANIFEST_BODY)
                    ),
                    "https://piston-meta.mojang.com/v1/1211.json": httpx.Response(
                        200, json=MOJANG_DETAIL_BODY
                    ),
                }
            )
        )

        target = await resolve("vanilla", "1.21.1", client=client)

        assert target.algorithm == "sha1"
        assert target.checksum == "0" * 40
        await client.aclose()

    async def test_unknown_source_lists_the_available_ones(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            await list_versions("forge")

        assert "vanilla" in (excinfo.value.remediation or "")

    async def test_unreachable_source_is_reported_with_an_action(self) -> None:
        client = httpx.AsyncClient(transport=transport({}))

        with pytest.raises(DownloadUnavailable) as excinfo:
            await list_versions("paper", client=client)

        assert excinfo.value.remediation
        await client.aclose()

    async def test_a_source_pointing_elsewhere_is_refused(self) -> None:
        """Une API compromise ne doit pas pouvoir nous faire télécharger ailleurs."""
        detail = {
            "downloads": {"server": {"url": "https://evil.example/server.jar", "sha1": "0" * 40}}
        }
        client = httpx.AsyncClient(
            transport=transport(
                {
                    "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json": (
                        httpx.Response(200, json=MOJANG_MANIFEST_BODY)
                    ),
                    "https://piston-meta.mojang.com/v1/1211.json": httpx.Response(200, json=detail),
                }
            )
        )

        with pytest.raises(ValidationError) as excinfo:
            await resolve("vanilla", "1.21.1", client=client)

        assert "evil.example" in (excinfo.value.cause or "")
        await client.aclose()

    def test_every_source_declares_a_known_host(self) -> None:
        assert ALLOWED_HOSTS
        assert set(SOURCES) == {"vanilla", "paper", "purpur"}


@pytest.mark.asyncio
class TestDownload:
    def _client(self, body: bytes) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
        )

    async def test_valid_file_is_installed(self, tmp_path: Path) -> None:
        body = b"faux jar" * 100
        target = DownloadTarget(
            url="https://piston-data.mojang.com/server.jar",
            filename="server.jar",
            checksum=hashlib.sha1(body).hexdigest(),
            algorithm="sha1",
        )
        client = self._client(body)

        await _download(target, tmp_path / "server.jar", client=client)

        assert (tmp_path / "server.jar").read_bytes() == body
        # Aucun résidu : le fichier temporaire a été renommé, pas laissé derrière.
        assert not (tmp_path / "server.jar.part").exists()
        await client.aclose()

    async def test_altered_file_is_refused_and_removed(self, tmp_path: Path) -> None:
        """Un JAR au contenu inattendu ne doit jamais être installé."""
        target = DownloadTarget(
            url="https://piston-data.mojang.com/server.jar",
            filename="server.jar",
            checksum=hashlib.sha1("ce que la source annonçait".encode()).hexdigest(),
            algorithm="sha1",
        )
        client = self._client(b"tout autre chose")

        with pytest.raises(ValidationError) as excinfo:
            await _download(target, tmp_path / "server.jar", client=client)

        assert "empreinte" in (excinfo.value.cause or "")
        assert not (tmp_path / "server.jar").exists()
        assert not (tmp_path / "server.jar.part").exists()
        await client.aclose()

    async def test_network_failure_leaves_nothing_behind(self, tmp_path: Path) -> None:
        def failing(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("réseau coupé", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(failing))
        target = DownloadTarget(
            url="https://piston-data.mojang.com/server.jar",
            filename="server.jar",
            checksum="0" * 40,
            algorithm="sha1",
        )

        with pytest.raises(ValidationError) as excinfo:
            await _download(target, tmp_path / "server.jar", client=client)

        assert excinfo.value.remediation
        assert not (tmp_path / "server.jar.part").exists()
        await client.aclose()
