"""Tests bout en bout des mods, plugins, configurations et server.properties."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.integration.conftest import ApiClient, fake_server_payload

pytestmark = pytest.mark.asyncio


async def _create(admin: ApiClient, directory: Path, name: str = "survie") -> dict:
    response = await admin.post("/api/v1/servers", json=fake_server_payload(name, directory))
    assert response.status_code == 201, response.text
    return response.json()


async def _upload(
    admin: ApiClient, server_id: int, area: str, filename: str, content: bytes, **data: str
):
    csrf = admin.raw.cookies.get("msm_csrf")
    return await admin.raw.post(
        f"/api/v1/servers/{server_id}/files/{area}",
        files={"file": (filename, content, "application/java-archive")},
        data=data,
        headers={"X-CSRF-Token": csrf} if csrf else {},
    )


class TestModsAndPlugins:
    async def test_listing_an_absent_directory_is_empty_not_an_error(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create(admin, fake_server_dir)

        response = await admin.get(f"/api/v1/servers/{server['id']}/files/mods")

        assert response.status_code == 200
        assert response.json() == []

    async def test_upload_then_list(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create(admin, fake_server_dir)

        response = await _upload(admin, server["id"], "mods", "super-mod.jar", b"PK\x03\x04data")

        assert response.status_code == 201, response.text
        assert response.json()["name"] == "super-mod.jar"
        assert response.json()["enabled"] is True

        listing = (await admin.get(f"/api/v1/servers/{server['id']}/files/mods")).json()
        assert [item["name"] for item in listing] == ["super-mod.jar"]
        assert (fake_server_dir / "mods" / "super-mod.jar").is_file()

    async def test_upload_sanitises_the_filename(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        """Un nom hostile est reconstruit, jamais utilisé tel quel."""
        server = await _create(admin, fake_server_dir)

        response = await _upload(admin, server["id"], "mods", "../../../evil mod.jar", b"contenu")

        assert response.status_code == 201
        assert response.json()["name"] == "evil_mod.jar"
        assert (fake_server_dir / "mods" / "evil_mod.jar").is_file()
        assert not (fake_server_dir.parent / "evil mod.jar").exists()

    async def test_upload_refuses_a_non_jar(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create(admin, fake_server_dir)

        response = await _upload(admin, server["id"], "mods", "script.sh", b"#!/bin/sh\nrm -rf /")

        assert response.status_code == 400
        assert response.json()["code"] == "UNSAFE_UPLOAD"
        assert ".jar" in response.json()["remediation"]

    async def test_upload_refuses_an_empty_file(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create(admin, fake_server_dir)

        response = await _upload(admin, server["id"], "mods", "vide.jar", b"")

        assert response.status_code == 400

    async def test_duplicate_upload_needs_confirmation(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create(admin, fake_server_dir)
        await _upload(admin, server["id"], "mods", "mod.jar", b"v1")

        conflict = await _upload(admin, server["id"], "mods", "mod.jar", b"v2")
        assert conflict.status_code == 409

        replaced = await _upload(admin, server["id"], "mods", "mod.jar", b"v2", overwrite="true")
        assert replaced.status_code == 201
        assert (fake_server_dir / "mods" / "mod.jar").read_bytes() == b"v2"

    async def test_disable_renames_instead_of_deleting(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        """Désactiver doit rester réversible sans avoir à retrouver le fichier."""
        server = await _create(admin, fake_server_dir)
        await _upload(admin, server["id"], "mods", "mod.jar", b"contenu")

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/files/mods/mod.jar/toggle",
            json={"enabled": False},
        )

        assert response.status_code == 200
        assert response.json()["enabled"] is False
        assert not (fake_server_dir / "mods" / "mod.jar").exists()
        assert (fake_server_dir / "mods" / "mod.jar.disabled").read_bytes() == b"contenu"

        listing = (await admin.get(f"/api/v1/servers/{server['id']}/files/mods")).json()
        assert listing[0]["name"] == "mod.jar"
        assert listing[0]["enabled"] is False

    async def test_reenable(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create(admin, fake_server_dir)
        await _upload(admin, server["id"], "mods", "mod.jar", b"contenu")
        await admin.post(
            f"/api/v1/servers/{server['id']}/files/mods/mod.jar/toggle", json={"enabled": False}
        )

        response = await admin.post(
            f"/api/v1/servers/{server['id']}/files/mods/mod.jar/toggle", json={"enabled": True}
        )

        assert response.status_code == 200
        assert (fake_server_dir / "mods" / "mod.jar").is_file()

    async def test_delete(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create(admin, fake_server_dir)
        await _upload(admin, server["id"], "mods", "mod.jar", b"contenu")

        response = await admin.delete(f"/api/v1/servers/{server['id']}/files/mods/mod.jar")

        assert response.status_code == 200
        assert not (fake_server_dir / "mods" / "mod.jar").exists()

    async def test_unknown_area_is_reported(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create(admin, fake_server_dir)

        response = await admin.get(f"/api/v1/servers/{server['id']}/files/mondes")

        assert response.status_code == 404
        assert "mods" in response.json()["remediation"]


class TestConfigs:
    async def test_browse_lists_directories_and_editable_files(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        (fake_server_dir / "config").mkdir()
        (fake_server_dir / "config" / "mod.toml").write_text("a = 1", encoding="utf-8")
        (fake_server_dir / "serveur.jar").write_bytes(b"binaire")
        (fake_server_dir / "bukkit.yml").write_text("settings: {}", encoding="utf-8")
        server = await _create(admin, fake_server_dir)

        entries = (await admin.get(f"/api/v1/servers/{server['id']}/configs")).json()

        names = {entry["name"] for entry in entries}
        assert "config" in names
        assert "bukkit.yml" in names
        # Un JAR n'est pas un fichier de configuration : il n'a rien à faire ici.
        assert "serveur.jar" not in names

    async def test_read_and_write_preserve_comments(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        """L'écriture ne passe pas par un sérialiseur : les commentaires survivent."""
        (fake_server_dir / "config").mkdir()
        original = "# Réglages du mod\n[general]\nactive = true\n"
        # Écriture en binaire : le contenu doit traverser l'API à l'octet près,
        # sans conversion de fin de ligne par la plateforme hôte.
        (fake_server_dir / "config" / "mod.toml").write_bytes(original.encode("utf-8"))
        server = await _create(admin, fake_server_dir)

        read = await admin.get(
            f"/api/v1/servers/{server['id']}/configs/file", params={"path": "config/mod.toml"}
        )
        assert read.status_code == 200
        assert read.json()["content"] == original
        assert read.json()["format"] == "toml"

        updated = original.replace("active = true", "active = false")
        write = await admin.put(
            f"/api/v1/servers/{server['id']}/configs/file",
            params={"path": "config/mod.toml"},
            json={"content": updated},
        )

        assert write.status_code == 200
        saved = (fake_server_dir / "config" / "mod.toml").read_bytes().decode("utf-8")
        assert saved == updated
        assert "# Réglages du mod" in saved

    async def test_windows_line_endings_survive_a_round_trip(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        """Un fichier en CRLF ne doit pas être converti par un passage dans l'éditeur."""
        (fake_server_dir / "paper.yml").write_bytes(b"# entete\r\nverbose: false\r\n")
        server = await _create(admin, fake_server_dir)

        read = await admin.get(
            f"/api/v1/servers/{server['id']}/configs/file", params={"path": "paper.yml"}
        )
        assert "\r\n" in read.json()["content"]

        await admin.put(
            f"/api/v1/servers/{server['id']}/configs/file",
            params={"path": "paper.yml"},
            json={"content": read.json()["content"].replace("false", "true")},
        )

        assert (fake_server_dir / "paper.yml").read_bytes() == b"# entete\r\nverbose: true\r\n"

    @pytest.mark.parametrize(
        ("filename", "content", "expected"),
        [
            ("bad.json", '{"a": 1,}', "JSON"),
            ("bad.yml", "a:\n  - b\n c: d", "YAML"),
            ("bad.toml", "a = = 1", "TOML"),
        ],
    )
    async def test_invalid_syntax_is_refused_before_writing(
        self,
        admin: ApiClient,
        fake_server_dir: Path,
        filename: str,
        content: str,
        expected: str,
    ) -> None:
        """Une configuration invalide n'apparaîtrait qu'au prochain démarrage."""
        original = "{}" if filename.endswith(".json") else "a: 1"
        (fake_server_dir / filename).write_text(original, encoding="utf-8")
        server = await _create(admin, fake_server_dir)

        response = await admin.put(
            f"/api/v1/servers/{server['id']}/configs/file",
            params={"path": filename},
            json={"content": content},
        )

        assert response.status_code == 422
        assert expected in response.json()["message"]
        # Le fichier d'origine est intact.
        assert (fake_server_dir / filename).read_text(encoding="utf-8") == original

    async def test_path_traversal_is_refused(
        self, admin: ApiClient, fake_server_dir: Path, tmp_path: Path
    ) -> None:
        secret = tmp_path / "secret.txt"
        secret.write_text("données sensibles", encoding="utf-8")
        server = await _create(admin, fake_server_dir)

        response = await admin.get(
            f"/api/v1/servers/{server['id']}/configs/file", params={"path": "../secret.txt"}
        )

        assert response.status_code == 400
        assert response.json()["code"] == "PATH_TRAVERSAL"

    async def test_binary_file_is_not_editable(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        (fake_server_dir / "monde.dat").write_bytes(b"\x00\x01\x02")
        server = await _create(admin, fake_server_dir)

        response = await admin.get(
            f"/api/v1/servers/{server['id']}/configs/file", params={"path": "monde.dat"}
        )

        assert response.status_code == 422
        assert response.json()["remediation"]


class TestServerProperties:
    PROPERTIES = (
        "#Minecraft server properties\n"
        "#Mon Nov 18 21:04:11 CET 2024\n"
        "motd=Bienvenue\n"
        "server-port=25565\n"
        "difficulty=easy\n"
        "max-players=20\n"
        "pvp=true\n"
    )

    async def test_read_exposes_types(self, admin: ApiClient, fake_server_dir: Path) -> None:
        (fake_server_dir / "server.properties").write_text(self.PROPERTIES, encoding="utf-8")
        server = await _create(admin, fake_server_dir)

        response = await admin.get(f"/api/v1/servers/{server['id']}/properties")

        assert response.status_code == 200
        entries = {entry["key"]: entry for entry in response.json()["entries"]}
        assert entries["max-players"]["type"] == "integer"
        assert entries["pvp"]["type"] == "boolean"
        assert entries["difficulty"]["choices"] == ["peaceful", "easy", "normal", "hard"]
        assert entries["difficulty"]["requires_restart"] is False

    async def test_update_preserves_comments_and_order(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        (fake_server_dir / "server.properties").write_text(self.PROPERTIES, encoding="utf-8")
        server = await _create(admin, fake_server_dir)

        response = await admin.put(
            f"/api/v1/servers/{server['id']}/properties",
            json={"changes": {"max-players": "40", "motd": "Serveur de Flavien"}},
        )

        assert response.status_code == 200
        assert set(response.json()["updated"]) == {"max-players", "motd"}

        content = (fake_server_dir / "server.properties").read_text(encoding="utf-8")
        assert "#Minecraft server properties" in content
        assert "#Mon Nov 18 21:04:11 CET 2024" in content
        assert "max-players=40" in content
        assert "motd=Serveur de Flavien" in content
        # L'ordre d'origine est conservé.
        assert content.index("motd=") < content.index("server-port=")

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("max-players", "beaucoup"),
            ("server-port", "99999"),
            ("difficulty", "impossible"),
            ("pvp", "peut-être"),
            ("view-distance", "1"),
        ],
    )
    async def test_invalid_values_are_refused(
        self, admin: ApiClient, fake_server_dir: Path, key: str, value: str
    ) -> None:
        (fake_server_dir / "server.properties").write_text(self.PROPERTIES, encoding="utf-8")
        server = await _create(admin, fake_server_dir)

        response = await admin.put(
            f"/api/v1/servers/{server['id']}/properties", json={"changes": {key: value}}
        )

        assert response.status_code == 422
        assert response.json()["remediation"]
        assert (fake_server_dir / "server.properties").read_text(
            encoding="utf-8"
        ) == self.PROPERTIES

    async def test_value_with_a_line_break_is_refused(
        self, admin: ApiClient, fake_server_dir: Path
    ) -> None:
        """Un saut de ligne injecterait une clé supplémentaire dans le fichier."""
        (fake_server_dir / "server.properties").write_text(self.PROPERTIES, encoding="utf-8")
        server = await _create(admin, fake_server_dir)

        response = await admin.put(
            f"/api/v1/servers/{server['id']}/properties",
            json={"changes": {"motd": "Bonjour\nonline-mode=false"}},
        )

        assert response.status_code == 422
        assert "online-mode=false" not in (fake_server_dir / "server.properties").read_text(
            encoding="utf-8"
        )

    async def test_unknown_key_is_added(self, admin: ApiClient, fake_server_dir: Path) -> None:
        """Une clé d'une version future de Minecraft doit rester modifiable."""
        (fake_server_dir / "server.properties").write_text(self.PROPERTIES, encoding="utf-8")
        server = await _create(admin, fake_server_dir)

        response = await admin.put(
            f"/api/v1/servers/{server['id']}/properties",
            json={"changes": {"nouvelle-option-2027": "valeur"}},
        )

        assert response.status_code == 200
        assert "nouvelle-option-2027=valeur" in (fake_server_dir / "server.properties").read_text(
            encoding="utf-8"
        )

    async def test_missing_file_is_explained(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create(admin, fake_server_dir)

        read = await admin.get(f"/api/v1/servers/{server['id']}/properties")
        assert read.json()["exists"] is False

        write = await admin.put(
            f"/api/v1/servers/{server['id']}/properties", json={"changes": {"pvp": "false"}}
        )
        assert write.status_code == 422
        assert "Démarrer le serveur" in write.json()["remediation"]


class TestPermissions:
    async def test_moderator_cannot_upload_or_edit(
        self, admin: ApiClient, moderator: ApiClient, fake_server_dir: Path
    ) -> None:
        (fake_server_dir / "config.json").write_text("{}", encoding="utf-8")
        server = await _create(admin, fake_server_dir)

        upload = await _upload(moderator, server["id"], "mods", "mod.jar", b"contenu")
        assert upload.status_code == 403

        write = await moderator.put(
            f"/api/v1/servers/{server['id']}/configs/file",
            params={"path": "config.json"},
            json={"content": '{"a": 1}'},
        )
        assert write.status_code == 403

    async def test_viewer_can_read(
        self, admin: ApiClient, viewer: ApiClient, fake_server_dir: Path
    ) -> None:
        server = await _create(admin, fake_server_dir)

        assert (await viewer.get(f"/api/v1/servers/{server['id']}/files/mods")).status_code == 200
        assert (await viewer.get(f"/api/v1/servers/{server['id']}/configs")).status_code == 200

    async def test_file_actions_are_audited(self, admin: ApiClient, fake_server_dir: Path) -> None:
        server = await _create(admin, fake_server_dir)
        await _upload(admin, server["id"], "mods", "mod.jar", b"contenu")

        entries = (await admin.get("/api/v1/audit", params={"action": "file.uploaded"})).json()[
            "entries"
        ]

        assert entries
        assert entries[0]["payload"]["file"] == "mod.jar"
        assert json.loads(json.dumps(entries[0]["payload"]))["area"] == "mods"
