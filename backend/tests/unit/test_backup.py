"""Tests du contenu, de l'écriture et de la lecture des archives."""

from __future__ import annotations

import io
import json
import tarfile
import time
from pathlib import Path

import pytest

from msm.backup.archive import BackupCancelled, create_archive, extract_archive, read_manifest
from msm.backup.selection import (
    INVENTORY_NAME,
    MANIFEST_NAME,
    build_manifest,
    select_content,
)
from msm.exceptions import ValidationError


@pytest.fixture
def server_dir(tmp_path: Path) -> Path:
    """Un dossier de serveur réaliste : monde, configs, mods et déchets."""
    directory = tmp_path / "survie"
    (directory / "world" / "region").mkdir(parents=True)
    (directory / "world" / "level.dat").write_bytes(b"niveau")
    (directory / "world" / "region" / "r.0.0.mca").write_bytes(b"region" * 100)
    (directory / "world" / "session.lock").write_bytes(b"verrou")

    (directory / "world_nether").mkdir()
    (directory / "world_nether" / "level.dat").write_bytes(b"nether")

    (directory / "server.properties").write_text("level-name=world\n", encoding="utf-8")
    (directory / "ops.json").write_text("[]", encoding="utf-8")

    (directory / "config").mkdir()
    (directory / "config" / "jei.toml").write_text("[general]\n", encoding="utf-8")

    (directory / "mods").mkdir()
    (directory / "mods" / "jei-1.20.1.jar").write_bytes(b"x" * 2048)
    (directory / "mods" / "optifine.jar.disabled").write_bytes(b"y" * 1024)

    (directory / "plugins").mkdir()
    (directory / "plugins" / "EssentialsX.jar").write_bytes(b"z" * 512)
    (directory / "plugins" / "EssentialsX").mkdir()
    (directory / "plugins" / "EssentialsX" / "config.yml").write_text("a: 1", encoding="utf-8")

    # Bruit : rien de tout cela n'a de valeur restaurée.
    (directory / "logs").mkdir()
    (directory / "logs" / "latest.log").write_text("bruit", encoding="utf-8")
    (directory / "crash-reports").mkdir()
    (directory / "crash-reports" / "crash.txt").write_text("bruit", encoding="utf-8")
    return directory


class TestSelection:
    def test_worlds_and_configs_are_taken(self, server_dir: Path) -> None:
        selection = select_content(server_dir)
        names = {entry.arcname for entry in selection.entries}

        assert "world/level.dat" in names
        assert "world/region/r.0.0.mca" in names
        assert "world_nether/level.dat" in names
        assert "server.properties" in names
        assert "config/jei.toml" in names
        assert selection.worlds == ("world", "world_nether")

    def test_mods_and_plugins_are_not_taken(self, server_dir: Path) -> None:
        """Plusieurs gigaoctets re-téléchargeables n'ont pas à voyager."""
        names = {entry.arcname for entry in select_content(server_dir).entries}

        assert not any(name.endswith(".jar") for name in names)
        assert "mods/jei-1.20.1.jar" not in names

    def test_plugin_configurations_are_taken(self, server_dir: Path) -> None:
        """Le JAR se retélécharge ; sa configuration se réécrit à la main."""
        names = {entry.arcname for entry in select_content(server_dir).entries}

        assert "plugins/EssentialsX/config.yml" in names

    def test_noise_is_left_out(self, server_dir: Path) -> None:
        names = {entry.arcname for entry in select_content(server_dir).entries}

        assert not any(name.startswith("logs/") for name in names)
        assert not any(name.startswith("crash-reports/") for name in names)
        assert "world/session.lock" not in names

    def test_symlinks_are_not_followed(self, server_dir: Path, tmp_path: Path) -> None:
        """Un lien vers la racine transformerait la sauvegarde en copie du disque."""
        secret = tmp_path / "ailleurs"
        secret.mkdir()
        (secret / "secret.txt").write_text("mot de passe", encoding="utf-8")
        try:
            (server_dir / "world" / "lien").symlink_to(secret, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("Création de lien symbolique non autorisée sur cette machine.")

        names = {entry.arcname for entry in select_content(server_dir).entries}

        assert not any("secret" in name for name in names)

    def test_installed_plugins_are_inventoried(self, server_dir: Path) -> None:
        selection = select_content(server_dir)

        mods = {mod.name: mod for mod in selection.mods}
        assert mods["jei-1.20.1.jar"].enabled is True
        assert mods["optifine.jar"].enabled is False
        assert {plugin.name for plugin in selection.plugins} == {"EssentialsX.jar"}


class TestManifest:
    def test_manifest_lists_what_is_not_archived(self, server_dir: Path) -> None:
        selection = select_content(server_dir)

        files = build_manifest(
            selection,
            server_name="Survie",
            server_type="FORGE",
            minecraft_version="1.20.1",
            msm_version="2.0.0",
        )
        manifest = json.loads(files[MANIFEST_NAME].decode("utf-8"))

        assert manifest["server"]["name"] == "Survie"
        assert manifest["content"]["worlds"] == ["world", "world_nether"]
        assert {mod["name"] for mod in manifest["mods"]} == {"jei-1.20.1.jar", "optifine.jar"}

    def test_inventory_is_readable_without_tooling(self, server_dir: Path) -> None:
        """Celui qui a tout perdu n'a que l'archive : elle doit se lire seule."""
        files = build_manifest(
            select_content(server_dir),
            server_name="Survie",
            server_type="FORGE",
            minecraft_version="1.20.1",
            msm_version="2.0.0",
        )
        text = files[INVENTORY_NAME].decode("utf-8")

        assert "jei-1.20.1.jar" in text
        assert "[désactivé]" in text


class TestArchive:
    def test_round_trip(self, server_dir: Path, tmp_path: Path) -> None:
        selection = select_content(server_dir)
        destination = tmp_path / "sauvegardes" / "survie.tar.gz"

        result = create_archive(
            selection.entries,
            destination,
            extra_files=build_manifest(
                selection,
                server_name="Survie",
                server_type="VANILLA",
                minecraft_version=None,
                msm_version="2.0.0",
            ),
        )

        assert result.path.is_file()
        assert result.size_bytes > 0

        target = tmp_path / "restaure"
        written = extract_archive(
            destination, target, skip=frozenset({MANIFEST_NAME, INVENTORY_NAME})
        )

        assert written == selection.file_count
        assert (target / "world" / "level.dat").read_bytes() == b"niveau"
        assert (target / "config" / "jei.toml").is_file()
        # Les fichiers descriptifs restent dans l'archive, pas dans le serveur.
        assert not (target / MANIFEST_NAME).exists()

    def test_manifest_is_readable_without_extracting(
        self, server_dir: Path, tmp_path: Path
    ) -> None:
        selection = select_content(server_dir)
        destination = tmp_path / "survie.tar.gz"
        create_archive(
            selection.entries,
            destination,
            extra_files=build_manifest(
                selection,
                server_name="Survie",
                server_type="PAPER",
                minecraft_version="1.21",
                msm_version="2.0.0",
            ),
        )

        manifest = read_manifest(destination)

        assert manifest["server"]["type"] == "PAPER"

    def test_interrupted_archive_never_takes_its_final_name(
        self, server_dir: Path, tmp_path: Path
    ) -> None:
        """Une coupure laisse un déchet identifiable, jamais une fausse sauvegarde."""
        destination = tmp_path / "survie.tar.gz"
        entries = select_content(server_dir).entries

        with pytest.raises(BackupCancelled):
            create_archive(entries, destination, should_stop=lambda: True)

        assert not destination.exists()
        assert not destination.with_name(destination.name + ".part").exists()

    def test_vanished_file_does_not_abort_the_backup(
        self, server_dir: Path, tmp_path: Path
    ) -> None:
        """Un serveur démarré réécrit ses fichiers : certains disparaissent."""
        selection = select_content(server_dir)
        (server_dir / "config" / "jei.toml").unlink()

        result = create_archive(selection.entries, tmp_path / "survie.tar.gz")

        assert result.file_count == selection.file_count - 1

    def test_unreadable_archive_is_reported_clearly(self, tmp_path: Path) -> None:
        fake = tmp_path / "pas-une-archive.tar.gz"
        fake.write_bytes(b"ceci n'est pas une archive")

        with pytest.raises(ValidationError) as excinfo:
            read_manifest(fake)

        assert excinfo.value.remediation


class TestExtractionSafety:
    """Une archive est une entrée non fiable, même déposée par un administrateur."""

    def _malicious(self, path: Path, name: str) -> None:
        with tarfile.open(path, "w:gz") as archive:
            info = tarfile.TarInfo(name)
            info.size = 3
            info.mtime = int(time.time())
            archive.addfile(info, io.BytesIO(b"mal"))

    @pytest.mark.parametrize(
        "name",
        [
            "../evasion.txt",
            "world/../../evasion.txt",
            "/etc/evasion.txt",
        ],
    )
    def test_paths_escaping_the_directory_are_refused(self, tmp_path: Path, name: str) -> None:
        archive = tmp_path / "piege.tar.gz"
        self._malicious(archive, name)
        target = tmp_path / "serveur"

        with pytest.raises(ValidationError):
            extract_archive(archive, target)

        assert not (tmp_path / "evasion.txt").exists()

    def test_symlink_members_are_refused(self, tmp_path: Path) -> None:
        """Un lien vers / permettrait d'écrire n'importe où au coup suivant."""
        archive = tmp_path / "piege.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            info = tarfile.TarInfo("passwd")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            info.mtime = int(time.time())
            tar.addfile(info)

        with pytest.raises(ValidationError):
            extract_archive(archive, tmp_path / "serveur")

    def test_nothing_is_written_when_a_member_is_refused(self, tmp_path: Path) -> None:
        """La validation passe avant toute écriture, pas au fil de l'extraction."""
        archive = tmp_path / "piege.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for name in ("world/level.dat", "../evasion.txt"):
                info = tarfile.TarInfo(name)
                info.size = 3
                info.mtime = int(time.time())
                tar.addfile(info, io.BytesIO(b"mal"))

        target = tmp_path / "serveur"
        with pytest.raises(ValidationError):
            extract_archive(archive, target)

        assert not (target / "world" / "level.dat").exists()
