"""Tests de la ligne de commande d'administration.

`install.sh` s'appuie sur ces commandes : leur sortie est un contrat, pas un
affichage. `count-users` doit écrire **un entier et rien d'autre** sur la sortie
standard — un script embarqué le faisait avant, sauf qu'il y mêlait ses lignes de
journalisation, et l'installation redemandait un compte administrateur à chaque
mise à jour.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]


def run_cli(*args: str, env: dict[str, str], expect_success: bool = True):
    """Lance la CLI dans un vrai processus : c'est ainsi que le script l'appelle."""
    result = subprocess.run(
        [sys.executable, "-m", "msm.cli", *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        # La console d'un Windows français n'est pas en UTF-8 : sans cela, les
        # accents rendraient le test dépendant de la locale de la machine.
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if expect_success:
        assert result.returncode == 0, result.stderr
    return result


@pytest.fixture
def cli_env(tmp_path: Path) -> dict[str, str]:
    """Environnement isolé : base temporaire, journalisation en console."""
    import os

    env = {key: value for key, value in os.environ.items() if not key.startswith("MSM_")}
    env.update(
        {
            "MSM_ENVIRONMENT": "test",
            "MSM_SECRET_KEY": "cle-de-test-suffisamment-longue-pour-la-validation-0123456789",
            "MSM_DATABASE_URL": f"sqlite+aiosqlite:///{(tmp_path / 'cli.db').as_posix()}",
            "MSM_DATA_DIR": str(tmp_path / "data"),
            "MSM_LOG_DIR": str(tmp_path / "logs"),
            "MSM_CORS_ORIGINS": "",
            "MSM_SERVER_ROOTS": "",
        }
    )
    return env


class TestCountUsers:
    def test_stdout_holds_only_the_number(self, cli_env: dict[str, str]) -> None:
        """Le contrat dont dépend install.sh."""
        run_cli("migrate", env=cli_env)

        result = run_cli("count-users", env=cli_env)

        assert result.stdout.strip() == "0"
        # Rien d'autre : ni ligne de log, ni bannière.
        assert result.stdout.strip().isdigit()

    def test_logs_go_to_standard_error(self, cli_env: dict[str, str]) -> None:
        """Sinon un appelant qui lit la sortie standard reçoit des horodatages."""
        run_cli("migrate", env=cli_env)

        result = run_cli("count-users", env=cli_env)

        assert "database_engine" in result.stderr
        assert "database_engine" not in result.stdout

    def test_counts_created_accounts(self, cli_env: dict[str, str]) -> None:
        run_cli("migrate", env=cli_env)
        run_cli(
            "createadmin",
            "flavien",
            env={**cli_env, "MSM_ADMIN_PASSWORD": "mot-de-passe-de-test-1234"},
        )

        assert run_cli("count-users", env=cli_env).stdout.strip() == "1"


class TestMigrate:
    def test_migrate_is_idempotent(self, cli_env: dict[str, str]) -> None:
        """`install.sh` la rejoue à chaque mise à jour."""
        run_cli("migrate", env=cli_env)
        result = run_cli("migrate", env=cli_env)

        # Sur le mot seul : l'accent dépendrait de l'encodage de la console.
        assert "Migrations" in result.stdout


class TestSecret:
    def test_secret_prints_a_usable_key(self, cli_env: dict[str, str]) -> None:
        """Elle sert à remplir le .env : elle ne doit rien afficher d'autre."""
        result = run_cli("secret", env=cli_env)

        assert len(result.stdout.strip()) >= 64
        assert "\n" not in result.stdout.strip()
