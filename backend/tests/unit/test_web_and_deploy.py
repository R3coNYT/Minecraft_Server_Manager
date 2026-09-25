"""Tests du service de l'interface compilée et du modèle d'unité systemd."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import FileResponse

from msm.config import PROJECT_ROOT
from msm.web import mount_frontend, spa_response


@pytest.fixture
def frontend(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>panneau</html>", encoding="utf-8")
    (root / "assets" / "index.js").write_text("console.log(1)", encoding="utf-8")
    (root / "favicon.ico").write_bytes(b"\x00")
    return root


class TestFrontendMounting:
    def test_absent_frontend_is_not_an_error(self, tmp_path: Path) -> None:
        """En développement, Vite sert l'interface : l'API doit fonctionner seule."""
        app = FastAPI()

        assert mount_frontend(app, tmp_path / "nexiste-pas") is False
        assert spa_response(app, "/servers/1") is None

    def test_mounted_frontend(self, frontend: Path) -> None:
        app = FastAPI()

        assert mount_frontend(app, frontend) is True
        assert app.state.frontend_root == frontend


class TestSpaFallback:
    @pytest.fixture
    def app(self, frontend: Path) -> FastAPI:
        application = FastAPI()
        mount_frontend(application, frontend)
        return application

    @pytest.mark.parametrize(
        "path", ["/", "/servers/3/console", "/audit", "/chemin/inconnu/profond"]
    )
    def test_application_routes_return_the_page(self, app: FastAPI, path: str) -> None:
        """Le routage se fait côté navigateur : ces chemins n'existent pas côté serveur."""
        response = spa_response(app, path)

        assert isinstance(response, FileResponse)
        assert response.path.name == "index.html"

    @pytest.mark.parametrize("path", ["/api/v1/inconnu", "/api", "/ws"])
    def test_api_paths_stay_errors(self, app: FastAPI, path: str) -> None:
        """Un appel d'API mal formé doit recevoir du JSON, jamais du HTML."""
        assert spa_response(app, path) is None

    def test_existing_file_is_served(self, app: FastAPI) -> None:
        response = spa_response(app, "/favicon.ico")

        assert isinstance(response, FileResponse)
        assert response.path.name == "favicon.ico"

    def test_traversal_falls_back_to_the_page(self, app: FastAPI, tmp_path: Path) -> None:
        """Aucun chemin ne doit permettre de servir un fichier hors du dossier compilé."""
        (tmp_path / "secret.txt").write_text("données", encoding="utf-8")

        response = spa_response(app, "/../secret.txt")

        assert isinstance(response, FileResponse)
        assert response.path.name == "index.html"


class TestDeploymentAssets:
    """Le modèle d'unité et l'installateur sont livrés : ils doivent rester cohérents."""

    UNIT = PROJECT_ROOT / "systemd" / "minecraft-server-manager.service"
    INSTALLER = PROJECT_ROOT / "install.sh"

    def test_files_exist(self) -> None:
        assert self.UNIT.is_file()
        assert self.INSTALLER.is_file()

    def test_unit_never_runs_as_root(self) -> None:
        content = self.UNIT.read_text(encoding="utf-8")

        assert "User=__MSM_USER__" in content
        assert "User=root" not in content

    def test_unit_is_hardened(self) -> None:
        content = self.UNIT.read_text(encoding="utf-8")

        for directive in (
            "NoNewPrivileges=yes",
            "ProtectSystem=strict",
            "ProtectHome=yes",
            "PrivateTmp=yes",
            "ReadWritePaths=",
        ):
            assert directive in content, f"Directive de durcissement manquante : {directive}"

    def test_every_placeholder_is_substituted_by_the_installer(self) -> None:
        """Un marqueur oublié produirait une unité systemd invalide."""
        import re

        placeholders = set(re.findall(r"__MSM_[A-Z_]+__", self.UNIT.read_text(encoding="utf-8")))
        renderer = (PROJECT_ROOT / "systemd" / "render-unit.sh").read_text(encoding="utf-8")

        for placeholder in placeholders:
            assert placeholder in renderer, (
                f"{placeholder} n'est jamais remplacé par render-unit.sh"
            )

    def test_install_and_update_share_the_unit_renderer(self) -> None:
        """Deux copies du rendu finiraient par diverger."""
        for script in (self.INSTALLER, PROJECT_ROOT / "update.sh"):
            content = script.read_text(encoding="utf-8")
            assert "render-unit.sh" in content, f"{script.name} ne passe pas par render-unit.sh"
            assert "__MSM_USER__" not in content, f"{script.name} rend l'unité lui-même"

    def test_unit_does_not_kill_minecraft_servers(self) -> None:
        """Les serveurs Minecraft sont dans le groupe de contrôle de MSM.

        Avec `mixed` ou `control-group`, systemd les tuerait (SIGKILL, sans
        sauvegarde du monde) à chaque redémarrage ou mise à jour du panneau.
        """
        assert "KillMode=process" in self.UNIT.read_text(encoding="utf-8")

    def test_msm_command_runs_like_the_service(self) -> None:
        """`sudo msm createadmin` doit viser la vraie base : celle du .env du service.

        Sans la configuration chargée, la ligne de commande retomberait sur une
        base locale au dossier du code — un compte créé là n'existerait pas.
        """
        installer = self.INSTALLER.read_text(encoding="utf-8")

        assert "cat > /usr/local/bin/msm" in installer
        assert "runuser -u ${MSM_USER}" in installer
        assert '_ "${ENV_FILE}"' in installer
        assert "python -m msm.cli createadmin NAME" not in installer

    def test_installer_does_not_leak_the_password(self) -> None:
        """Le mot de passe ne doit apparaître ni en argument ni en variable."""
        installer = self.INSTALLER.read_text(encoding="utf-8")

        assert "MSM_ADMIN_PASSWORD" not in installer
        assert "read -r -s -p" not in installer


class TestSandboxAssets:
    """Isolation des serveurs des comptes : le helper root et son socket."""

    SYSTEMD = PROJECT_ROOT / "systemd"

    def test_socket_is_reserved_to_msm_group(self) -> None:
        content = (self.SYSTEMD / "msm-sandbox.socket").read_text(encoding="utf-8")

        assert "SocketMode=0660" in content
        assert "SocketGroup=__MSM_GROUP__" in content
        assert "Accept=yes" in content

    def test_installer_renders_the_socket_group(self) -> None:
        installer = (PROJECT_ROOT / "install.sh").read_text(encoding="utf-8")

        assert "s|__MSM_GROUP__|${MSM_GROUP}|g" in installer
        assert "msm-sandbox.socket" in installer
        assert "MSM_ISOLATION=" in installer

    def test_request_unit_never_stops_the_server(self) -> None:
        """Le serveur vit dans sa propre unité : la demande qui l'a lancé non plus."""
        content = (self.SYSTEMD / "msm-sandbox@.service").read_text(encoding="utf-8")

        assert "StandardInput=socket" in content
        assert "KillMode=process" in content

    def test_helper_confines_the_server(self) -> None:
        helper = (self.SYSTEMD / "msm-sandbox").read_text(encoding="utf-8")

        for directive in (
            "NoNewPrivileges=yes",
            "ProtectSystem=strict",
            "CapabilityBoundingSet=",
            "MemoryMax=",
            "CPUQuota=",
            "TasksMax=",
            "TemporaryFileSystem=",
            "InaccessiblePaths=",
        ):
            assert directive in helper, f"Protection manquante : {directive}"
        # Le compte est toujours dérivé de l'identifiant validé, jamais fourni tel quel.
        assert 'local user="msm-$account"' in helper
        assert "'^[a-z0-9]{10}$'" in helper

    def test_msm_itself_needs_no_privilege(self) -> None:
        """Ni sudo ni setuid : l'unité de MSM garde NoNewPrivileges."""
        installer = (PROJECT_ROOT / "install.sh").read_text(encoding="utf-8")

        assert "sudoers" not in installer
        assert "NoNewPrivileges=yes" in (
            self.SYSTEMD / "minecraft-server-manager.service"
        ).read_text(encoding="utf-8")
