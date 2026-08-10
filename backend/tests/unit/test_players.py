"""Tests des fichiers de référence des joueurs et de la résolution de skins."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from msm.minecraft.players import json_files
from msm.minecraft.skins import _extract_texture_url, is_expired


@pytest.fixture
def server_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "srv"
    directory.mkdir()
    return directory


def write(directory: Path, name: str, content: object) -> None:
    (directory / name).write_text(json.dumps(content), encoding="utf-8")


class TestJsonFiles:
    def test_missing_files_are_not_an_error(self, server_dir: Path) -> None:
        """Un serveur jamais démarré n'a aucun de ces fichiers."""
        snapshot = json_files.read_all(server_dir)

        assert snapshot.ops == {}
        assert snapshot.banned == {}
        assert not snapshot.whitelist_active
        assert snapshot.uuid_of("Flavien") is None

    def test_reads_ops(self, server_dir: Path) -> None:
        write(
            server_dir,
            "ops.json",
            [{"uuid": "069A79F4-44E9-4726-A5BE-FCA90E38AAF5", "name": "Flavien", "level": 4}],
        )

        snapshot = json_files.read_all(server_dir)

        assert snapshot.is_op("Flavien")
        assert snapshot.ops["flavien"].level == 4
        # Les UUID sont normalisés en minuscules pour la comparaison.
        assert snapshot.uuid_of("Flavien") == "069a79f4-44e9-4726-a5be-fca90e38aaf5"

    def test_op_lookup_is_case_insensitive(self, server_dir: Path) -> None:
        """Minecraft conserve la casse mais ne la distingue pas."""
        write(server_dir, "ops.json", [{"name": "Flavien", "uuid": "abc"}])

        snapshot = json_files.read_all(server_dir)

        assert snapshot.is_op("flavien")
        assert snapshot.is_op("FLAVIEN")

    def test_reads_bans_with_reason(self, server_dir: Path) -> None:
        write(
            server_dir,
            "banned-players.json",
            [{"name": "Tricheur", "reason": "Utilisation de mods interdits", "expires": "forever"}],
        )

        snapshot = json_files.read_all(server_dir)

        assert snapshot.is_banned("Tricheur")
        assert snapshot.banned["tricheur"].reason == "Utilisation de mods interdits"

    def test_whitelist_activity(self, server_dir: Path) -> None:
        assert not json_files.read_all(server_dir).whitelist_active

        write(server_dir, "whitelist.json", [{"name": "Flavien", "uuid": "abc"}])
        snapshot = json_files.read_all(server_dir)

        assert snapshot.whitelist_active
        assert snapshot.is_whitelisted("Flavien")

    def test_usercache_provides_uuid_for_offline_players(self, server_dir: Path) -> None:
        """Seule source d'UUID pour un joueur qui n'est pas connecté."""
        write(
            server_dir,
            "usercache.json",
            [{"name": "Steve", "uuid": "11111111-2222-3333-4444-555555555555"}],
        )

        assert (
            json_files.read_all(server_dir).uuid_of("steve")
            == "11111111-2222-3333-4444-555555555555"
        )

    @pytest.mark.parametrize(
        "content",
        ["", "pas du json", "{}", '{"name": "objet et non tableau"}', "[1, 2, 3]"],
    )
    def test_corrupted_file_never_raises(self, server_dir: Path, content: str) -> None:
        """Un ops.json cassé ne doit pas empêcher d'afficher les joueurs connectés."""
        (server_dir / "ops.json").write_text(content, encoding="utf-8")

        assert json_files.read_all(server_dir).ops == {}

    def test_file_with_a_byte_order_mark_is_readable(self, server_dir: Path) -> None:
        """Un ops.json édité sous Windows porte un BOM que JSON refuse."""
        raw = json.dumps([{"name": "Flavien", "level": 4}])
        (server_dir / "ops.json").write_bytes(b"\xef\xbb\xbf" + raw.encode("utf-8"))

        assert json_files.read_all(server_dir).is_op("Flavien")

    def test_entries_without_name_are_ignored(self, server_dir: Path) -> None:
        write(server_dir, "ops.json", [{"uuid": "abc"}, {"name": "Flavien"}])

        assert list(json_files.read_all(server_dir).ops) == ["flavien"]


class TestSkinExtraction:
    def _profile(self, url: str) -> dict:
        import base64

        payload = json.dumps({"textures": {"SKIN": {"url": url}}})
        return {
            "properties": [
                {"name": "textures", "value": base64.b64encode(payload.encode()).decode()}
            ]
        }

    def test_extracts_official_texture_url(self) -> None:
        url = "https://textures.minecraft.net/texture/abcdef"

        assert _extract_texture_url(self._profile(url)) == url

    def test_refuses_a_foreign_url(self) -> None:
        """Le profil vient d'un tiers : il ne doit pas faire télécharger n'importe quoi."""
        assert _extract_texture_url(self._profile("https://exemple.invalide/charge.bin")) is None

    @pytest.mark.parametrize(
        "profile",
        [None, {}, {"properties": []}, {"properties": [{"name": "autre", "value": "x"}]}],
    )
    def test_missing_textures(self, profile: object) -> None:
        assert _extract_texture_url(profile) is None

    def test_invalid_base64_is_tolerated(self) -> None:
        profile = {"properties": [{"name": "textures", "value": "pas du base64 !!"}]}

        assert _extract_texture_url(profile) is None


class TestSkinCacheExpiry:
    def test_absent_entry_is_expired(self) -> None:
        assert is_expired(None, not_found=False)

    def test_recent_skin_is_fresh(self) -> None:
        assert not is_expired(datetime.now(UTC) - timedelta(hours=2), not_found=False)

    def test_old_skin_expires(self) -> None:
        assert is_expired(datetime.now(UTC) - timedelta(days=2), not_found=False)

    def test_unknown_uuid_is_retried_sooner(self) -> None:
        """Un UUID hors ligne est réessayé plus tôt qu'un skin connu n'est rafraîchi."""
        stamp = datetime.now(UTC) - timedelta(hours=8)

        assert is_expired(stamp, not_found=True)
        assert not is_expired(stamp, not_found=False)
