"""Tests de la barrière anti-traversée de chemin.

C'est le test de sécurité le plus important de la phase 3 : si l'un d'eux
devient rouge, un utilisateur du panneau peut lire ou écrire hors du dossier
de son serveur.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from msm.exceptions import PathTraversalError, UnsafeUploadError
from msm.security.safe_path import is_within, relative_to_root, resolve_within
from msm.security.uploads import check_size, sanitize_filename


@pytest.fixture
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "serveur"
    (directory / "config").mkdir(parents=True)
    (directory / "config" / "mod.toml").write_text("a = 1", encoding="utf-8")
    # Un voisin, pour vérifier qu'un préfixe commun ne suffit pas à passer.
    (tmp_path / "serveur-bis").mkdir()
    (tmp_path / "secret.txt").write_text("données sensibles", encoding="utf-8")
    return directory


class TestTraversal:
    @pytest.mark.parametrize(
        "attack",
        [
            "../secret.txt",
            "../../etc/passwd",
            "config/../../secret.txt",
            "config/../../../etc/shadow",
            "..",
            "./../../secret.txt",
            "config/./../../secret.txt",
        ],
    )
    def test_parent_traversal_is_refused(self, root: Path, attack: str) -> None:
        with pytest.raises(PathTraversalError):
            resolve_within(root, attack)

    @pytest.mark.parametrize(
        "attack",
        ["/etc/passwd", "//serveur/partage/fichier", "C:/Windows/System32/config", "C:\\Windows"],
    )
    def test_absolute_paths_are_refused(self, root: Path, attack: str) -> None:
        with pytest.raises(PathTraversalError):
            resolve_within(root, attack)

    def test_backslash_separator_is_normalised(self, root: Path) -> None:
        """Un client Windows envoie des `\\` : ils ne doivent pas contourner le contrôle."""
        with pytest.raises(PathTraversalError):
            resolve_within(root, "..\\secret.txt")

        assert resolve_within(root, "config\\mod.toml").name == "mod.toml"

    def test_percent_encoded_sequence_is_not_decoded(self, root: Path) -> None:
        """`..%2f..` est un nom de fichier, pas une remontée.

        Le décoder ici reviendrait à décoder deux fois ce que la couche HTTP a
        déjà traité — c'est précisément ainsi que naissent les contournements.
        """
        resolved = resolve_within(root, "config/..%2f..%2fsecret.txt")

        assert resolved.name == "..%2f..%2fsecret.txt"
        assert resolved.parent == (root / "config").resolve()

    def test_null_byte_is_refused(self, root: Path) -> None:
        with pytest.raises(PathTraversalError):
            resolve_within(root, "config/mod.toml\x00.png")

    def test_sibling_directory_sharing_a_prefix_is_refused(self, root: Path) -> None:
        """« serveur-bis » commence par « serveur » sans être dedans."""
        with pytest.raises(PathTraversalError):
            resolve_within(root, "../serveur-bis")

    @pytest.mark.skipif(sys.platform == "win32", reason="liens symboliques restreints sous Windows")
    def test_symlink_escaping_the_root_is_refused(self, root: Path, tmp_path: Path) -> None:
        """Une vérification textuelle laisserait passer ce cas : `resolve()` non."""
        (root / "evasion").symlink_to(tmp_path, target_is_directory=True)

        with pytest.raises(PathTraversalError):
            resolve_within(root, "evasion/secret.txt")

    @pytest.mark.skipif(sys.platform != "win32", reason="système de fichiers insensible à la casse")
    def test_case_variation_cannot_escape(self, root: Path) -> None:
        with pytest.raises(PathTraversalError):
            resolve_within(root, "CONFIG/../../secret.txt")


class TestLegitimatePaths:
    def test_root_itself(self, root: Path) -> None:
        assert resolve_within(root, None) == root.resolve()
        assert resolve_within(root, "") == root.resolve()
        assert resolve_within(root, ".") == root.resolve()

    def test_nested_file(self, root: Path) -> None:
        resolved = resolve_within(root, "config/mod.toml")

        assert resolved == (root / "config" / "mod.toml").resolve()

    def test_file_that_does_not_exist_yet(self, root: Path) -> None:
        """Un téléversement vise un fichier encore absent : ce n'est pas une erreur."""
        assert resolve_within(root, "mods/nouveau.jar").name == "nouveau.jar"

    def test_must_exist_reports_a_missing_file(self, root: Path) -> None:
        with pytest.raises(PathTraversalError) as excinfo:
            resolve_within(root, "config/absent.toml", must_exist=True)

        assert excinfo.value.status_code == 404

    def test_relative_to_root_uses_posix_separators(self, root: Path) -> None:
        """L'API expose des `/` quel que soit le système hôte."""
        target = resolve_within(root, "config/mod.toml")

        assert relative_to_root(root, target) == "config/mod.toml"

    def test_is_within(self, root: Path, tmp_path: Path) -> None:
        assert is_within(root, root / "config")
        assert not is_within(root, tmp_path / "secret.txt")


class TestUploadNames:
    JARS = frozenset({".jar"})

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("mod.jar", "mod.jar"),
            ("../../evil.jar", "evil.jar"),
            ("C:\\Windows\\evil.jar", "evil.jar"),
            ("mon mod.jar", "mon_mod.jar"),
            ("modèle-café.jar", "modele-cafe.jar"),
            ("mod...jar", "mod.jar"),
        ],
    )
    def test_names_are_rebuilt(self, raw: str, expected: str) -> None:
        assert sanitize_filename(raw, allowed_suffixes=self.JARS) == expected

    @pytest.mark.parametrize("raw", ["mod.exe", "mod.sh", "mod", "mod.jar.exe", "archive.zip"])
    def test_unexpected_extensions_are_refused(self, raw: str) -> None:
        with pytest.raises(UnsafeUploadError):
            sanitize_filename(raw, allowed_suffixes=self.JARS)

    @pytest.mark.parametrize("raw", ["CON.jar", "nul.jar", "com1.jar", "LPT9.jar"])
    def test_windows_reserved_names_are_refused(self, raw: str) -> None:
        with pytest.raises(UnsafeUploadError):
            sanitize_filename(raw, allowed_suffixes=self.JARS)

    @pytest.mark.parametrize("raw", ["", "   ", "///", "..."])
    def test_empty_names_are_refused(self, raw: str) -> None:
        with pytest.raises(UnsafeUploadError):
            sanitize_filename(raw, allowed_suffixes=self.JARS)

    def test_long_names_are_shortened_but_keep_their_extension(self) -> None:
        result = sanitize_filename("a" * 300 + ".jar", allowed_suffixes=self.JARS)

        assert result.endswith(".jar")
        assert len(result) <= 120


class TestUploadSize:
    def test_empty_file_is_refused(self) -> None:
        with pytest.raises(UnsafeUploadError, match="vide"):
            check_size(0, maximum=1024)

    def test_oversized_file_is_refused(self) -> None:
        with pytest.raises(UnsafeUploadError) as excinfo:
            check_size(5 * 1024 * 1024, maximum=1024 * 1024)

        assert excinfo.value.remediation and "MSM_UPLOAD_MAX_SIZE_MB" in excinfo.value.remediation

    def test_acceptable_size(self) -> None:
        assert check_size(2048, maximum=4096) == 2048
