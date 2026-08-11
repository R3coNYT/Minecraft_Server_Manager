"""Fixtures de l'API : application isolée, base temporaire, client authentifié.

Chaque test dispose de sa propre base SQLite dans un dossier temporaire. Aucun
test ne peut donc voir les données d'un autre, ni celles du poste de développement.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from msm.config import Settings
from msm.core.permissions import Role
from msm.db.base import Base
from msm.db.session import get_engine, init_engine, session_scope
from msm.main import create_app
from msm.services.auth_service import AuthService

FAKE_SERVER = Path(__file__).resolve().parents[1] / "fixtures" / "fake_minecraft_server.py"

ADMIN_USERNAME = "flavien"
ADMIN_PASSWORD = "mot-de-passe-de-test-1234"


@pytest.fixture
def api_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        secret_key="cle-de-test-suffisamment-longue-pour-la-validation-0123456789",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        cors_origins=[],
        stats_interval_s=0.2,
        log_flush_interval_s=0.05,
    )


@pytest.fixture
async def app(api_settings: Settings) -> AsyncIterator[FastAPI]:
    """Application prête à l'emploi, schéma créé, comptes initialisés."""
    application = create_app(api_settings)

    # Le schéma doit exister avant le `lifespan`, qui recense les serveurs.
    init_engine(api_settings)
    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_scope() as session:
        auth = AuthService(session, api_settings)
        await auth.create_user(username=ADMIN_USERNAME, password=ADMIN_PASSWORD, role=Role.ADMIN)
        await auth.create_user(username="moderateur", password=ADMIN_PASSWORD, role=Role.MODERATOR)
        await auth.create_user(username="lecteur", password=ADMIN_PASSWORD, role=Role.VIEWER)

    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Client HTTP non authentifié."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


class ApiClient:
    """Client authentifié qui joint automatiquement le jeton anti-CSRF."""

    def __init__(self, http: AsyncClient) -> None:
        self._http = http

    @property
    def raw(self) -> AsyncClient:
        return self._http

    async def login(self, username: str, password: str) -> Any:
        return await self._http.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(extra or {})
        csrf = self._http.cookies.get("msm_csrf")
        if csrf:
            headers.setdefault("X-CSRF-Token", csrf)
        return headers

    async def get(self, url: str, **kwargs: Any) -> Any:
        return await self._http.get(url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> Any:
        kwargs["headers"] = self._headers(kwargs.get("headers"))
        return await self._http.post(url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> Any:
        kwargs["headers"] = self._headers(kwargs.get("headers"))
        return await self._http.put(url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> Any:
        kwargs["headers"] = self._headers(kwargs.get("headers"))
        return await self._http.delete(url, **kwargs)


@pytest.fixture
async def admin(client: AsyncClient) -> ApiClient:
    """Client connecté en tant qu'administrateur."""
    api = ApiClient(client)
    response = await api.login(ADMIN_USERNAME, ADMIN_PASSWORD)
    assert response.status_code == 200, response.text
    return api


@pytest.fixture
async def moderator(app: FastAPI) -> AsyncIterator[ApiClient]:
    """Client connecté en tant que modérateur (droits restreints)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        api = ApiClient(http_client)
        response = await api.login("moderateur", ADMIN_PASSWORD)
        assert response.status_code == 200, response.text
        yield api


@pytest.fixture
async def viewer(app: FastAPI) -> AsyncIterator[ApiClient]:
    """Client connecté en lecture seule."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        api = ApiClient(http_client)
        response = await api.login("lecteur", ADMIN_PASSWORD)
        assert response.status_code == 200, response.text
        yield api


@pytest.fixture
def fake_server_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "minecraft"
    directory.mkdir()
    return directory


def fake_server_payload(name: str, directory: Path, *extra_args: str) -> dict[str, Any]:
    """Charge utile de création d'un serveur branché sur le faux serveur de test."""
    return {
        "name": name,
        "directory": str(directory),
        "launcher_key": "custom",
        "settings": {
            "custom_argv": [sys.executable, str(FAKE_SERVER), *extra_args],
            "stop_timeout_s": 5,
            "kill_timeout_s": 3,
            "start_timeout_s": 30,
        },
    }


@pytest.fixture(autouse=True)
def _cleanup_processes() -> Iterator[None]:
    """Filet de sécurité : aucun processus de test ne doit survivre."""
    import psutil

    before = {p.pid for p in psutil.process_iter()}
    yield
    for process in psutil.process_iter(["pid", "cmdline"]):
        if process.pid in before:
            continue
        try:
            cmdline = process.info.get("cmdline") or []
            if any("fake_minecraft_server.py" in str(part) for part in cmdline):
                process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
