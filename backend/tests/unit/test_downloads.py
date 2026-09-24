"""Tests des sources de téléchargement et de la vérification des fichiers."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from msm.downloads import sources as sources_module
from msm.downloads.sources import (
    ALLOWED_HOSTS,
    SOURCES,
    DownloadTarget,
    DownloadUnavailable,
    list_builds,
    list_versions,
    neoforge_minecraft_version,
    resolve,
)
from msm.exceptions import ValidationError
from msm.services.download_service import download_file

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

        await download_file(target, tmp_path / "server.jar", client=client)

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
            await download_file(target, tmp_path / "server.jar", client=client)

        assert "checksum" in (excinfo.value.cause or "")
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
            await download_file(target, tmp_path / "server.jar", client=client)

        assert excinfo.value.remediation
        assert not (tmp_path / "server.jar.part").exists()
        await client.aclose()


class TestCatalogue:
    def test_every_source_is_offered(self) -> None:
        assert set(SOURCES) == {
            "vanilla",
            "paper",
            "purpur",
            "fabric",
            "neoforge",
            "mohist",
            "youer",
        }

    def test_every_api_points_to_an_allowed_host(self) -> None:
        """Une source ajoutée sans son hôte échouerait au premier appel."""
        for name in (
            "MOJANG_MANIFEST",
            "PAPER_API",
            "PURPUR_API",
            "FABRIC_META",
            "NEOFORGE_MAVEN",
            "NEOFORGE_VERSIONS",
            "MOHIST_API",
        ):
            host = httpx.URL(getattr(sources_module, name)).host
            assert host in ALLOWED_HOSTS, name

    def test_only_the_neoforge_installer_is_an_installer(self) -> None:
        kinds = {key: source["kind"] for key, source in SOURCES.items()}
        assert [key for key, kind in kinds.items() if kind == "installer"] == ["neoforge"]

    def test_existing_servers_cannot_switch_to_an_installer(self) -> None:
        from msm.services.download_service import DownloadService

        keys = {item["key"] for item in DownloadService.sources()}
        assert "neoforge" not in keys
        assert {"vanilla", "fabric", "mohist", "youer"} <= keys


class TestNeoForgeNumbering:
    @pytest.mark.parametrize(
        ("neoforge", "minecraft"),
        [
            ("21.1.77", "1.21.1"),
            ("21.0.3-beta", "1.21"),
            ("20.4.80-beta", "1.20.4"),
            ("20.2.3-beta", "1.20.2"),
            ("26.3.0.8-beta", "26.3"),
            ("26.1.1.2", "26.1.1"),
            ("0.25w14craftmine.3-beta", None),
        ],
    )
    def test_minecraft_version_is_derived(self, neoforge: str, minecraft: str | None) -> None:
        assert neoforge_minecraft_version(neoforge) == minecraft


NEOFORGE_VERSIONS_BODY = {
    "versions": [
        "20.4.80-beta",
        "21.1.76",
        "21.1.77",
        "21.1.78-beta",
        "26.3.0.8-beta",
        "0.25w14craftmine.3-beta",
    ]
}


@pytest.mark.asyncio
class TestNewSources:
    async def test_neoforge_groups_builds_by_minecraft_version(self) -> None:
        client = httpx.AsyncClient(
            transport=transport(
                {sources_module.NEOFORGE_VERSIONS: httpx.Response(200, json=NEOFORGE_VERSIONS_BODY)}
            )
        )

        versions = await list_versions("neoforge", client=client)
        builds = await list_builds("neoforge", "1.21.1", client=client)

        assert [(item.id, item.channel) for item in versions] == [
            ("26.3", "snapshot"),  # uniquement des bêtas
            ("1.21.1", "release"),
            ("1.20.4", "snapshot"),
        ]
        assert [item.id for item in builds] == ["21.1.78-beta", "21.1.77", "21.1.76"]
        assert builds[0].channel == "beta"
        await client.aclose()

    async def test_neoforge_resolves_to_a_verified_installer(self) -> None:
        installer = f"{sources_module.NEOFORGE_MAVEN}/21.1.77/neoforge-21.1.77-installer.jar"
        client = httpx.AsyncClient(
            transport=transport(
                {
                    sources_module.NEOFORGE_VERSIONS: httpx.Response(
                        200, json=NEOFORGE_VERSIONS_BODY
                    ),
                    f"{installer}.sha256": httpx.Response(200, text="a" * 64 + "  file\n"),
                }
            )
        )

        # Sans build précisé : le plus récent **stable**, pas la bêta.
        target = await resolve("neoforge", "1.21.1", client=client)

        assert target.url == installer
        assert target.kind == "installer"
        assert (target.algorithm, target.checksum) == ("sha256", "a" * 64)
        await client.aclose()

    async def test_fabric_has_no_checksum_but_a_known_host(self) -> None:
        meta = sources_module.FABRIC_META
        client = httpx.AsyncClient(
            transport=transport(
                {
                    f"{meta}/loader/1.21.1": httpx.Response(
                        200,
                        json=[
                            {"loader": {"version": "0.20.0-beta", "stable": False}},
                            {"loader": {"version": "0.19.5", "stable": True}},
                        ],
                    ),
                    f"{meta}/installer": httpx.Response(
                        200, json=[{"version": "1.1.2", "stable": True}]
                    ),
                }
            )
        )

        target = await resolve("fabric", "1.21.1", client=client)

        assert target.url == f"{meta}/loader/1.21.1/0.19.5/1.1.2/server/jar"
        assert target.checksum is None
        assert target.kind == "jar"
        await client.aclose()

    async def test_mohist_builds_are_newest_first_and_verified(self) -> None:
        api = sources_module.MOHIST_API
        body = [
            {"id": 424, "file_sha256": "b" * 64, "build_date": "2025-12-26T10:19:05Z"},
            {"id": 471, "file_sha256": "c" * 64, "build_date": "2026-01-16T13:01:42Z"},
        ]
        client = httpx.AsyncClient(
            transport=transport({f"{api}/mohist/1.20.1/builds": httpx.Response(200, json=body)})
        )

        builds = await list_builds("mohist", "1.20.1", client=client)
        latest = await resolve("mohist", "1.20.1", client=client)
        chosen = await resolve("mohist", "1.20.1", "424", client=client)

        assert [item.id for item in builds] == ["471", "424"]
        assert latest.url == f"{api}/mohist/1.20.1/builds/471/download"
        assert latest.checksum == "c" * 64
        assert chosen.checksum == "b" * 64
        await client.aclose()

    async def test_a_mohist_build_without_checksum_is_refused(self) -> None:
        api = sources_module.MOHIST_API
        client = httpx.AsyncClient(
            transport=transport(
                {f"{api}/youer/1.21.1/builds": httpx.Response(200, json=[{"id": 9}])}
            )
        )

        with pytest.raises(DownloadUnavailable):
            await resolve("youer", "1.21.1", client=client)
        await client.aclose()


@pytest.mark.asyncio
class TestDownloadWithoutChecksum:
    async def test_file_is_installed_and_progress_reported(self, tmp_path: Path) -> None:
        body = b"launcher" * 1000
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
        )
        seen: list[tuple[int, int | None]] = []

        await download_file(
            DownloadTarget(
                url="https://meta.fabricmc.net/x.jar",
                filename="x.jar",
                checksum=None,
                algorithm=None,
            ),
            tmp_path / "x.jar",
            client=client,
            on_progress=lambda written, total: seen.append((written, total)),
        )

        assert (tmp_path / "x.jar").read_bytes() == body
        assert seen[-1] == (len(body), len(body))
        await client.aclose()


@pytest.mark.asyncio
class TestPaperFill:
    """PaperMC a arrêté son API v2 ; l'API « Fill » v3 la remplace."""

    async def test_versions_are_flattened_and_unstable_ones_set_aside(self) -> None:
        body = {"versions": {"1.21": ["1.21.11", "1.21.11-rc3", "1.21.10"], "1.20": ["1.20.6"]}}
        client = httpx.AsyncClient(
            transport=transport({sources_module.PAPER_API: httpx.Response(200, json=body)})
        )

        versions = await list_versions("paper", client=client)

        assert [(item.id, item.channel) for item in versions] == [
            ("1.21.11", "release"),
            ("1.21.11-rc3", "snapshot"),
            ("1.21.10", "release"),
            ("1.20.6", "release"),
        ]
        await client.aclose()

    async def test_the_newest_stable_build_is_chosen_and_verified(self) -> None:
        def build(number: int, channel: str) -> dict:
            return {
                "id": number,
                "channel": channel,
                "downloads": {
                    "server:default": {
                        "name": f"paper-1.21.1-{number}.jar",
                        "checksums": {"sha256": str(number % 10) * 64},
                        "size": 49_000_000,
                        "url": f"https://fill-data.papermc.io/v1/objects/x/paper-1.21.1-{number}.jar",
                    }
                },
            }

        client = httpx.AsyncClient(
            transport=transport(
                {
                    f"{sources_module.PAPER_API}/versions/1.21.1/builds": httpx.Response(
                        200, json=[build(134, "BETA"), build(133, "STABLE"), build(132, "STABLE")]
                    )
                }
            )
        )

        target = await resolve("paper", "1.21.1", client=client)

        assert target.filename == "paper-1.21.1-133.jar"
        assert (target.algorithm, target.checksum) == ("sha256", "3" * 64)
        assert httpx.URL(target.url).host in ALLOWED_HOSTS
        await client.aclose()
