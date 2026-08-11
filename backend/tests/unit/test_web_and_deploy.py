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
        installer = self.INSTALLER.read_text(encoding="utf-8")

        for placeholder in placeholders:
            assert placeholder in installer, f"{placeholder} n'est jamais remplacé par install.sh"

    def test_installer_does_not_leak_the_password(self) -> None:
        """Le mot de passe ne doit apparaître ni en argument ni en variable."""
        installer = self.INSTALLER.read_text(encoding="utf-8")

        assert "MSM_ADMIN_PASSWORD" not in installer
        assert "read -r -s -p" not in installer
