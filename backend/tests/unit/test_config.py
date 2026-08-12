"""Tests de la configuration lue depuis l'environnement.

Ces tests existent parce qu'ils manquaient : la configuration n'était construite
qu'avec des arguments explicites, jamais depuis des variables d'environnement —
c'est-à-dire jamais comme en production, où systemd charge un fichier `.env`.

Le premier déploiement réel s'est arrêté sur `MSM_CORS_ORIGINS=` : une liste vide,
écrite de la façon la plus naturelle qui soit, que pydantic-settings tentait de
lire comme du JSON.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from msm.config import Settings
from msm.exceptions import ConfigurationError

SECRET = "cle-de-test-suffisamment-longue-pour-passer-la-validation-0123456789"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isole chaque test de l'environnement réel du poste."""
    for name in list(os.environ):
        if name.startswith("MSM_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MSM_SECRET_KEY", SECRET)
    # Le `.env` du poste de développement ne doit pas influencer le test : c'est
    # l'environnement qui est examiné ici, comme sous systemd.
    monkeypatch.setitem(Settings.model_config, "env_file", None)


class TestListsFromEnvironment:
    """Les listes s'écrivent en clair dans un `.env`, pas en JSON."""

    def test_empty_origins_is_an_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Le cas exact qui a fait échouer la première installation."""
        monkeypatch.setenv("MSM_CORS_ORIGINS", "")

        assert Settings().cors_origins == []

    def test_origins_are_separated_by_commas(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MSM_CORS_ORIGINS", "https://a.example, https://b.example")

        assert Settings().cors_origins == ["https://a.example", "https://b.example"]

    def test_a_single_server_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ce qu'écrit install.sh : un chemin, sans guillemets ni crochets."""
        monkeypatch.setenv("MSM_SERVER_ROOTS", "/data/minecraft")

        assert Settings().server_roots == [Path("/data/minecraft")]

    def test_several_roots_use_the_native_separator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MSM_SERVER_ROOTS", os.pathsep.join(["/data/minecraft", "/srv/mc"]))

        assert Settings().server_roots == [Path("/data/minecraft"), Path("/srv/mc")]

    def test_empty_roots_is_an_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MSM_SERVER_ROOTS", "")

        assert Settings().server_roots == []

    def test_defaults_apply_when_nothing_is_set(self) -> None:
        settings = Settings()

        assert settings.cors_origins == ["http://localhost:5173"]
        assert settings.server_roots == []


class TestSecretKey:
    def test_production_requires_a_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MSM_ENVIRONMENT", "production")
        monkeypatch.setenv("MSM_SECRET_KEY", "")

        with pytest.raises(ConfigurationError) as excinfo:
            Settings()

        assert excinfo.value.remediation

    def test_a_short_secret_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MSM_SECRET_KEY", "trop-court")

        with pytest.raises(ConfigurationError):
            Settings()

    def test_development_generates_an_ephemeral_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MSM_SECRET_KEY", "")

        assert len(Settings().secret_key) >= 32


class TestPaths:
    def test_relative_paths_are_anchored_on_the_project(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sinon l'emplacement dépendrait du dossier d'où la commande est lancée."""
        monkeypatch.setenv("MSM_DATA_DIR", "./data")

        assert Settings().data_dir.is_absolute()

    def test_backups_default_under_the_data_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("MSM_DATA_DIR", str(tmp_path))

        assert Settings().backups_root == tmp_path / "backups"

    def test_an_explicit_backup_directory_wins(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("MSM_BACKUP_DIR", str(tmp_path / "ailleurs"))

        assert Settings().backups_root == tmp_path / "ailleurs"


class TestNumericSettings:
    def test_values_are_read_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MSM_PORT", "9000")
        monkeypatch.setenv("MSM_BACKUP_RETENTION", "3")
        monkeypatch.setenv("MSM_SCHEDULER_GRACE_MINUTES", "15")

        settings = Settings()

        assert (settings.port, settings.backup_retention) == (9000, 3)
        assert settings.scheduler_grace_minutes == 15

    def test_an_out_of_range_value_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MSM_PORT", "70000")

        with pytest.raises(Exception):  # noqa: B017 - pydantic lève sa propre erreur
            Settings()
