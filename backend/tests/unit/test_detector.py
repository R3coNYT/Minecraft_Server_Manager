"""Tests de la détection de serveurs et des capacités d'un dossier."""

from __future__ import annotations

from pathlib import Path

import pytest

from msm.minecraft.capabilities import detect_capabilities
from msm.minecraft.detector import detect
from msm.minecraft.types import Capability, ServerType

BIG = b"x" * (6 * 1024 * 1024)


@pytest.fixture
def server_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "srv"
    directory.mkdir()
    return directory


class TestJarDetection:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("server.jar", ServerType.VANILLA),
            ("minecraft_server.1.21.1.jar", ServerType.VANILLA),
            ("forge-1.20.1-47.2.0-shim.jar", ServerType.VANILLA),
            ("neoforge-21.1.72.jar", ServerType.NEOFORGE),
            ("mohist-1.20.1-745-server.jar", ServerType.MOHIST),
            ("paper-1.20.1-196.jar", ServerType.PAPER),
            ("purpur-1.20.4-2176.jar", ServerType.PURPUR),
            ("fabric-server-launch.jar", ServerType.FABRIC),
        ],
    )
    def test_type_is_inferred_from_the_jar_name(
        self, server_dir: Path, filename: str, expected: ServerType
    ) -> None:
        if "shim" in filename:
            pytest.skip("les JAR d'installation sont volontairement ignorés")
        (server_dir / filename).write_bytes(BIG)

        assert detect(server_dir).server_type is expected

    def test_installer_jars_are_ignored(self, server_dir: Path) -> None:
        """Un installateur n'est pas un serveur : le proposer mènerait à un échec."""
        (server_dir / "forge-1.20.1-installer.jar").write_bytes(BIG)

        result = detect(server_dir)

        assert result.jars == ()
        assert result.launcher_key is None

    def test_minecraft_version_is_extracted(self, server_dir: Path) -> None:
        (server_dir / "paper-1.20.1-196.jar").write_bytes(BIG)

        assert detect(server_dir).minecraft_version == "1.20.1"

    def test_largest_recognised_jar_wins(self, server_dir: Path) -> None:
        (server_dir / "une-bibliotheque.jar").write_bytes(b"petit")
        (server_dir / "paper-1.20.1-196.jar").write_bytes(BIG)

        result = detect(server_dir)

        assert result.jar_path == "paper-1.20.1-196.jar"
        assert len(result.jars) == 2
        assert result.notes, "La présence de plusieurs JAR doit être signalée"


class TestLauncherSuggestion:
    def test_script_is_suggested_when_no_jar_is_identified(self, server_dir: Path) -> None:
        (server_dir / "run.sh").write_text("#!/bin/bash\n", encoding="utf-8")

        result = detect(server_dir)

        assert result.launcher_key == "shell"
        assert result.script_path == "run.sh"

    def test_batch_script_is_detected(self, server_dir: Path) -> None:
        (server_dir / "run.bat").write_text("@echo off\n", encoding="utf-8")

        result = detect(server_dir)

        assert result.launcher_key == "batch"
        assert result.script_path == "run.bat"

    def test_jar_is_preferred_over_script(self, server_dir: Path) -> None:
        """Le lancement direct donne à MSM la maîtrise complète du processus."""
        (server_dir / "paper-1.20.1-196.jar").write_bytes(BIG)
        (server_dir / "run.sh").write_text("#!/bin/bash\n", encoding="utf-8")

        assert detect(server_dir).launcher_key == "jar"

    def test_empty_directory_is_not_an_error(self, server_dir: Path) -> None:
        result = detect(server_dir)

        assert result.exists is True
        assert result.launcher_key is None
        assert not result.is_configurable
        assert result.notes

    def test_missing_directory_is_reported(self, tmp_path: Path) -> None:
        result = detect(tmp_path / "nexiste-pas")

        assert result.exists is False
        assert result.notes


class TestCapabilities:
    def test_base_capabilities_are_always_present(self, server_dir: Path) -> None:
        capabilities = detect_capabilities(server_dir)

        assert Capability.CONSOLE in capabilities
        assert Capability.PLAYERS in capabilities
        assert Capability.EVENTS in capabilities

    def test_directories_drive_the_capabilities(self, server_dir: Path) -> None:
        """L'interface reflète le contenu du disque, pas une famille déclarée."""
        (server_dir / "mods").mkdir()
        (server_dir / "config").mkdir()
        (server_dir / "server.properties").write_text("", encoding="utf-8")

        capabilities = detect_capabilities(server_dir)

        assert Capability.MODS in capabilities
        assert Capability.CONFIGS in capabilities
        assert Capability.PROPERTIES in capabilities
        assert Capability.PLUGINS not in capabilities

    def test_vanilla_server_with_a_mods_folder_gets_the_mods_tab(self, server_dir: Path) -> None:
        (server_dir / "server.jar").write_bytes(BIG)
        (server_dir / "mods").mkdir()

        result = detect(server_dir)

        assert result.server_type is ServerType.VANILLA
        assert Capability.MODS in result.capabilities

    def test_worlds_and_datapacks(self, server_dir: Path) -> None:
        world = server_dir / "world"
        world.mkdir()
        (world / "level.dat").write_bytes(b"\x00")
        (world / "datapacks").mkdir()

        capabilities = detect_capabilities(server_dir)

        assert Capability.WORLDS in capabilities
        assert Capability.DATAPACKS in capabilities

    def test_hybrid_server_is_flagged(self, server_dir: Path) -> None:
        (server_dir / "mods").mkdir()
        (server_dir / "plugins").mkdir()

        result = detect(server_dir)

        assert any("hybride" in note for note in result.notes)


class TestProperties:
    def test_port_is_read_from_server_properties(self, server_dir: Path) -> None:
        (server_dir / "server.properties").write_text(
            "#Minecraft server properties\nserver-port=25566\nmotd=Bonjour\n", encoding="utf-8"
        )

        assert detect(server_dir).port == 25566

    def test_invalid_port_is_ignored(self, server_dir: Path) -> None:
        (server_dir / "server.properties").write_text("server-port=abc\n", encoding="utf-8")

        assert detect(server_dir).port is None

    def test_eula_status_is_reported(self, server_dir: Path) -> None:
        assert detect(server_dir).eula_accepted is None

        (server_dir / "eula.txt").write_text("eula=false\n", encoding="utf-8")
        assert detect(server_dir).eula_accepted is False

        (server_dir / "eula.txt").write_text("eula=true\n", encoding="utf-8")
        assert detect(server_dir).eula_accepted is True
