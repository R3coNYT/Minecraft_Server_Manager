"""Configuration applicative.

Trois sources, par priorité croissante :

1. valeurs par défaut du code ;
2. ``config.yaml`` (optionnel, pratique pour les réglages non secrets) ;
3. variables d'environnement / ``.env`` préfixées ``MSM_`` (priorité maximale).

Aucun secret n'est codé en dur : ``secret_key`` est obligatoire hors développement.
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from msm.exceptions import ConfigurationError

# Racine du dépôt (…/backend/msm/config.py → …/)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]

LogFormat = Literal["console", "json"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


def _load_yaml_config() -> dict[str, Any]:
    """Charge ``config.yaml`` s'il existe.

    Emplacement : racine du dépôt, ou chemin donné par ``MSM_CONFIG_FILE``.
    """
    candidate = os.environ.get("MSM_CONFIG_FILE")
    path = Path(candidate) if candidate else (PROJECT_ROOT / "config.yaml")
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(
            "Le fichier de configuration est illisible.",
            cause=f"{path} n'est pas un YAML valide : {exc}",
            remediation=(
                "Corriger la syntaxe YAML, ou supprimer le fichier pour revenir aux défauts."
            ),
        ) from exc
    if not isinstance(data, dict):
        raise ConfigurationError(
            "Le fichier de configuration est invalide.",
            cause=f"{path} doit contenir un dictionnaire à la racine.",
            remediation="Utiliser des paires `clé: valeur` au premier niveau du fichier.",
        )
    return {str(k).lower(): v for k, v in data.items()}


class Settings(BaseSettings):
    """Réglages de l'application, validés au démarrage."""

    model_config = SettingsConfigDict(
        env_prefix="MSM_",
        env_file=(PROJECT_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Environnement ----------------------------------------------------
    environment: Literal["development", "production", "test"] = "development"

    # --- Réseau -----------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- Sécurité ---------------------------------------------------------
    secret_key: str = ""
    session_ttl_hours: int = Field(default=168, ge=1, le=24 * 365)
    session_cookie_name: str = "msm_session"
    session_cookie_secure: bool = False
    max_login_attempts: int = Field(default=8, ge=1)
    login_lockout_minutes: int = Field(default=15, ge=1)

    # --- Base de données --------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/msm.db"
    database_echo: bool = False

    # --- Chemins ----------------------------------------------------------
    data_dir: Path = Path("./data")
    log_dir: Path = Path("./logs")
    server_roots: list[Path] = Field(default_factory=list)

    # --- Processus Minecraft ---------------------------------------------
    default_stop_timeout_s: float = Field(default=60.0, gt=0)
    default_kill_timeout_s: float = Field(default=15.0, gt=0)
    default_start_timeout_s: float = Field(default=300.0, gt=0)
    default_log_history_lines: int = Field(default=5000, ge=100, le=200_000)
    stats_interval_s: float = Field(default=2.0, gt=0)
    log_flush_interval_s: float = Field(default=0.1, gt=0)
    log_flush_max_lines: int = Field(default=200, ge=1)

    # --- Uploads ----------------------------------------------------------
    upload_max_size_mb: int = Field(default=256, ge=1)

    # --- Sauvegardes ------------------------------------------------------
    #: Vide = `<data_dir>/backups`. Doit rester **hors** des dossiers de serveur,
    #: faute de quoi chaque sauvegarde emporterait les précédentes.
    backup_dir: Path | None = None
    #: Sauvegardes conservées par serveur ; les plus anciennes sont purgées.
    backup_retention: int = Field(default=10, ge=1, le=1000)
    #: Marge de sécurité exigée sur le disque, en plus de la taille estimée.
    backup_free_space_margin_mb: int = Field(default=512, ge=0)

    # --- Métriques --------------------------------------------------------
    metrics_enabled: bool = True
    #: Un point par serveur et par intervalle ; 30 s = 2 880 points par jour.
    metrics_interval_s: float = Field(default=30.0, ge=5.0, le=3600.0)
    metrics_retention_days: int = Field(default=7, ge=1, le=365)

    # --- Journalisation ---------------------------------------------------
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "console"
    log_retention_days: int = Field(default=30, ge=1)

    # ------------------------------------------------------------------ #
    #  Validateurs
    # ------------------------------------------------------------------ #
    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("server_roots", mode="before")
    @classmethod
    def _split_roots(cls, value: Any) -> Any:
        """Accepte une liste ou une chaîne séparée par le séparateur natif de l'OS."""
        if isinstance(value, str):
            if not value.strip():
                return []
            return [item.strip() for item in value.split(os.pathsep) if item.strip()]
        return value

    @field_validator("data_dir", "log_dir")
    @classmethod
    def _absolutize(cls, value: Path) -> Path:
        return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    @field_validator("backup_dir")
    @classmethod
    def _absolutize_backup_dir(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    @model_validator(mode="after")
    def _absolutize_sqlite_url(self) -> Settings:
        """Ancre un chemin SQLite relatif sur la racine du projet.

        Sans cela, l'emplacement du fichier dépendrait du dossier depuis lequel
        la commande est lancée : les migrations et le service pourraient viser
        deux bases différentes selon qu'on démarre depuis `backend/` ou depuis la
        racine — avec, à l'arrivée, des données introuvables.
        """
        marker = "sqlite+aiosqlite:///"
        url = self.database_url
        if not url.startswith(marker):
            return self

        raw = url[len(marker) :]
        if raw in ("", ":memory:") or raw.startswith(":memory:"):
            return self

        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        object.__setattr__(self, "database_url", f"{marker}{path.as_posix()}")
        return self

    @model_validator(mode="after")
    def _check_secret(self) -> Settings:
        if not self.secret_key:
            if self.environment == "production":
                raise ConfigurationError(
                    "Clé secrète absente.",
                    cause="MSM_SECRET_KEY est vide alors que l'environnement est `production`.",
                    remediation=(
                        'Générer une clé avec `python -c "import secrets; '
                        'print(secrets.token_urlsafe(64))"` puis la placer dans .env'
                    ),
                )
            # Développement et tests : clé éphémère, régénérée à chaque démarrage.
            object.__setattr__(self, "secret_key", secrets.token_urlsafe(64))
        elif len(self.secret_key) < 32:
            raise ConfigurationError(
                "Clé secrète trop courte.",
                cause=f"MSM_SECRET_KEY fait {len(self.secret_key)} caractères, 32 minimum requis.",
                remediation="Régénérer une clé d'au moins 64 caractères aléatoires.",
            )
        return self

    # ------------------------------------------------------------------ #
    #  Utilitaires
    # ------------------------------------------------------------------ #
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def upload_max_size_bytes(self) -> int:
        return self.upload_max_size_mb * 1024 * 1024

    @property
    def msm_log_file(self) -> Path:
        return self.log_dir / "msm.log"

    @property
    def backups_root(self) -> Path:
        """Où sont écrites les archives de sauvegarde."""
        return self.backup_dir or (self.data_dir / "backups")

    def ensure_directories(self) -> None:
        """Crée les répertoires de travail. Appelé une fois au démarrage."""
        for directory in (
            self.data_dir,
            self.log_dir,
            self.data_dir / "cache",
            self.backups_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instance unique des réglages (le cache garantit une seule lecture disque)."""
    return Settings(**_load_yaml_config())


def reset_settings_cache() -> None:
    """Vide le cache — réservé aux tests."""
    get_settings.cache_clear()
