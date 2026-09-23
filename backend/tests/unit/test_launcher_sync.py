"""Synchronisation avec le serveur de fichiers d'un launcher : briques pures."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from msm.exceptions import MsmError, ValidationError
from msm.launcher_sync import disk
from msm.launcher_sync.manifest import ManifestEntry, parse_manifest, validate_path
from msm.launcher_sync.plan import (
    LocalFile,
    compute_plan,
    is_mass_deletion,
    plan_from_dict,
    plan_to_dict,
)
from msm.launcher_sync.remote import FileServerClient, file_url, normalize_base_url
from msm.launcher_sync.sides import BOTH, CLIENT, SERVER, detect_side


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _entry(path: str, data: bytes = b"x", **kwargs: object) -> ManifestEntry:
    return ManifestEntry(path=path, sha256=_sha(data), size=len(data), **kwargs)  # type: ignore[arg-type]


def _jar(path: Path, files: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


# --------------------------------------------------------------------------- #
#  Manifest
# --------------------------------------------------------------------------- #
class TestManifest:
    def test_frankumc_manifest_is_read(self) -> None:
        manifest = parse_manifest(
            {
                "packVersion": "1.4.0",
                "mcVersion": "1.21.1",
                "neoforgeVersion": "21.1.77",
                "files": [{"path": "mods/a.jar", "sha256": "a" * 64, "size": 10}],
            }
        )

        assert manifest.pack_version == "1.4.0"
        assert manifest.loader == "neoforge 21.1.77"
        assert manifest.entries[0].path == "mods/a.jar"
        assert manifest.entries[0].disabled is False

    def test_disabled_files_belong_to_the_pack(self) -> None:
        """Sinon MSM supprimerait du serveur le mod qu'il vient de désactiver."""
        manifest = parse_manifest(
            {
                "files": [],
                "disabledFiles": [{"path": "mods/b.jar", "sha256": "b" * 64, "size": 1}],
            }
        )

        assert [(e.path, e.disabled) for e in manifest.entries] == [("mods/b.jar", True)]

    def test_optional_side_is_read(self) -> None:
        manifest = parse_manifest(
            {"files": [{"path": "mods/c.jar", "sha256": "c" * 64, "size": 1, "side": "client"}]}
        )

        assert manifest.entries[0].side == CLIENT

    @pytest.mark.parametrize(
        "path",
        [
            "../evil.jar",
            "mods/../../evil.jar",
            "/etc/passwd",
            "C:/x.jar",
            "mods\\a.jar",
            "",
            "a\0b",
        ],
    )
    def test_dangerous_paths_are_refused(self, path: str) -> None:
        with pytest.raises(ValidationError):
            validate_path(path)

    def test_one_bad_entry_refuses_the_whole_manifest(self) -> None:
        """Appliquer un manifest à moitié valide supprimerait les fichiers écartés."""
        with pytest.raises(ValidationError):
            parse_manifest(
                {
                    "files": [
                        {"path": "mods/a.jar", "sha256": "a" * 64, "size": 1},
                        {"path": "../evil.jar", "sha256": "b" * 64, "size": 1},
                    ]
                }
            )

    def test_bad_hash_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            parse_manifest({"files": [{"path": "mods/a.jar", "sha256": "zz", "size": 1}]})

    def test_duplicates_are_refused_whatever_the_case(self) -> None:
        with pytest.raises(ValidationError):
            parse_manifest(
                {
                    "files": [
                        {"path": "mods/A.jar", "sha256": "a" * 64, "size": 1},
                        {"path": "mods/a.jar", "sha256": "a" * 64, "size": 1},
                    ]
                }
            )

    def test_under_filters_on_synchronized_folders(self) -> None:
        manifest = parse_manifest(
            {
                "files": [
                    {"path": "mods/a.jar", "sha256": "a" * 64, "size": 1},
                    {"path": "config/a.toml", "sha256": "b" * 64, "size": 1},
                    {"path": "modsextra/x.jar", "sha256": "c" * 64, "size": 1},
                ]
            }
        )

        assert [e.path for e in manifest.under(("mods/",))] == ["mods/a.jar"]


# --------------------------------------------------------------------------- #
#  Côté client / serveur
# --------------------------------------------------------------------------- #
class TestSides:
    def test_fabric_client_mod(self, tmp_path: Path) -> None:
        jar = _jar(tmp_path / "a.jar", {"fabric.mod.json": json.dumps({"environment": "client"})})
        assert detect_side(jar) == CLIENT

    def test_fabric_universal_mod(self, tmp_path: Path) -> None:
        jar = _jar(tmp_path / "a.jar", {"fabric.mod.json": json.dumps({"environment": "*"})})
        assert detect_side(jar) == BOTH

    def test_quilt_server_mod(self, tmp_path: Path) -> None:
        meta = {"minecraft": {"environment": "dedicated_server"}}
        jar = _jar(tmp_path / "a.jar", {"quilt.mod.json": json.dumps(meta)})
        assert detect_side(jar) == SERVER

    def test_forge_client_side_only(self, tmp_path: Path) -> None:
        toml = 'modLoader="javafml"\nclientSideOnly=true\n'
        jar = _jar(tmp_path / "a.jar", {"META-INF/mods.toml": toml})
        assert detect_side(jar) == CLIENT

    def test_neoforge_client_dependency(self, tmp_path: Path) -> None:
        toml = (
            'modLoader="javafml"\n'
            '[[dependencies.monmod]]\nmodId="minecraft"\nside="CLIENT"\n'
            '[[dependencies.monmod]]\nmodId="neoforge"\nside="CLIENT"\n'
        )
        jar = _jar(tmp_path / "a.jar", {"META-INF/neoforge.mods.toml": toml})
        assert detect_side(jar) == CLIENT

    def test_forge_mod_needed_on_both_sides(self, tmp_path: Path) -> None:
        toml = '[[dependencies.monmod]]\nmodId="minecraft"\nside="BOTH"\n'
        jar = _jar(tmp_path / "a.jar", {"META-INF/mods.toml": toml})
        assert detect_side(jar) == BOTH

    def test_unreadable_jar_defaults_to_both(self, tmp_path: Path) -> None:
        """Dans le doute, on installe : un mod manquant empêche le serveur de démarrer."""
        jar = tmp_path / "a.jar"
        jar.write_bytes(b"pas une archive")
        assert detect_side(jar) == BOTH


# --------------------------------------------------------------------------- #
#  Plan
# --------------------------------------------------------------------------- #
class TestPlan:
    def test_new_file_is_installed(self) -> None:
        plan = compute_plan([_entry("mods/a.jar", b"a")], {}, {})

        assert [item.path for item in plan.installs] == ["mods/a.jar"]
        assert plan.removes == ()

    def test_identical_untracked_file_is_adopted_not_downloaded(self) -> None:
        local = {"mods/a.jar": LocalFile("mods/a.jar", _sha(b"a"), disabled=False)}

        plan = compute_plan([_entry("mods/a.jar", b"a")], local, {})

        assert plan.installs == ()
        assert plan.adopts == (("mods/a.jar", _sha(b"a")),)

    def test_updated_mod_keeps_its_disabled_state(self) -> None:
        """La désactivation décidée dans MSM l'emporte, même sur une mise à jour."""
        local = {"mods/a.jar": LocalFile("mods/a.jar", _sha(b"v1"), disabled=True)}

        plan = compute_plan([_entry("mods/a.jar", b"v2")], local, {"mods/a.jar": _sha(b"v1")})

        assert plan.installs[0].disabled is True

    def test_new_server_takes_the_published_state(self) -> None:
        plan = compute_plan([_entry("mods/a.jar", b"a", disabled=True)], {}, {})

        assert plan.installs[0].disabled is True

    def test_only_files_installed_by_msm_are_removed(self) -> None:
        """Un mod ajouté à la main n'est jamais touché."""
        local = {
            "mods/old.jar": LocalFile("mods/old.jar", _sha(b"o"), disabled=False),
            "mods/manual.jar": LocalFile("mods/manual.jar", _sha(b"m"), disabled=False),
        }

        plan = compute_plan([], local, {"mods/old.jar": _sha(b"o")})

        assert plan.removes == ("mods/old.jar",)

    def test_mass_deletion_is_detected(self) -> None:
        tracked = {f"mods/{i}.jar": "a" * 64 for i in range(10)}
        plan = compute_plan([], {}, tracked)

        assert is_mass_deletion(plan, len(tracked))

    def test_small_removal_is_not_suspicious(self) -> None:
        tracked = {f"mods/{i}.jar": "a" * 64 for i in range(3)}
        plan = compute_plan([], {}, tracked)

        assert not is_mass_deletion(plan, len(tracked))

    def test_plan_survives_persistence(self) -> None:
        plan = compute_plan([_entry("mods/a.jar", b"a")], {}, {"mods/b.jar": "b" * 64})

        restored = plan_from_dict(json.loads(json.dumps(plan_to_dict(plan))))

        assert restored.installs == plan.installs
        assert restored.removes == plan.removes


# --------------------------------------------------------------------------- #
#  Disque
# --------------------------------------------------------------------------- #
class TestDisk:
    def test_apply_installs_updates_and_removes(self, tmp_path: Path) -> None:
        server = tmp_path / "server"
        staging = tmp_path / "staging"
        (server / "mods").mkdir(parents=True)
        (server / "mods" / "old.jar").write_bytes(b"o")
        (server / "mods" / "upd.jar.disabled").write_bytes(b"v1")
        staging.mkdir()
        for data in (b"new", b"v2"):
            disk.staged_path(staging, _sha(data)).write_bytes(data)

        local = disk.scan_local(server, ("mods/",), {})
        plan = compute_plan(
            [_entry("mods/new.jar", b"new"), _entry("mods/upd.jar", b"v2")],
            local,
            {"mods/old.jar": _sha(b"o"), "mods/upd.jar": _sha(b"v1")},
        )
        disk.apply_plan(server, staging, plan)

        assert (server / "mods" / "new.jar").read_bytes() == b"new"
        # Toujours désactivé, mais à jour.
        assert (server / "mods" / "upd.jar.disabled").read_bytes() == b"v2"
        assert not (server / "mods" / "upd.jar").exists()
        assert not (server / "mods" / "old.jar").exists()

    def test_scan_reports_disabled_files_under_their_manifest_name(self, tmp_path: Path) -> None:
        (tmp_path / "mods").mkdir()
        (tmp_path / "mods" / "a.jar.disabled").write_bytes(b"a")

        local = disk.scan_local(tmp_path, ("mods/",), {})

        assert local["mods/a.jar"].disabled is True
        assert disk.disabled_paths(tmp_path, ("mods/",)) == ["mods/a.jar"]


# --------------------------------------------------------------------------- #
#  Serveur de fichiers
# --------------------------------------------------------------------------- #
class TestRemote:
    def test_http_is_refused_on_the_internet(self) -> None:
        """Le jeton d'écriture circulerait en clair."""
        with pytest.raises(ValidationError):
            normalize_base_url("http://frankumc.frankulin.fr")

    def test_http_is_accepted_on_the_local_network(self) -> None:
        assert normalize_base_url("http://192.168.1.10:8080/") == "http://192.168.1.10:8080"

    def test_file_url_encodes_each_segment_like_the_launcher(self) -> None:
        url = file_url("https://f.test", "mods/Mon Mod+1.jar")
        assert url == "https://f.test/files/mods/Mon%20Mod%2B1.jar"

    async def test_download_verifies_the_hash(self, tmp_path: Path) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"altere"))
        client = FileServerClient("https://f.test", transport=transport)
        destination = tmp_path / "a.jar"

        with pytest.raises(MsmError):
            await client.download("mods/a.jar", _sha(b"attendu"), 7, destination)

        assert not destination.exists()
        assert not list(tmp_path.iterdir()), "Aucun fichier partiel ne doit rester"

    async def test_push_sends_the_token_and_the_state(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(204)

        client = FileServerClient("https://f.test", transport=httpx.MockTransport(handler))
        await client.push_state(
            token="secret", server_name="survie", revision=3, disabled=["mods/a.jar"]
        )

        assert seen[0].method == "PUT"
        assert seen[0].url.path == "/msm/state"
        assert seen[0].headers["Authorization"] == "Bearer secret"
        assert json.loads(seen[0].content) == {
            "protocol": 1,
            "server": "survie",
            "revision": 3,
            "disabledFiles": ["mods/a.jar"],
        }

    async def test_push_does_not_follow_redirects(self) -> None:
        """Le jeton ne doit partir nulle part ailleurs qu'à l'adresse configurée."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(302, headers={"Location": "https://ailleurs.test/vol"})

        client = FileServerClient("https://f.test", transport=httpx.MockTransport(handler))
        with pytest.raises(MsmError):
            await client.push_state(token="secret", server_name="s", revision=1, disabled=[])

        assert seen == ["https://f.test/msm/state"]
